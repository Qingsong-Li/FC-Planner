#!/usr/bin/env python3
import cgi
import json
import os
import re
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
LAUNCH_DIR = WS / "src" / "hierarchical_coverage_planner" / "launch"
JOBS_DIR = ROOT / ".local_web_jobs"
FRONTEND_DIR = ROOT / "frontend"
PREVIEW_DIR = JOBS_DIR / "_preview"
JOBS_DIR.mkdir(exist_ok=True)
PREVIEW_DIR.mkdir(exist_ok=True)

# 是否启用第一视角(FOV)渲染链路（依赖 Blender；关闭可减少整体耗时）
ENABLE_BLENDER_PIPELINE = False
# 是否启用第三人称巡检视频渲染链路（推荐保持开启）
ENABLE_THIRD_PERSON_PIPELINE = False
# 第三人称仅渲染首帧预览图（调试视角时用；正式运行建议 False）
THIRD_PERSON_PREVIEW_ONLY = False
# 第三人称最多渲染帧数；0 表示不设上限
THIRD_PERSON_MAX_FRAMES = 0
# 对完整轨迹做均匀抽样后的目标帧数（用于压缩时长但保留全程）
THIRD_PERSON_TARGET_FRAMES = 600


@dataclass
class Job:
    job_id: str
    status: str = "queued"  # queued/running/done/failed/canceled
    phase: str = ""
    public_status: str = "等待开始"
    messages: List[str] = field(default_factory=list)
    error: str = ""
    warning: str = ""
    fov_video_url: str = ""
    third_person_video_url: str = ""
    metrics: Dict[str, str] = field(default_factory=dict)
    video_status: Dict[str, str] = field(default_factory=lambda: {"third_person": "idle", "fov": "idle"})
    cancel_requested: bool = False
    process_refs: List[subprocess.Popen] = field(default_factory=list)
    raw_log_path: str = ""
    scene_name: str = ""


JOBS: Dict[str, Job] = {}
LOCK = threading.Lock()


def push_msg(job: Job, text: str) -> None:
    job.messages.append(text)
    if len(job.messages) > 200:
        job.messages = job.messages[-200:]


def set_phase(job: Job, phase: str, public_status: str, msg: Optional[str] = None) -> None:
    job.phase = phase
    job.public_status = public_status
    if msg:
        push_msg(job, msg)


def check_cancel(job: Job) -> None:
    if job.cancel_requested:
        raise RuntimeError("任务已终止")


