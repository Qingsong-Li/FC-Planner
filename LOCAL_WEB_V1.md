# 无人机三维目标巡检视觉系统（本地 V1）

## 1. 目标

`local_web_v1.py` 提供一个本地网页入口，支持：

- 上传 `OBJ`，可选上传 `MTL/材质文件`
- 填写核心巡检参数
- 一键触发：
  1) `obj_to_pcd.py` 模型点云转换
  2) 动态生成 launch 并运行 FC-Planner 规划
  3) 自动触发规划任务并等待轨迹结果
  4) Blender 生成第三人称巡检视频（可选再生成 FOV 视频）
  5) 自动合成 `mp4`
- 运行中锁定上传和参数编辑
- 支持“终止运行”
- 页面只显示用户友好进度提示，不显示底层终端命令
- 结果页展示视频与关键性能指标（不展示轨迹文本文件）
- 结果页支持成果文件下载（可勾选下载）：
  - `CloudInfo`：可见点云覆盖过程信息
  - `PitchCoeff`：俯仰角系数
  - `PositionCoeff`：三维位置系数
  - `TrajInfo`：轨迹与姿态关键时序
  - `YawCoeff`：偏航角系数

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

- `ffmpeg`：用于把 Blender 输出的 `png` 合成 `mp4`（推荐）

说明：如果未安装 `ffmpeg`，当前版本会自动调用 Blender 脚本
`FC-Planner/vis_tool/frames_to_video.py` 合成视频。

## 4. 结果位置

每个任务产物在：

```text
.local_web_jobs/<job_id>/
```

通常包含：

- `third_person_frames/*.png`
- `third_person.mp4`
- `render_frames/*.png`（当开启 FOV 渲染时）
- `fov.mp4`（当开启 FOV 渲染时）
- `scene.log`（规划日志）
- `internal.log`（内部命令日志，仅排障用）

## 5. 当前 V1 限制

- 默认串行执行，不支持并发任务
- 参数白名单是当前表单字段，未覆盖 launch 全部参数
- “已扫描区域实时高亮”仍在迭代中（当前版本重点是稳定流程与结果展示）
