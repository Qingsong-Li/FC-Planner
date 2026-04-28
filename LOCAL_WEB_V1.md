# FC-Planner 本地一键网页 V1

## 1. 目标

`local_web_v1.py` 提供一个本地网页入口，支持：

- 上传 `obj`
- 填写核心参数
- 一键触发：
  1) `obj_to_pcd.py`
  2) 生成 launch 并运行 FC-Planner
  3) 自动发 ROS 触发消息（替代 RViz 的 `2D Nav Goal` 与 `2D Pose Estimate`）
  4) 自动录制 RViz 飞行可视化视频（当 `ffmpeg` + `Xvfb` 或可用 `DISPLAY` 存在时）
  4) 调 Blender 生成 FOV 帧
  5) 若有 `ffmpeg`，自动合成 `mp4`
- 页面轮询任务状态并展示结果链接

## 2. 运行方式

在仓库根目录运行：

```bash
python3 local_web_v1.py
```

然后打开：

```text
http://127.0.0.1:8008
```

如果你要改端口：

```bash
FC_WEB_PORT=8010 python3 local_web_v1.py
```

## 3. 依赖说明

V1 脚本本身不依赖第三方 Python Web 框架（Flask/FastAPI 都不需要）。

但任务执行依赖你的本地环境已具备：

- ROS Noetic + FC-Planner 已编译完成（`devel/setup.bash` 可用）
- Blender 命令可用（脚本会调用 `blender --background`）

可选：

- `ffmpeg`：优先用于把 Blender 输出的 `png` 合成 `mp4`
- `Xvfb`：用于无头运行 RViz 并配合 ffmpeg 录屏（可选；有桌面 `DISPLAY` 时也可录）

说明：如果未安装 `ffmpeg`，当前版本会自动调用 Blender 脚本
`FC-Planner/vis_tool/frames_to_video.py` 来合成 `fov.mp4`。

## 4. 结果位置

每个任务产物在：

```text
.local_web_jobs/<job_id>/
```

通常包含：

- `render_frames/*.png`
- `fov.mp4`（优先 ffmpeg；无 ffmpeg 时自动改用 Blender 编码）
- `rviz.mp4`（当 RViz 录屏条件满足时）
- `TrajInfo_*.txt`

## 5. 当前 V1 限制

- RViz 视频录制依赖系统可用的 `ffmpeg`，且需要 `Xvfb` 或有效 `DISPLAY`
- 任务串行执行，默认不支持并发
- 参数白名单是当前表单字段，未覆盖 launch 全部参数
