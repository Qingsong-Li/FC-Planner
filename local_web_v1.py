#!/usr/bin/env python3
import cgi
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
WS = ROOT / "FC-Planner"
HCP_DIR = WS / "src" / "hierarchical_coverage_planner"
DATA_DIR = HCP_DIR / "data"
LAUNCH_DIR = HCP_DIR / "launch"
JOBS_DIR = ROOT / ".local_web_jobs"
JOBS_DIR.mkdir(exist_ok=True)
# 是否启用旧版 FOV 渲染流水线（render_in_traj.py）。
ENABLE_BLENDER_PIPELINE = False
# 是否启用第三视角可视化流水线（render_third_person.py）。
ENABLE_THIRD_PERSON_PIPELINE = True
# 第三视角是否仅渲染预览图（单帧 preview.png）。True: 调机位快；False: 渲染完整视频。
THIRD_PERSON_PREVIEW_ONLY = False
# 第三视角最大渲染帧数上限。0 表示不截断；>0 表示最多渲染前 N 帧。
THIRD_PERSON_MAX_FRAMES = 0
# 第三视角全程重采样目标帧数（非截断）。
# 例如原始 900 帧可均匀压缩到 600 帧，仍覆盖完整轨迹，但视频更短。
THIRD_PERSON_TARGET_FRAMES = 600