def run_cmd(cmd: str, job: Job, cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None) -> None:
    raw = Path(job.raw_log_path)
    raw.parent.mkdir(parents=True, exist_ok=True)
    with raw.open("a", encoding="utf-8") as f:
        f.write(f"\n$ {cmd}\n")
    p = subprocess.Popen(
        ["bash", "-lc", cmd],
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    job.process_refs.append(p)
    assert p.stdout is not None
    with raw.open("a", encoding="utf-8", errors="ignore") as f:
        for line in p.stdout:
            f.write(line)
            if job.cancel_requested:
                p.terminate()
                break
    code = p.wait()
    if code != 0:
        raise RuntimeError(f"命令执行失败: {cmd}")


def run_cmd_simple(cmd: str, cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None) -> str:
    p = subprocess.run(
        ["bash", "-lc", cmd],
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    out = p.stdout or ""
    if p.returncode != 0:
        raise RuntimeError((out[-600:] if out else "命令执行失败").strip())
    return out


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def parse_metrics(scene_log: Path) -> Dict[str, str]:
    if not scene_log.exists():
        return {}
    txt = strip_ansi(scene_log.read_text(errors="ignore").replace("\x00", ""))

    def pick(pattern: str):
        m = re.search(pattern, txt)
        return m.group(1) if m else ""

    metrics = {
        "path_length_m": pick(r"path length = ([\d\.]+) m"),
        "path_coverage_pct": pick(r"path coverage rate = ([\d\.]+) %"),
        "traj_coverage_pct": pick(r"trajectory coverage rate = ([\d\.]+) %"),
        "viewpoints_count": pick(r"all viewpoints quantity = (\d+)"),
        "waypoints_count": pick(r"all waypoints quantity = (\d+)"),
        "planning_latency_ms": pick(r"system computation latency = ([\d\.]+) ms"),
        "traj_gen_latency_ms": pick(r"Trajectory Generation latency = ([\d\.]+) ms"),
        "coverage_eval_s": pick(r"(?:path|trajectory) coverage evaluation time = ([\d\.]+) s"),
        "traj_length_m": pick(r"traj length = ([\d\.]+) m"),
        "traj_exec_time_s": pick(r"traj exec time = ([\d\.]+) s"),
        "traj_max_vel_mps": pick(r"traj max vel = ([\d\.]+) m/s"),
        "traj_max_acc_mps2": pick(r"traj max acc = ([\d\.]+) m/s\^2"),
    }
    return metrics


def wait_metrics_ready(scene_log: Path, timeout_s: int = 180) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        m = parse_metrics(scene_log)
        # 至少拿到这三项再继续，避免前端指标空白
        if m.get("path_coverage_pct") and m.get("path_length_m") and m.get("planning_latency_ms"):
            return
        time.sleep(1.0)


def make_launch_from_eiffel(form: Dict[str, str], launch_out: Path, mesh_path: Path, pcd_path: Path, fullcloud_path: Path, traj_path: Path, cloud_path: Path, pos_path: Path, pitch_path: Path, yaw_path: Path) -> None:
    tpl = (LAUNCH_DIR / "eiffeltower.launch").read_text(encoding="utf-8")

    def rp(old: str, new: str) -> None:
        nonlocal tpl
        tpl = tpl.replace(old, new)

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

    rp('value="$(find hierarchical_coverage_planner)/data/EiffelTower.pcd"', f'value="{pcd_path}"')
    rp('value="$(find hierarchical_coverage_planner)/data/mesh/EiffelTower.obj"', f'value="{mesh_path}"')
    rp('value="$(find hierarchical_coverage_planner)/data/EiffelTowermore.pcd"', f'value="{fullcloud_path}"')
    rp('value="$(find hierarchical_coverage_planner)/solution/Traj/TrajInfoEiffelTower.txt"', f'value="{traj_path}"')
    rp('value="$(find hierarchical_coverage_planner)/solution/Traj/CloudInfoEiffelTower.txt"', f'value="{cloud_path}"')
    rp('value="$(find hierarchical_coverage_planner)/solution/Traj/PositionCoeffEiffelTower.txt"', f'value="{pos_path}"')
    rp('value="$(find hierarchical_coverage_planner)/solution/Traj/PitchCoeffEiffelTower.txt"', f'value="{pitch_path}"')
    rp('value="$(find hierarchical_coverage_planner)/solution/Traj/YawCoeffEiffelTower.txt"', f'value="{yaw_path}"')

    launch_out.write_text(tpl, encoding="utf-8")


def run_job(job: Job, form: Dict[str, str], obj_uploaded_path: Path, asset_uploaded_paths: List[Path]) -> None:
    scene_proc = None
    scene_out = None
    ros_env = os.environ.copy()
    ros_env["DISABLE_ROS1_EOL_WARNINGS"] = "1"

    try:
        job.status = "running"
        set_phase(job, "prepare", "正在准备任务…", "已收到任务，开始准备环境。")

        scene = "".join(ch for ch in form["scene_name"].strip() if ch.isalnum() or ch in ("_", "-")).strip("_-")
        if not scene:
            raise RuntimeError("场景名称无效")
        job.scene_name = scene

        job_dir = JOBS_DIR / job.job_id
        scene_log = job_dir / "scene.log"
        job.raw_log_path = str(job_dir / "internal.log")

        mesh_dst = job_dir / f"{scene}.obj"
        pcd_dst = job_dir / f"{scene}.pcd"
        fullcloud_dst = job_dir / f"{scene}more.pcd"
        traj_path = job_dir / f"TrajInfo_{scene}.txt"
        cloud_path = job_dir / f"CloudInfo_{scene}.txt"
        pos_path = job_dir / f"PositionCoeff_{scene}.txt"
        pitch_path = job_dir / f"PitchCoeff_{scene}.txt"
        yaw_path = job_dir / f"YawCoeff_{scene}.txt"

        shutil.copy2(obj_uploaded_path, mesh_dst)
        for src in asset_uploaded_paths:
            if src.exists() and src.resolve() != mesh_dst.resolve():
                dst = job_dir / src.name
                if src.resolve() != dst.resolve():
                    shutil.copy2(src, dst)

        check_cancel(job)
        set_phase(job, "convert", "正在处理模型文件…", "正在把三维模型整理成可计算格式。")
        run_cmd(
            "python3 obj_to_pcd.py "
            f"--obj '{mesh_dst}' --out-prefix '{scene}' --out-dir '{job_dir}' "
            "--full-points 500000 --rosa-voxel 0.05 --rosa-max-points 120000",
            job,
            cwd=ROOT,
        )
        shutil.move(str(job_dir / f"{scene}_rosa.pcd"), str(pcd_dst))
        shutil.move(str(job_dir / f"{scene}_fullcloud.pcd"), str(fullcloud_dst))

        check_cancel(job)
        set_phase(job, "planning", "正在规划巡检路线…", "系统正在计算巡检路线和飞行策略。")
        launch_file = job_dir / f"web_{scene}.launch"
        make_launch_from_eiffel(form, launch_file, mesh_dst, pcd_dst, fullcloud_dst, traj_path, cloud_path, pos_path, pitch_path, yaw_path)

        scene_cmd = f"cd '{WS}' && source devel/setup.bash && roslaunch '{launch_file}'"
        scene_out = scene_log.open("w", encoding="utf-8")
        scene_proc = subprocess.Popen(["bash", "-lc", scene_cmd], stdout=scene_out, stderr=scene_out, env=ros_env)
        job.process_refs.append(scene_proc)
        time.sleep(8.0)

        trigger_goal = (
            "source devel/setup.bash && rostopic pub -1 /move_base_simple/goal geometry_msgs/PoseStamped "
            "\"{header: {frame_id: 'world'}, pose: {position: {x: 0.0, y: 0.0, z: 0.0}, orientation: {x: 0.0, y: 0.0, z: 0.0, w: 1.0}}}\""
        )
        run_cmd(f"cd '{WS}' && {trigger_goal}", job, env=ros_env)

        timeout_s = 1800
        t0 = time.time()
        while True:
            check_cancel(job)
            if traj_path.exists() and traj_path.stat().st_size > 0 and cloud_path.exists() and cloud_path.stat().st_size > 0:
                break
            if time.time() - t0 > timeout_s:
                raise RuntimeError("规划超时，请尝试简化模型或参数")
            time.sleep(1.5)

        # 等待关键指标写入日志，避免结果页“关键数据为空”
        set_phase(job, "metrics", "正在整理结果数据…", "正在汇总本次巡检的关键结果。")
        wait_metrics_ready(scene_log, timeout_s=180)

        job.metrics = parse_metrics(scene_log)
        set_phase(job, "done", "任务完成", "关键数据已生成，可按需生成可视化视频。")
        job.status = "done"
    except Exception as e:
        if job.cancel_requested:
            job.status = "canceled"
            set_phase(job, "canceled", "任务已终止", "已按你的要求终止任务。")
        else:
            job.status = "failed"
            job.error = str(e)
            set_phase(job, "failed", "任务执行失败", "任务执行中断，请稍后重试。")
    finally:
        if scene_proc and scene_proc.poll() is None:
            scene_proc.terminate()
        if scene_out is not None:
            scene_out.flush()
            scene_out.close()


def get_running_job() -> Optional[Job]:
    with LOCK:
        for j in JOBS.values():
            if j.status in ("queued", "running"):
                return j
    return None


def render_video(job: Job, video_type: str) -> None:
    job_dir = JOBS_DIR / job.job_id
    scene = job.scene_name
    mesh_dst = job_dir / f"{scene}.obj"
    traj_path = job_dir / f"TrajInfo_{scene}.txt"
    cloud_path = job_dir / f"CloudInfo_{scene}.txt"
    if not mesh_dst.exists() or not traj_path.exists():
        raise RuntimeError("缺少渲染所需文件")

    if video_type == "third_person":
        third_frames = job_dir / "third_person_frames"
        third_frames.mkdir(parents=True, exist_ok=True)
        third_video = job_dir / "third_person.mp4"
        drone_model = WS / "src" / "traj_utils" / "f250.dae"
        run_cmd(
            f"blender --background --python '{WS / 'vis_tool' / 'render_third_person.py'}' -- "
            f"--scene_model_path '{mesh_dst}' --drone_model_path '{drone_model}' "
            f"--traj_path '{traj_path}' --cloud_path '{cloud_path}' --renderout_path '{third_frames}' "
            f"--fps 20 --sample_step 5 --max_frames {THIRD_PERSON_MAX_FRAMES} --target_frames {THIRD_PERSON_TARGET_FRAMES} "
            + ("--preview_only" if THIRD_PERSON_PREVIEW_ONLY else ""),
            job,
        )
        if command_exists("ffmpeg"):
            run_cmd(
                f"ffmpeg -y -framerate 20 -pattern_type glob -i '{third_frames}/*.png' -c:v libx264 -pix_fmt yuv420p '{third_video}'",
                job,
            )
        else:
            run_cmd(
                f"blender --background --python '{WS / 'vis_tool' / 'frames_to_video.py'}' -- --frames_dir '{third_frames}' --output '{third_video}' --fps 20",
                job,
            )
        if third_video.exists() and third_video.stat().st_size > 0:
            job.third_person_video_url = f"/artifacts/{job.job_id}/third_person.mp4"
        else:
            raise RuntimeError("第三人称视频生成失败")
    elif video_type == "fov":
        fov_frames = job_dir / "render_frames"
        fov_video = job_dir / "fov.mp4"
        run_cmd(
            f"blender --background --python '{WS / 'vis_tool' / 'render_in_traj.py'}' -- --model_path '{mesh_dst}' --traj_path '{traj_path}' --renderout_path '{fov_frames}' --backend BLENDER_EEVEE --fast_mode",
            job,
        )
        if command_exists("ffmpeg"):
            run_cmd(f"ffmpeg -y -framerate 20 -i '{fov_frames}/%04d.png' -c:v libx264 -pix_fmt yuv420p '{fov_video}'", job)
        else:
            run_cmd(
                f"blender --background --python '{WS / 'vis_tool' / 'frames_to_video.py'}' -- --frames_dir '{fov_frames}' --output '{fov_video}' --fps 20",
                job,
            )
        if fov_video.exists() and fov_video.stat().st_size > 0:
            job.fov_video_url = f"/artifacts/{job.job_id}/fov.mp4"
        else:
            raise RuntimeError("FOV视频生成失败")
    else:
        raise RuntimeError("未知视频类型")


def run_render_video(job: Job, video_type: str) -> None:
    try:
        job.video_status[video_type] = "running"
        if video_type == "third_person":
            set_phase(job, "render_third_person", "正在生成第三人称视频…", "已开始生成第三人称视频。")
        else:
            set_phase(job, "render_fov", "正在生成无人机FOV视频…", "已开始生成无人机FOV视频。")
        render_video(job, video_type)
        job.video_status[video_type] = "done"
        set_phase(job, "done", "任务完成", "视频生成完成。")
    except Exception as e:
        job.video_status[video_type] = "failed"
        job.error = str(e)
        set_phase(job, "done", "任务完成", "视频生成失败，请重试。")


def build_result_files(job: Job) -> List[Dict[str, str]]:
    if not job.scene_name:
        return []
    job_dir = JOBS_DIR / job.job_id
    scene = job.scene_name
    specs = [
        ("cloud_info", f"CloudInfo_{scene}.txt", "CloudInfo", "巡检过程中每个时刻可见点云信息，用于分析覆盖过程。"),
        ("pitch_coeff", f"PitchCoeff_{scene}.txt", "PitchCoeff", "相机/机体俯仰角系数，描述姿态随时间变化。"),
        ("position_coeff", f"PositionCoeff_{scene}.txt", "PositionCoeff", "无人机三维位置轨迹系数，用于还原飞行路径。"),
        ("traj_info", f"TrajInfo_{scene}.txt", "TrajInfo", "轨迹关键时间序列与姿态信息，是回放与渲染的核心输入。"),
        ("yaw_coeff", f"YawCoeff_{scene}.txt", "YawCoeff", "偏航角系数，描述朝向随时间变化。"),
    ]
    out: List[Dict[str, str]] = []
    for key, filename, label, desc in specs:
        p = job_dir / filename
        if p.exists() and p.stat().st_size > 0:
            out.append({
                "key": key,
                "label": label,
                "filename": filename,
                "desc": desc,
                "size_bytes": str(p.stat().st_size),
                "url": f"/artifacts/{job.job_id}/{filename}",
            })
    return out


def build_obj_preview(obj_field, asset_fields: List) -> Dict[str, str]:
    if not getattr(obj_field, "file", None) or not obj_field.filename:
        raise RuntimeError("请先上传 OBJ 文件")

    preview_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    preview_job_dir = PREVIEW_DIR / preview_id
    preview_job_dir.mkdir(parents=True, exist_ok=True)

    obj_name = Path(obj_field.filename.replace("\\", "/")).name
    if not obj_name.lower().endswith(".obj"):
        obj_name = "upload.obj"
    obj_path = preview_job_dir / obj_name
    with obj_path.open("wb") as f:
        shutil.copyfileobj(obj_field.file, f)

    for af in asset_fields:
        if not getattr(af, "file", None) or not af.filename:
            continue
        name = Path(af.filename.replace("\\", "/")).name
        if not name:
            continue
        with (preview_job_dir / name).open("wb") as f:
            shutil.copyfileobj(af.file, f)

    preview_png = preview_job_dir / "preview.png"
    if not command_exists("blender"):
        raise RuntimeError("本机未检测到 blender，无法生成模型快照")
    run_cmd_simple(
        f"blender --background --python '{WS / 'vis_tool' / 'render_model_snapshot.py'}' -- "
        f"--model_path '{obj_path}' --output_path '{preview_png}' --width 1280 --height 720 --samples 8",
        cwd=ROOT,
    )
    if not preview_png.exists() or preview_png.stat().st_size == 0:
        raise RuntimeError("模型快照生成失败")

    return {
        "preview_url": f"/artifacts/_preview/{preview_id}/preview.png",
        "model_name": Path(obj_name).stem,
    }


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
            data = (FRONTEND_DIR / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if parsed.path.startswith("/static/"):
            rel = parsed.path[len("/static/"):]
            file = (FRONTEND_DIR / rel).resolve()
            if not str(file).startswith(str(FRONTEND_DIR.resolve())) or not file.exists():
                self.send_error(404)
                return
            data = file.read_bytes()
            ctype = "text/plain"
            if file.suffix == ".css":
                ctype = "text/css"
            elif file.suffix == ".js":
                ctype = "application/javascript"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if parsed.path == "/api/status":
            q = parse_qs(parsed.query)
            job_id = q.get("job_id", [""])[0]
            with LOCK:
                job = JOBS.get(job_id)
            if not job:
                self._json({"ok": False, "error": "job not found"}, 404)
                return
            # 兜底：任务结束后若内存指标为空，按需从 scene.log 回填，避免页面显示全 "-"
            if job.status in ("done", "failed", "canceled"):
                scene_log = JOBS_DIR / job.job_id / "scene.log"
                refreshed = parse_metrics(scene_log)
                if any(v for v in refreshed.values()):
                    job.metrics = refreshed
            self._json({
                "ok": True,
                "job_id": job.job_id,
                "status": job.status,
                "phase": job.phase,
                "public_status": job.public_status,
                "messages": job.messages[-80:],
                "error": job.error,
                "warning": job.warning,
                "third_person_video_url": job.third_person_video_url,
                "fov_video_url": job.fov_video_url,
                "metrics": job.metrics,
                "video_status": job.video_status,
                "result_files": build_result_files(job),
            })
            return

        if parsed.path.startswith("/artifacts/"):
            rel = parsed.path[len("/artifacts/"):].lstrip("/")
            local = (JOBS_DIR / rel).resolve()
            if not str(local).startswith(str(JOBS_DIR.resolve())) or not local.exists():
                self.send_error(404)
                return
            data = local.read_bytes()
            ctype = "application/octet-stream"
            if local.suffix == ".mp4":
                ctype = "video/mp4"
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

        if parsed.path == "/api/preview_obj":
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
                self._json({"ok": False, "error": "请先上传 OBJ 文件。"}, 400)
                return
            obj_field = fs["obj_file"]
            upload_fields = []
            if "asset_files" in fs:
                upload_fields.append(fs["asset_files"])
            if "asset_dir_files" in fs:
                upload_fields.append(fs["asset_dir_files"])
            assets = []
            for item in upload_fields:
                assets.extend(item if isinstance(item, list) else [item])
            try:
                info = build_obj_preview(obj_field, assets)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
                return
            self._json({"ok": True, **info})
            return

        if parsed.path == "/api/cancel":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            payload = json.loads(body)
            job_id = payload.get("job_id", "")
            with LOCK:
                job = JOBS.get(job_id)
            if not job:
                self._json({"ok": False, "error": "job not found"}, 404)
                return
            job.cancel_requested = True
            for p in list(job.process_refs):
                try:
                    if p.poll() is None:
                        p.terminate()
                except Exception:
                    pass
            self._json({"ok": True})
            return

        if parsed.path == "/api/render_video":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            payload = json.loads(body)
            job_id = payload.get("job_id", "")
            video_type = payload.get("video_type", "")
            if video_type not in ("third_person", "fov"):
                self._json({"ok": False, "error": "video_type 必须是 third_person 或 fov"}, 400)
                return
            with LOCK:
                job = JOBS.get(job_id)
            if not job:
                self._json({"ok": False, "error": "job not found"}, 404)
                return
            if job.status != "done":
                self._json({"ok": False, "error": "请等待主任务完成后再生成视频。"}, 409)
                return
            if job.video_status.get(video_type) == "running":
                self._json({"ok": False, "error": "该视频正在生成中。"}, 409)
                return
            t = threading.Thread(target=run_render_video, args=(job, video_type), daemon=True)
            t.start()
            self._json({"ok": True})
            return

        if parsed.path != "/api/start":
            self.send_error(404)
            return

        if get_running_job() is not None:
            self._json({"ok": False, "error": "当前已有任务运行，请先等待结束或点击终止。"}, 409)
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
            self._json({"ok": False, "error": "请先上传 OBJ 文件。"}, 400)
            return

        obj_field = fs["obj_file"]
        if not getattr(obj_field, "file", None) or not obj_field.filename:
            self._json({"ok": False, "error": "请先上传 OBJ 文件。"}, 400)
            return

        form = {}
        keys = ["scene_name", "fov_h", "fov_w", "cx", "cy", "fx", "fy", "max_dist", "resolution_", "max_vel", "max_acc", "max_jerk", "max_yd", "max_ydd", "drone_radius", "start_x", "start_y", "start_z", "viewpoint_dist", "safe_radius"]
        for k in keys:
            form[k] = fs.getvalue(k, "").strip() or "0"

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
                assets.extend(item if isinstance(item, list) else [item])
            for af in assets:
                if not getattr(af, "file", None) or not af.filename:
                    continue
                rel = Path(af.filename.replace("\\", "/")).name
                if not rel:
                    continue
                dst = uploads_dir / rel
                with dst.open("wb") as f:
                    shutil.copyfileobj(af.file, f)
                asset_uploaded_paths.append(dst)

        job = Job(job_id=job_id)
        if not asset_uploaded_paths:
            job.warning = "未上传材质文件，可能影响无人机FOV视频显示效果。"

        with LOCK:
            JOBS[job_id] = job
        t = threading.Thread(target=run_job, args=(job, form, obj_uploaded, asset_uploaded_paths), daemon=True)
        t.start()
        self._json({"ok": True, "job_id": job_id, "warning": job.warning})


def main() -> None:
    port = int(os.environ.get("FC_WEB_PORT", "8008"))
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[INFO] 无人机三维目标巡检视觉系统: http://127.0.0.1:{port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