HTML_INDEX = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>FC-Planner V1 Local Runner</title>
  <style>
    body { font-family: sans-serif; margin: 24px; max-width: 980px; }
    .card { border: 1px solid #ddd; border-radius: 10px; padding: 16px; margin-bottom: 16px; }
    .row { display: grid; grid-template-columns: 180px 1fr; gap: 12px; align-items: center; margin: 8px 0; }
    input, button, textarea { padding: 8px; }
    .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    pre { background: #111; color: #ddd; padding: 10px; overflow: auto; border-radius: 8px; max-height: 360px; }
    .ok { color: #0a7f2e; }
    .err { color: #c62828; }
    video { max-width: 100%; border: 1px solid #ddd; border-radius: 8px; }
  </style>
</head>
<body>
  <h2>FC-Planner 一键本地演示（V1）</h2>
  <div class="card">
    <form id="jobForm" enctype="multipart/form-data">
      <div class="row"><label>OBJ 文件</label><input type="file" name="obj_file" accept=".obj" required /></div>
      <div class="row"><label>MTL/材质文件</label><input type="file" name="asset_files" multiple /></div>
      <div class="row"><label>材质文件夹</label><input type="file" name="asset_dir_files" webkitdirectory directory multiple /></div>
      <div class="row"><label>场景名(scene)</label><input name="scene_name" value="webscene" required /></div>
      <div class="grid2">
        <div class="row"><label>fov_h</label><input name="fov_h" value="55.0" /></div>
        <div class="row"><label>fov_w</label><input name="fov_w" value="75.0" /></div>
        <div class="row"><label>cx</label><input name="cx" value="320.0" /></div>
        <div class="row"><label>cy</label><input name="cy" value="240.0" /></div>
        <div class="row"><label>fx</label><input name="fx" value="417.0467" /></div>
        <div class="row"><label>fy</label><input name="fy" value="461.0951" /></div>
        <div class="row"><label>max_dist</label><input name="max_dist" value="11.0" /></div>
        <div class="row"><label>resolution</label><input name="resolution_" value="0.4" /></div>
        <div class="row"><label>max_vel</label><input name="max_vel" value="5.0" /></div>
        <div class="row"><label>max_acc</label><input name="max_acc" value="1.0" /></div>
        <div class="row"><label>max_jerk</label><input name="max_jerk" value="0.5" /></div>
        <div class="row"><label>max_yd(deg/s)</label><input name="max_yd" value="60.0" /></div>
        <div class="row"><label>max_ydd(deg/s²)</label><input name="max_ydd" value="30.0" /></div>
        <div class="row"><label>drone_radius</label><input name="drone_radius" value="0.3" /></div>
        <div class="row"><label>start_x</label><input name="start_x" value="0.0" /></div>
        <div class="row"><label>start_y</label><input name="start_y" value="20.0" /></div>
        <div class="row"><label>start_z</label><input name="start_z" value="-13.0" /></div>
        <div class="row"><label>viewpoint_dist</label><input name="viewpoint_dist" value="8.0" /></div>
        <div class="row"><label>safe_radius</label><input name="safe_radius" value="3.0" /></div>
      </div>
      <div class="row"><label>目标端口</label><input name="port" value="8008" /></div>
      <div class="row"><label></label><button type="submit">一键开始</button></div>
    </form>
  </div>

  <div class="card">
    <div id="status">等待任务...</div>
    <pre id="logs"></pre>
    <div id="artifacts"></div>
  </div>

  <script>
    let jobId = null;
    let pollTimer = null;

    async function pollStatus() {
      if (!jobId) return;
      const res = await fetch(`/api/status?job_id=${jobId}`);
      const data = await res.json();
      document.getElementById('status').innerHTML =
        `任务: <b>${data.job_id}</b> | 状态: <b>${data.status}</b> | 阶段: ${data.phase || '-'}`;
      document.getElementById('logs').textContent = (data.logs || []).join('\\n');
      if (data.status === 'done' || data.status === 'failed') {
        clearInterval(pollTimer);
        const a = document.getElementById('artifacts');
        a.innerHTML = '';
        if (data.status === 'done') {
          let html = '<p class="ok">任务完成</p>';
          if (data.fov_video_url) {
            html += `<h4>FOV 视频</h4><video controls src="${data.fov_video_url}"></video>`;
          } else if (data.fov_frames_url) {
            html += `<h4>FOV 帧目录</h4><a href="${data.fov_frames_url}" target="_blank">${data.fov_frames_url}</a>`;
          }
          if (data.third_person_video_url) {
            html += `<h4>第三视角回放</h4><video controls src="${data.third_person_video_url}"></video>`;
          }
          if (data.third_person_preview_url) {
            html += `<h4>第三视角预览</h4><img style="max-width:100%;border:1px solid #ddd;border-radius:8px" src="${data.third_person_preview_url}" />`;
          }
          if (data.traj_url) {
            html += `<h4>轨迹文件</h4><a href="${data.traj_url}" target="_blank">${data.traj_url}</a>`;
          }
          a.innerHTML = html;
        } else {
          a.innerHTML = `<p class="err">任务失败：${data.error || 'unknown error'}</p>`;
        }
      }
    }

    document.getElementById('jobForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      const form = document.getElementById('jobForm');
      const fd = new FormData(form);
      const res = await fetch('/api/start', { method: 'POST', body: fd });
      const data = await res.json();
      if (!data.ok) {
        alert(data.error || '启动失败');
        return;
      }
      jobId = data.job_id;
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(pollStatus, 1500);
      pollStatus();
    });
  </script>
</body>
</html>
"""


def run_cmd(cmd: str, logs: List[str], cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None) -> None:
    logs.append(f"$ {cmd}")
    p = subprocess.Popen(
        ["bash", "-lc", cmd],
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert p.stdout is not None
    for line in p.stdout:
        logs.append(line.rstrip())
    code = p.wait()
    if code != 0:
        raise RuntimeError(f"Command failed({code}): {cmd}")


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


@dataclass
class Job:
    job_id: str
    status: str = "queued"
    phase: str = ""
    logs: List[str] = field(default_factory=list)
    error: str = ""
    fov_video_url: str = ""
    third_person_video_url: str = ""
    third_person_preview_url: str = ""
    fov_frames_url: str = ""
    traj_url: str = ""


JOBS: Dict[str, Job] = {}
LOCK = threading.Lock()


def make_launch_from_eiffel(
    form: Dict[str, str],
    launch_out: Path,
    mesh_path: Path,
    pcd_path: Path,
    fullcloud_path: Path,
    traj_path: Path,
    cloud_path: Path,
    pos_path: Path,
    pitch_path: Path,
    yaw_path: Path,
) -> None:
    tpl = (LAUNCH_DIR / "eiffeltower.launch").read_text(encoding="utf-8")

    def rp(old: str, new: str) -> None:
        nonlocal tpl
        tpl = tpl.replace(old, new)

    # Keep all defaults aligned with eiffeltower.launch except paths and user-adjustable fields.
    rp('name="fov_h" value="55.0"', f'name="fov_h" value="{form["fov_h"]}"')
    rp('name="fov_w" value="75.0"', f'name="fov_w" value="{form["fov_w"]}"')
    rp('name="cx" value="320.0"', f'name="cx" value="{form["cx"]}"')
    rp('name="cy" value="240.0"', f'name="cy" value="{form["cy"]}"')
    rp('name="fx" value="417.0467"', f'name="fx" value="{form["fx"]}"')
    rp('name="fy" value="461.0951"', f'name="fy" value="{form["fy"]}"')
    rp('name="max_dist" value="11.0"', f'name="max_dist" value="{form["max_dist"]}"')
    rp('name="resolution_" value="0.4"', f'name="resolution_" value="{form["resolution_"]}"')
    rp('name="max_vel" value="5.0"', f'name="max_vel" value="{form["max_vel"]}"')
    rp('name="max_acc" value="1.0"', f'name="max_acc" value="{form["max_acc"]}"')
    rp('name="max_jerk" value="0.5"', f'name="max_jerk" value="{form["max_jerk"]}"')
    rp('name="max_yd" value="60.0"', f'name="max_yd" value="{form["max_yd"]}"')
    rp('name="max_ydd" value="30.0"', f'name="max_ydd" value="{form["max_ydd"]}"')
    rp('name="drone_radius" value="0.3"', f'name="drone_radius" value="{form["drone_radius"]}"')
    rp('name="hcplanner/current_x_" value="0.0"', f'name="hcplanner/current_x_" value="{form["start_x"]}"')
    rp('name="hcplanner/current_y_" value="20.0"', f'name="hcplanner/current_y_" value="{form["start_y"]}"')
    rp('name="hcplanner/current_z_" value="-13.0"', f'name="hcplanner/current_z_" value="{form["start_z"]}"')
    rp('name="viewpoint_manager/viewpoints_distance" value="8.0"', f'name="viewpoint_manager/viewpoints_distance" value="{form["viewpoint_dist"]}"')
    rp('name="viewpoint_manager/safe_radius" value="3.0"', f'name="viewpoint_manager/safe_radius" value="{form["safe_radius"]}"')

    # Route all data IO to local job directory, not FC-Planner workspace.
    rp('value="$(find hierarchical_coverage_planner)/data/EiffelTower.pcd"', f'value="{pcd_path}"')
    rp('value="$(find hierarchical_coverage_planner)/data/mesh/EiffelTower.obj"', f'value="{mesh_path}"')
    rp('value="$(find hierarchical_coverage_planner)/data/EiffelTowermore.pcd"', f'value="{fullcloud_path}"')
    rp('value="$(find hierarchical_coverage_planner)/solution/Traj/TrajInfoEiffelTower.txt"', f'value="{traj_path}"')
    rp('value="$(find hierarchical_coverage_planner)/solution/Traj/CloudInfoEiffelTower.txt"', f'value="{cloud_path}"')
    rp('value="$(find hierarchical_coverage_planner)/solution/Traj/PositionCoeffEiffelTower.txt"', f'value="{pos_path}"')
    rp('value="$(find hierarchical_coverage_planner)/solution/Traj/PitchCoeffEiffelTower.txt"', f'value="{pitch_path}"')
    rp('value="$(find hierarchical_coverage_planner)/solution/Traj/YawCoeffEiffelTower.txt"', f'value="{yaw_path}"')

    launch_out.write_text(tpl, encoding="utf-8")


def _safe_rel_path(p: str) -> Path:
    rel = Path(p.replace("\\", "/"))
    cleaned = Path(*[x for x in rel.parts if x not in ("", ".", "..")])
    if str(cleaned) == "":
        raise RuntimeError("empty relative path for uploaded asset")
    return cleaned


def run_job(job: Job, form: Dict[str, str], obj_uploaded_path: Path, asset_uploaded_paths: List[Path]) -> None:
    scene_proc = None
    ros_env = os.environ.copy()
    # Avoid ROS1 Noetic EOL popup blocking RViz in headless recording.
    ros_env["DISABLE_ROS1_EOL_WARNINGS"] = "1"
    try:
        job.status = "running"
        scene = form["scene_name"].strip()
        if not scene:
            raise RuntimeError("scene_name is empty")
        safe_scene = "".join(ch for ch in scene if ch.isalnum() or ch in ("_", "-")).strip("_-")
        if not safe_scene:
            raise RuntimeError("scene_name only has invalid chars")
        scene = safe_scene

        job_dir = JOBS_DIR / job.job_id
        render_dir = job_dir / "render_frames"
        render_dir.mkdir(parents=True, exist_ok=True)

        job.phase = "prepare-files"
        mesh_dst = job_dir / f"{scene}.obj"
        pcd_dst = job_dir / f"{scene}.pcd"
        fullcloud_dst = job_dir / f"{scene}more.pcd"
        traj_path = job_dir / f"TrajInfo_{scene}.txt"
        cloud_path = job_dir / f"CloudInfo_{scene}.txt"
        pos_path = job_dir / f"PositionCoeff_{scene}.txt"
        pitch_path = job_dir / f"PitchCoeff_{scene}.txt"
        yaw_path = job_dir / f"YawCoeff_{scene}.txt"
        shutil.copy2(obj_uploaded_path, mesh_dst)
        job.logs.append(f"[INFO] OBJ copied: {mesh_dst}")
        if asset_uploaded_paths:
            copied = 0
            for src in asset_uploaded_paths:
                if src.exists():
                    if src.resolve() == mesh_dst.resolve():
                        continue
                    dst = job_dir / src.name
                    if src.resolve() != dst.resolve():
                        shutil.copy2(src, dst)
                    copied += 1
            job.logs.append(f"[INFO] Asset files copied: {copied}")
        else:
            job.logs.append("[WARN] No MTL/texture assets uploaded; rendered material may be incomplete.")

        job.phase = "obj-to-pcd"
        run_cmd(
            "python3 obj_to_pcd.py "
            f"--obj '{mesh_dst}' "
            f"--out-prefix '{scene}' "
            f"--out-dir '{job_dir}' "
            "--full-points 500000 --rosa-voxel 0.05 --rosa-max-points 120000",
            job.logs,
            cwd=ROOT,
        )
        src_rosa = job_dir / f"{scene}_rosa.pcd"
        src_full = job_dir / f"{scene}_fullcloud.pcd"
        shutil.move(str(src_rosa), str(pcd_dst))
        shutil.move(str(src_full), str(fullcloud_dst))
        job.logs.append(f"[INFO] PCD ready: {pcd_dst}, {fullcloud_dst}")

        job.phase = "build-launch"
        launch_file = job_dir / f"web_{scene}.launch"
        make_launch_from_eiffel(
            form=form,
            launch_out=launch_file,
            mesh_path=mesh_dst,
            pcd_path=pcd_dst,
            fullcloud_path=fullcloud_dst,
            traj_path=traj_path,
            cloud_path=cloud_path,
            pos_path=pos_path,
            pitch_path=pitch_path,
            yaw_path=yaw_path,
        )
        job.logs.append(f"[INFO] Launch generated: {launch_file}")

        job.phase = "ros-launch"
        scene_log = job_dir / "scene.log"
        scene_cmd = (
            f"cd '{WS}' && source devel/setup.bash && "
            f"roslaunch '{launch_file}'"
        )
        scene_out = scene_log.open("w", encoding="utf-8")
        scene_proc = subprocess.Popen(["bash", "-lc", scene_cmd], stdout=scene_out, stderr=scene_out, env=ros_env)
        job.logs.append("[INFO] Scene launch started, waiting for nodes...")
        time.sleep(10.0)

        job.phase = "trigger-planning"
        trigger_goal = (
            "source devel/setup.bash && "
            "rostopic pub -1 /move_base_simple/goal geometry_msgs/PoseStamped "
            "\"{header: {frame_id: 'world'}, pose: {position: {x: 0.0, y: 0.0, z: 0.0}, "
            "orientation: {x: 0.0, y: 0.0, z: 0.0, w: 1.0}}}\""
        )
        run_cmd(f"cd '{WS}' && {trigger_goal}", job.logs, env=ros_env)
        job.logs.append("[INFO] Planning trigger sent (/move_base_simple/goal).")

        timeout_s = 1800
        t0 = time.time()
        job.phase = "wait-traj"
        while True:
            if traj_path.exists() and traj_path.stat().st_size > 0:
                break
            if time.time() - t0 > timeout_s:
                raise RuntimeError(f"Trajectory output timeout: {traj_path}")
            time.sleep(2.0)
        job.logs.append(f"[INFO] Traj generated: {traj_path}")

        job.phase = "wait-cloud"
        t1 = time.time()
        while True:
            if cloud_path.exists() and cloud_path.stat().st_size > 0:
                break
            if time.time() - t1 > timeout_s:
                raise RuntimeError(f"Cloud output timeout: {cloud_path}")
            time.sleep(1.0)
        job.logs.append(f"[INFO] Cloud generated: {cloud_path}")

        if ENABLE_THIRD_PERSON_PIPELINE:
            job.phase = "third-person-render"
            if not command_exists("blender"):
                raise RuntimeError("blender not found in PATH.")
            third_frames = job_dir / "third_person_frames"
            third_frames.mkdir(parents=True, exist_ok=True)
            third_video = job_dir / "third_person.mp4"
            drone_model = WS / "src" / "traj_utils" / "f250.dae"

            third_cmd = (
                f"blender --background --python '{WS / 'vis_tool' / 'render_third_person.py'}' -- "
                f"--scene_model_path '{mesh_dst}' "
                f"--drone_model_path '{drone_model}' "
                f"--traj_path '{traj_path}' "
                f"--cloud_path '{cloud_path}' "
                f"--renderout_path '{third_frames}' "
                f"--fps 20 --sample_step 5 --max_frames {THIRD_PERSON_MAX_FRAMES} --target_frames {THIRD_PERSON_TARGET_FRAMES} "
                + ("--preview_only" if THIRD_PERSON_PREVIEW_ONLY else "")
            )
            run_cmd(third_cmd, job.logs)

            if THIRD_PERSON_PREVIEW_ONLY:
                preview_png = third_frames / "preview.png"
                if preview_png.exists() and preview_png.stat().st_size > 0:
                    job.third_person_preview_url = f"/artifacts/{job.job_id}/third_person_frames/preview.png"
                else:
                    job.logs.append("[WARN] Third-person preview not generated.")
            else:
                if command_exists("ffmpeg"):
                    job.phase = "third-person-encode"
                    third_encode_cmd = (
                        f"ffmpeg -y -framerate 20 -pattern_type glob -i '{third_frames}/*.png' "
                        f"-c:v libx264 -pix_fmt yuv420p '{third_video}'"
                    )
                    run_cmd(third_encode_cmd, job.logs)
                else:
                    job.phase = "third-person-encode-blender"
                    third_encode_cmd = (
                        f"blender --background --python '{WS / 'vis_tool' / 'frames_to_video.py'}' -- "
                        f"--frames_dir '{third_frames}' --output '{third_video}' --fps 20"
                    )
                    run_cmd(third_encode_cmd, job.logs)

                if third_video.exists() and third_video.stat().st_size > 0:
                    job.third_person_video_url = f"/artifacts/{job.job_id}/third_person.mp4"
                else:
                    job.logs.append("[WARN] Third-person video not generated.")
        else:
            job.logs.append("[INFO] Third-person blender replay disabled.")

        if ENABLE_BLENDER_PIPELINE:
            job.phase = "blender-render"
            if not command_exists("blender"):
                raise RuntimeError("blender not found in PATH.")

            render_cmd = (
                f"blender --background --python '{WS / 'vis_tool' / 'render_in_traj.py'}' -- "
                f"--model_path '{mesh_dst}' "
                f"--traj_path '{traj_path}' "
                f"--renderout_path '{render_dir}' "
                "--backend BLENDER_EEVEE --fast_mode"
            )
            run_cmd(render_cmd, job.logs)

            fov_video = job_dir / "fov.mp4"
            if command_exists("ffmpeg"):
                job.phase = "fov-encode"
                encode_cmd = (
                    f"ffmpeg -y -framerate 20 -i '{render_dir}/%04d.png' "
                    f"-c:v libx264 -pix_fmt yuv420p '{fov_video}'"
                )
                run_cmd(encode_cmd, job.logs)
                job.fov_video_url = f"/artifacts/{job.job_id}/fov.mp4"
            else:
                job.phase = "fov-encode-blender"
                job.logs.append("[WARN] ffmpeg missing, using Blender to encode FOV video.")
                blender_encode_cmd = (
                    f"blender --background --python '{WS / 'vis_tool' / 'frames_to_video.py'}' -- "
                    f"--frames_dir '{render_dir}' --output '{fov_video}' --fps 20"
                )
                run_cmd(blender_encode_cmd, job.logs)
                if fov_video.exists() and fov_video.stat().st_size > 0:
                    job.fov_video_url = f"/artifacts/{job.job_id}/fov.mp4"
                else:
                    job.logs.append("[WARN] Blender encode did not produce mp4, output png frames only.")
                    job.fov_frames_url = f"/artifacts/{job.job_id}/render_frames/"
        else:
            job.logs.append("[INFO] Blender pipeline disabled. Skip FOV rendering/encoding.")

        traj_copy = job_dir / traj_path.name
        if traj_path.resolve() != traj_copy.resolve():
            shutil.copy2(traj_path, traj_copy)
        job.traj_url = f"/artifacts/{job.job_id}/{traj_copy.name}"

        job.phase = "done"
        job.status = "done"
    except Exception as e:
        job.status = "failed"
        job.error = str(e)
        job.logs.append(f"[ERROR] {e}")
    finally:
        if scene_proc and scene_proc.poll() is None:
            scene_proc.terminate()


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = HTML_INDEX.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/status":
            q = parse_qs(parsed.query)
            job_id = q.get("job_id", [""])[0]
            with LOCK:
                job = JOBS.get(job_id)
            if not job:
                self._json({"ok": False, "error": "job not found"}, 404)
                return
            self._json(
                {
                    "ok": True,
                    "job_id": job.job_id,
                    "status": job.status,
                    "phase": job.phase,
                    "logs": job.logs[-300:],
                    "error": job.error,
                    "fov_video_url": job.fov_video_url,
                    "fov_frames_url": job.fov_frames_url,
                    "traj_url": job.traj_url,
                    "third_person_video_url": job.third_person_video_url,
                    "third_person_preview_url": job.third_person_preview_url,
                }
            )
            return

        if parsed.path.startswith("/artifacts/"):
            rel = parsed.path[len("/artifacts/"):].lstrip("/")
            local = (JOBS_DIR / rel).resolve()
            if not str(local).startswith(str(JOBS_DIR.resolve())):
                self.send_error(403)
                return
            if local.is_dir():
                items = sorted(local.iterdir())
                lines = [f"<h3>{local.name}</h3><ul>"]
                for p in items:
                    href = parsed.path.rstrip("/") + "/" + p.name
                    lines.append(f'<li><a href="{href}">{p.name}</a></li>')
                lines.append("</ul>")
                body = "\n".join(lines).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if not local.exists():
                self.send_error(404)
                return
            data = local.read_bytes()
            ctype = "application/octet-stream"
            if local.suffix == ".mp4":
                ctype = "video/mp4"
            elif local.suffix == ".txt":
                ctype = "text/plain; charset=utf-8"
            elif local.suffix == ".png":
                ctype = "image/png"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/start":
            self.send_error(404)
            return
        fs = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            },
        )
        if "obj_file" not in fs:
            self._json({"ok": False, "error": "missing obj_file"}, 400)
            return

        obj_field = fs["obj_file"]
        if not getattr(obj_field, "file", None):
            self._json({"ok": False, "error": "invalid obj_file"}, 400)
            return

        form = {}
        keys = [
            "scene_name", "fov_h", "fov_w", "cx", "cy", "fx", "fy", "max_dist", "resolution_",
            "max_vel", "max_acc", "max_jerk", "max_yd", "max_ydd", "drone_radius",
            "start_x", "start_y", "start_z", "viewpoint_dist", "safe_radius",
        ]
        for k in keys:
            form[k] = fs.getvalue(k, "").strip()

        job_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        job_dir = JOBS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        uploads_dir = job_dir / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)

        obj_uploaded = uploads_dir / Path(obj_field.filename or "upload.obj").name
        if obj_uploaded.suffix.lower() != ".obj":
            obj_uploaded = uploads_dir / "upload.obj"
        with obj_uploaded.open("wb") as f:
            shutil.copyfileobj(obj_field.file, f)

        asset_uploaded_paths: List[Path] = []
        upload_fields = []
        if "asset_files" in fs:
            upload_fields.append(fs["asset_files"])
        if "asset_dir_files" in fs:
            upload_fields.append(fs["asset_dir_files"])

        if upload_fields:
            assets = []
            for item in upload_fields:
                if isinstance(item, list):
                    assets.extend(item)
                else:
                    assets.append(item)
            for i, af in enumerate(assets):
                if not getattr(af, "file", None):
                    continue
                if not af.filename:
                    continue
                rel_name = af.filename.replace("\\", "/")
                safe_name = Path(rel_name).name
                if not safe_name:
                    continue
                rel = _safe_rel_path(rel_name)
                dst = uploads_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                with dst.open("wb") as f:
                    shutil.copyfileobj(af.file, f)
                asset_uploaded_paths.append(dst)

        job = Job(job_id=job_id)
        with LOCK:
            JOBS[job_id] = job
        t = threading.Thread(target=run_job, args=(job, form, obj_uploaded, asset_uploaded_paths), daemon=True)
        t.start()
        self._json({"ok": True, "job_id": job_id})


def main() -> None:
    port = int(os.environ.get("FC_WEB_PORT", "8008"))
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[INFO] FC-Planner local web V1 running: http://127.0.0.1:{port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
