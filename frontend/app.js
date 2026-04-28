(() => {
  const PHASE_LABEL = {
    prepare: "任务准备",
    convert: "模型处理",
    planning: "路线规划",
    metrics: "关键数据整理",
    render_third_person: "第三人称视频生成",
    render_fov: "无人机FOV视频生成",
    done: "已完成",
    failed: "执行失败",
    canceled: "已终止"
  };

  const STATUS_BADGE = {
    idle: ["未开始", "badge--idle"],
    running: ["处理中", "badge--running"],
    done: ["已完成", "badge--success"],
    failed: ["失败", "badge--danger"],
    canceled: ["已终止", "badge--warning"]
  };

  const state = {
    jobId: "",
    pollTimer: null,
    lastStatus: "",
    lastPhase: "",
    phaseStartTs: 0,
    activeStartTs: 0,
    totalActiveMs: 0,
    lockUI: false,
    lockReason: "",
    hasEndedToast: false,
    pollErrorNotified: false,
    objPreviewSeq: 0,
    objNormalize: null,
    trajOverlayUrlLoaded: "",
    trajOverlayVisible: false
  };

  const el = {
    form: document.getElementById("jobForm"),
    objFileInput: document.getElementById("objFileInput"),
    objFileName: document.getElementById("objFileName"),
    objError: document.getElementById("objError"),
    assetFilesInput: document.getElementById("assetFilesInput"),
    assetFilesName: document.getElementById("assetFilesName"),
    assetDirInput: document.getElementById("assetDirInput"),
    assetDirName: document.getElementById("assetDirName"),
    sceneNameInput: document.getElementById("sceneNameInput"),
    startBtn: document.getElementById("startBtn"),
    stopBtn: document.getElementById("stopBtn"),
    hint: document.getElementById("hint"),
    statusLine: document.getElementById("statusLine"),
    progressBar: document.getElementById("progressBar"),
    progressMeta: document.getElementById("progressMeta"),
    phaseBadge: document.getElementById("phaseBadge"),
    timeline: document.getElementById("timeline"),
    artifacts: document.getElementById("artifacts"),
    downloads: document.getElementById("downloads"),
    metrics: document.getElementById("metrics"),
    toastContainer: document.getElementById("toastContainer"),
    objPreviewCanvas: document.getElementById("objPreviewCanvas"),
    objPreviewEmpty: document.getElementById("objPreviewEmpty"),
    objPreviewBadge: document.getElementById("objPreviewBadge"),
    objPreviewMeta: document.getElementById("objPreviewMeta"),
    resetObjViewBtn: document.getElementById("resetObjViewBtn"),
    objBgSelect: document.getElementById("objBgSelect"),
    objLightRange: document.getElementById("objLightRange"),
    trajOverlayToggle: document.getElementById("trajOverlayToggle"),
    nextAction: document.getElementById("nextAction"),
    globalStatusBadge: document.getElementById("globalStatusBadge"),
    jobIdentity: document.getElementById("jobIdentity")
  };

  function toMMSS(totalSeconds) {
    const s = Math.max(0, Math.floor(totalSeconds || 0));
    const mm = String(Math.floor(s / 60)).padStart(2, "0");
    const ss = String(s % 60).padStart(2, "0");
    return `${mm}:${ss}`;
  }

  function formatNum(value, fraction = 2) {
    if (value === undefined || value === null || value === "") return "-";
    const n = Number(value);
    if (!Number.isFinite(n)) return "-";
    return n.toLocaleString("zh-CN", { maximumFractionDigits: fraction, minimumFractionDigits: 0 });
  }

  function formatBytes(value) {
    const bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes <= 0) return "-";
    const units = ["B", "KB", "MB", "GB"];
    let size = bytes;
    let idx = 0;
    while (size >= 1024 && idx < units.length - 1) {
      size /= 1024;
      idx += 1;
    }
    return `${size.toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
  }

  function showToast(message, type = "info") {
    const toast = document.createElement("div");
    toast.className = `toast toast--${type}`;
    toast.textContent = message;
    el.toastContainer.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
  }

  function setFieldError(input, errorEl, msg) {
    const picker = input ? input.closest(".file-picker") : null;
    if (msg) {
      input.classList.add("input--invalid");
      if (picker) picker.classList.add("file-picker--invalid");
      errorEl.textContent = msg;
    } else {
      input.classList.remove("input--invalid");
      if (picker) picker.classList.remove("file-picker--invalid");
      errorEl.textContent = "";
    }
  }

  function setSimpleBadge(target, label, cls) {
    target.className = `badge ${cls}`;
    target.textContent = label;
  }

  function updatePickerCaption(input, labelEl, emptyText) {
    if (!input || !labelEl) return;
    const files = input.files ? Array.from(input.files) : [];
    if (!files.length) {
      labelEl.textContent = emptyText;
      return;
    }
    if (files.length === 1) {
      labelEl.textContent = files[0].name;
      return;
    }
    labelEl.textContent = `已选择 ${files.length} 个文件`;
  }

  function ensureFieldHelpSlots() {
    const fields = el.form.querySelectorAll(".field");
    fields.forEach((field) => {
      if (!field.querySelector(".field__help")) {
        const span = document.createElement("span");
        span.className = "field__help";
        span.innerHTML = "&nbsp;";
        field.appendChild(span);
      }
      if (!field.querySelector(".field__error")) {
        const span = document.createElement("span");
        span.className = "field__error";
        field.appendChild(span);
      }
    });
  }

  function getElapsedMs() {
    return state.totalActiveMs + (state.activeStartTs ? (Date.now() - state.activeStartTs) : 0);
  }

  function updateClock(active) {
    if (active && !state.activeStartTs) state.activeStartTs = Date.now();
    if (!active && state.activeStartTs) {
      state.totalActiveMs += (Date.now() - state.activeStartTs);
      state.activeStartTs = 0;
    }
  }

  function calcProgress(data, phaseElapsedSec) {
    const { status, phase, video_status: videoStatus } = data;
    const videoRunning = videoStatus?.third_person === "running" || videoStatus?.fov === "running";
    if (status === "queued") return 2;
    if (status === "running") {
      if (phase === "metrics") return Math.min(99, Math.max(85, 85 + Math.floor((phaseElapsedSec / 180) * 14)));
      const map = { prepare: 8, convert: 25, planning: 55, metrics: 85, done: 100 };
      return map[phase] ?? 40;
    }
    if (status === "done" && videoRunning) return 92;
    return 100;
  }

  function setBadge(target, label, cls) {
    target.className = `badge ${cls}`;
    target.textContent = label;
  }

  function setStatusBadges(status, phase) {
    const [label, cls] = STATUS_BADGE[status] || STATUS_BADGE.idle;
    setBadge(el.globalStatusBadge, label, cls);
    const phaseText = PHASE_LABEL[phase] || "处理中";
    const phaseClass = status === "failed"
      ? "badge--danger"
      : status === "canceled"
        ? "badge--warning"
        : status === "done"
          ? "badge--success"
          : "badge--running";
    setBadge(el.phaseBadge, phaseText, phaseClass);
  }

  function setFormLocked(locked, reason = "") {
    if (state.lockUI === locked && state.lockReason === reason) return;
    state.lockUI = locked;
    state.lockReason = reason;
    const controls = el.form.querySelectorAll("input, select, textarea, button");
    controls.forEach((node) => {
      if (node.id === "stopBtn") return;
      node.disabled = locked;
    });
    el.stopBtn.disabled = !locked;
  }

  function setButtonLoading(button, loading, loadingText, normalText) {
    if (!button) return;
    if (loading) {
      button.classList.add("is-loading");
      button.dataset.prevText = button.textContent;
      button.textContent = loadingText;
      button.disabled = true;
    } else {
      button.classList.remove("is-loading");
      button.textContent = normalText || button.dataset.prevText || button.textContent;
      button.disabled = false;
    }
  }

  function setNextAction(text) {
    el.nextAction.textContent = text;
  }

  function renderTimeline(lines) {
    el.timeline.innerHTML = "";
    const items = lines && lines.length ? lines : ["等待任务开始后显示运行日志…"];
    items.slice(-120).forEach((line) => {
      const li = document.createElement("li");
      li.textContent = line;
      li.title = line;
      el.timeline.appendChild(li);
    });
  }

  function renderMetrics(metrics, loading = false) {
    if (loading) {
      el.metrics.innerHTML = `
        <h3 class="metrics-title">关键性能数据</h3>
        <div class="skeleton-grid">
          <div class="skeleton-card"></div><div class="skeleton-card"></div><div class="skeleton-card"></div>
          <div class="skeleton-card"></div><div class="skeleton-card"></div><div class="skeleton-card"></div>
        </div>`;
      return;
    }

    const m = metrics || {};
    const cards = [
      ["路径总长度", m.path_length_m ? `${formatNum(m.path_length_m, 3)} m` : "-"],
      ["路径覆盖率", m.path_coverage_pct ? `${formatNum(m.path_coverage_pct, 3)} %` : "-"],
      ["轨迹覆盖率", m.traj_coverage_pct ? `${formatNum(m.traj_coverage_pct, 3)} %` : "-"],
      ["轨迹总长度", m.traj_length_m ? `${formatNum(m.traj_length_m, 3)} m` : "-"],
      ["预计飞行时长", m.traj_exec_time_s ? `${formatNum(m.traj_exec_time_s, 2)} s` : "-"],
      ["视点数量", m.viewpoints_count ? formatNum(m.viewpoints_count, 0) : "-"],
      ["路径点数量", m.waypoints_count ? formatNum(m.waypoints_count, 0) : "-"],
      ["最大飞行速度", m.traj_max_vel_mps ? `${formatNum(m.traj_max_vel_mps, 3)} m/s` : "-"],
      ["最大飞行加速度", m.traj_max_acc_mps2 ? `${formatNum(m.traj_max_acc_mps2, 3)} m/s²` : "-"],
      ["规划耗时", m.planning_latency_ms ? `${formatNum(m.planning_latency_ms, 3)} ms` : "-"],
      ["轨迹生成耗时", m.traj_gen_latency_ms ? `${formatNum(m.traj_gen_latency_ms, 3)} ms` : "-"],
      ["覆盖评估耗时", m.coverage_eval_s ? `${formatNum(m.coverage_eval_s, 3)} s` : "-"]
    ];

    const hasAny = cards.some(([, value]) => value !== "-");
    el.metrics.innerHTML = `
      <h3 class="metrics-title">关键性能数据</h3>
      ${hasAny
        ? `<div class="metric-grid">${cards.map(([k, v]) => `<article class="metric-item"><div class="metric-label">${k}</div><div class="metric-value">${v}</div></article>`).join("")}</div>`
        : `<div class="empty-block">暂无关键数据。请先运行任务。</div>`}
    `;
  }

  function videoStatusBadge(status) {
    if (status === "running") return `<span class="badge badge--running">生成中</span>`;
    if (status === "done") return `<span class="badge badge--success">已完成</span>`;
    if (status === "failed") return `<span class="badge badge--danger">失败</span>`;
    return `<span class="badge badge--idle">未生成</span>`;
  }

  function renderVideoCard({ title, key, videoUrl, canRender, running, status }) {
    const actionLabel = key === "third_person" ? "生成第三人称视频" : "生成 FOV 视频";
    const actionDisabled = !canRender || running;
    const downloadLabel = key === "third_person" ? "下载第三人称视频" : "下载 FOV 视频";
    const videoBody = running
      ? `<div class="spinner-line">正在渲染中，请稍候…</div>`
      : videoUrl
        ? `<div class="empty-block">视频已生成。网页不内嵌预览，请直接下载查看。</div>`
        : `<div class="empty-block">尚未生成。你可以在关键数据确认后再生成。</div>`;

    return `
      <article class="video-card" data-video-card="${key}">
        <div class="video-card__head">
          <div>
            <div class="video-card__title">${title}</div>
            ${videoStatusBadge(status)}
          </div>
          <div class="video-card__ops">
            <button class="btn btn--ghost btn--sm" data-render-video="${key}" ${actionDisabled ? "disabled" : ""}>${running ? "生成中" : actionLabel}</button>
            <button class="btn btn--primary btn--sm" data-download-video="${key}" ${videoUrl ? "" : "disabled"}>${downloadLabel}</button>
          </div>
        </div>
        ${videoBody}
      </article>`;
  }

  function triggerFileDownload(url, filename) {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename || "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function renderArtifacts(data) {
    const status = data.status;
    const vs = data.video_status || {};
    const done = status === "done";
    const content = [
      renderVideoCard({
        title: "第三人称巡检视频",
        key: "third_person",
        videoUrl: data.third_person_video_url,
        canRender: done,
        running: vs.third_person === "running",
        status: vs.third_person || "idle"
      }),
      renderVideoCard({
        title: "无人机 FOV 视频",
        key: "fov",
        videoUrl: data.fov_video_url,
        canRender: done,
        running: vs.fov === "running",
        status: vs.fov || "idle"
      })
    ].join("");

    el.artifacts.innerHTML = content;
    el.artifacts.querySelectorAll("[data-render-video]").forEach((btn) => {
      btn.addEventListener("click", () => triggerVideo(btn.dataset.renderVideo, btn));
    });
    el.artifacts.querySelectorAll("[data-download-video]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.dataset.downloadVideo;
        const videoUrl = key === "third_person" ? data.third_person_video_url : data.fov_video_url;
        if (!videoUrl) return;
        const filename = key === "third_person" ? "third_person.mp4" : "fov.mp4";
        triggerFileDownload(videoUrl, filename);
      });
    });
  }

  function renderDownloads(data) {
    const status = data.status;
    const files = data.result_files || [];
    if (status !== "done" && status !== "failed" && status !== "canceled") {
      el.downloads.innerHTML = `
        <h3 class="metrics-title">成果文件下载</h3>
        <div class="empty-block">任务完成后可下载轨迹与姿态成果文件。</div>
      `;
      return;
    }
    if (!files.length) {
      el.downloads.innerHTML = `
        <h3 class="metrics-title">成果文件下载</h3>
        <div class="empty-block">当前任务尚未产出可下载成果文件。</div>
      `;
      return;
    }

    el.downloads.innerHTML = `
      <h3 class="metrics-title">成果文件下载</h3>
      <div class="download-actions">
        <button id="selectAllFilesBtn" type="button" class="btn btn--ghost btn--sm">全选</button>
        <button id="downloadSelectedBtn" type="button" class="btn btn--primary btn--sm">下载选中文件</button>
      </div>
      <div class="download-table-wrap">
        <table class="download-table">
          <thead>
            <tr>
              <th class="col-check">选择</th>
              <th>文件名</th>
              <th>文件说明</th>
              <th class="col-size">大小</th>
              <th class="col-op">操作</th>
            </tr>
          </thead>
          <tbody>
            ${files.map((f) => `
              <tr>
                <td class="col-check"><input class="file-check" type="checkbox" data-file-key="${f.key}" /></td>
                <td title="${f.filename}"><span class="truncate">${f.filename}</span></td>
                <td title="${f.desc}"><span class="truncate">${f.desc}</span></td>
                <td class="col-size">${formatBytes(f.size_bytes)}</td>
                <td class="col-op"><button class="btn btn--ghost btn--sm" type="button" data-file-download="${f.key}">下载</button></td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    `;

    const fileMap = {};
    files.forEach((f) => { fileMap[f.key] = f; });
    const selectAllBtn = document.getElementById("selectAllFilesBtn");
    const downloadSelectedBtn = document.getElementById("downloadSelectedBtn");
    const checks = () => [...el.downloads.querySelectorAll(".file-check")];

    if (selectAllBtn) {
      selectAllBtn.addEventListener("click", () => {
        const allChecked = checks().every((c) => c.checked);
        checks().forEach((c) => { c.checked = !allChecked; });
        selectAllBtn.textContent = allChecked ? "全选" : "取消全选";
      });
    }

    if (downloadSelectedBtn) {
      downloadSelectedBtn.addEventListener("click", () => {
        const selected = checks().filter((c) => c.checked).map((c) => fileMap[c.dataset.fileKey]);
        if (!selected.length) {
          showToast("请先勾选要下载的文件。", "info");
          return;
        }
        selected.forEach((f) => triggerFileDownload(f.url, f.filename));
        showToast(`已开始下载 ${selected.length} 个文件。`, "success");
      });
    }

    el.downloads.querySelectorAll("[data-file-download]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.dataset.fileDownload;
        const target = fileMap[key];
        if (!target) return;
        triggerFileDownload(target.url, target.filename);
      });
    });
  }

  function updateProgress(data) {
    const phase = data.phase || "";
    const status = data.status || "idle";
    const videoRunning = data.video_status?.third_person === "running" || data.video_status?.fov === "running";
    const active = status === "running" || videoRunning;

    updateClock(active);
    if (phase !== state.lastPhase) {
      state.lastPhase = phase;
      state.phaseStartTs = Date.now();
    }

    const phaseElapsedSec = state.phaseStartTs ? (Date.now() - state.phaseStartTs) / 1000 : 0;
    const progress = calcProgress(data, phaseElapsedSec);
    const elapsed = toMMSS(getElapsedMs() / 1000);

    el.progressBar.classList.toggle("indeterminate", (status === "running" && (phase === "planning" || phase === "metrics")) || videoRunning);
    el.progressBar.style.width = `${progress}%`;

    const phaseText = PHASE_LABEL[phase] || "处理中";
    if (status === "running" && phase === "metrics") {
      const remainSec = Math.max(0, Math.floor(180 - phaseElapsedSec));
      el.progressMeta.textContent = `阶段：关键数据整理（估算 ${progress}% ，剩余约 ${remainSec}s） | 已用时：${elapsed}`;
    } else if (videoRunning) {
      el.progressMeta.textContent = `阶段：视频生成（总体 ${progress}%） | 已用时：${elapsed}`;
    } else {
      el.progressMeta.textContent = `阶段：${phaseText}（总体 ${progress}%） | 已用时：${elapsed}`;
    }

    setStatusBadges(status, phase);
  }

  function updateNextAction(data) {
    const status = data.status;
    const vs = data.video_status || {};
    if (status === "queued" || status === "idle") {
      setNextAction("请先上传 OBJ 文件并点击“开始运行”。");
      return;
    }
    if (status === "running") {
      setNextAction("系统正在处理中，请耐心等待。处理中不可修改参数。") ;
      return;
    }
    if (status === "failed") {
      setNextAction("任务失败。请检查参数或模型后重新运行。");
      return;
    }
    if (status === "canceled") {
      setNextAction("任务已终止。你可以调整参数后重新启动任务。");
      return;
    }
    if (vs.third_person === "running" || vs.fov === "running") {
      setNextAction("视频正在生成，请等待完成或点击“终止运行”停止当前生成。");
      return;
    }
    if (status === "done") {
      setNextAction("关键数据已就绪。可按需生成第三人称视频或 FOV 视频。");
    }
  }

  async function apiFetch(url, options = {}) {
    const resp = await fetch(url, options);
    let data = {};
    try {
      data = await resp.json();
    } catch (_e) {
      data = {};
    }
    if (!resp.ok || data.ok === false) throw new Error(data.error || `请求失败(${resp.status})`);
    return data;
  }

  async function pollStatus() {
    if (!state.jobId) return;
    try {
      const data = await apiFetch(`/api/status?job_id=${state.jobId}`);
      state.pollErrorNotified = false;

      state.lastStatus = data.status || "";
      el.statusLine.textContent = data.public_status || "系统正在处理，请稍候…";
      updateProgress(data);
      renderTimeline(data.messages || []);
      renderArtifacts(data);
      renderDownloads(data);
      updateNextAction(data);

      const videoRunning = data.video_status?.third_person === "running" || data.video_status?.fov === "running";
      const busy = data.status === "running" || videoRunning;
      setFormLocked(busy, busy ? "busy" : "idle");
      updateTrajectoryToggleState(data);

      if (data.status === "running") renderMetrics({}, true);
      else renderMetrics(data.metrics || {}, false);

      el.jobIdentity.textContent = `任务编号：${state.jobId}`;

      if ((data.status === "done" || data.status === "failed" || data.status === "canceled") && !videoRunning && !state.hasEndedToast) {
        state.hasEndedToast = true;
        if (data.status === "done") showToast("关键数据已生成，可按需生成视频。", "success");
        if (data.status === "failed") showToast(`任务失败：${data.error || "未知错误"}`, "error");
        if (data.status === "canceled") showToast("任务已终止。", "info");
      }

      if (data.warning) el.hint.textContent = data.warning;
    } catch (err) {
      el.statusLine.textContent = "状态更新失败，请稍后自动重试。";
      if (!state.pollErrorNotified) {
        showToast(err.message || "状态更新失败", "error");
        state.pollErrorNotified = true;
      }
    }
  }

  async function startJob(fd) {
    const data = await apiFetch("/api/start", { method: "POST", body: fd });
    state.jobId = data.job_id;
    state.lastStatus = "running";
    state.lastPhase = "";
    state.phaseStartTs = Date.now();
    state.activeStartTs = Date.now();
    state.totalActiveMs = 0;
    state.hasEndedToast = false;
    state.trajOverlayUrlLoaded = "";
    state.trajOverlayVisible = false;
    if (el.trajOverlayToggle) {
      el.trajOverlayToggle.checked = false;
      el.trajOverlayToggle.disabled = true;
    }
    if (objViewer) objViewer.setTrajectoryVisible(false);

    el.jobIdentity.textContent = `任务编号：${state.jobId}`;
    el.hint.textContent = data.warning || "";
    setFormLocked(true, "job-running");
    renderMetrics({}, true);
    renderTimeline(["任务已启动，正在准备中…"]);
    showToast("任务已启动。", "success");

    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = setInterval(pollStatus, 1200);
    await pollStatus();
  }

  async function cancelCurrent() {
    if (!state.jobId) return;
    try {
      await apiFetch("/api/cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: state.jobId })
      });
      showToast("正在终止当前处理…", "info");
    } catch (err) {
      showToast(err.message || "终止失败", "error");
    }
  }

  async function triggerVideo(videoType, button) {
    if (!state.jobId || state.lastStatus !== "done") return;
    const label = videoType === "third_person" ? "第三人称视频" : "FOV 视频";
    setButtonLoading(button, true, "生成中…", button.textContent);
    setFormLocked(true, "video-running");
    try {
      await apiFetch("/api/render_video", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: state.jobId, video_type: videoType })
      });
      showToast(`${label}任务已提交，正在生成。`, "info");
      await pollStatus();
    } catch (err) {
      setButtonLoading(button, false);
      setFormLocked(false, "idle");
      showToast(err.message || `${label}生成失败`, "error");
    }
  }

  // ---------- OBJ + MTL 交互渲染 ----------
  class ObjMaterialViewer {
    constructor(canvas) {
      this.canvas = canvas;
      this.gl = canvas.getContext("webgl", { antialias: true, alpha: false });
      this.meshes = [];
      this.textures = [];
      this.rotX = -0.35;
      this.rotY = 0.65;
      this.zoom = 3.0;
      this.bgMode = "dark";
      this.lightStrength = 1.0;
      this.trajVisible = false;
      this.trajPosBuf = null;
      this.trajCount = 0;
      this.dragging = false;
      this.lastX = 0;
      this.lastY = 0;
      this.raf = 0;

      if (!this.gl) throw new Error("当前浏览器不支持 WebGL 预览");
      this.initGL();
      this.bindEvents();
      this.resize();
    }

    initGL() {
      const gl = this.gl;
      const vs = `
        attribute vec3 aPos;
        attribute vec2 aUV;
        attribute vec3 aColor;
        attribute vec3 aNormal;
        uniform mat4 uMVP;
        uniform mat4 uModel;
        varying vec2 vUV;
        varying vec3 vColor;
        varying vec3 vNormal;
        void main() {
          gl_Position = uMVP * vec4(aPos, 1.0);
          vUV = aUV;
          vColor = aColor;
          vNormal = normalize((uModel * vec4(aNormal, 0.0)).xyz);
        }
      `;
      const fs = `
        precision mediump float;
        varying vec2 vUV;
        varying vec3 vColor;
        varying vec3 vNormal;
        uniform sampler2D uTex;
        uniform float uUseTex;
        uniform vec3 uLightDir;
        uniform float uLightStrength;
        void main() {
          vec4 base = vec4(vColor, 1.0);
          if (uUseTex > 0.5) {
            vec4 t = texture2D(uTex, vUV);
            base = mix(base, t, t.a);
          }
          float ndl = max(dot(normalize(vNormal), normalize(uLightDir)), 0.0);
          float lit = 0.28 + ndl * uLightStrength;
          gl_FragColor = vec4(base.rgb * lit, 1.0);
        }
      `;
      const program = this.createProgram(vs, fs);
      this.program = program;
      gl.useProgram(program);

      this.aPos = gl.getAttribLocation(program, "aPos");
      this.aUV = gl.getAttribLocation(program, "aUV");
      this.aColor = gl.getAttribLocation(program, "aColor");
      this.aNormal = gl.getAttribLocation(program, "aNormal");
      this.uMVP = gl.getUniformLocation(program, "uMVP");
      this.uModel = gl.getUniformLocation(program, "uModel");
      this.uUseTex = gl.getUniformLocation(program, "uUseTex");
      this.uTex = gl.getUniformLocation(program, "uTex");
      this.uLightDir = gl.getUniformLocation(program, "uLightDir");
      this.uLightStrength = gl.getUniformLocation(program, "uLightStrength");

      const lineVs = `
        attribute vec3 aPos;
        uniform mat4 uMVP;
        void main() {
          gl_Position = uMVP * vec4(aPos, 1.0);
          gl_PointSize = 4.0;
        }
      `;
      const lineFs = `
        precision mediump float;
        uniform vec4 uColor;
        void main() {
          gl_FragColor = uColor;
        }
      `;
      const lineProgram = this.createProgram(lineVs, lineFs);
      this.lineProgram = lineProgram;
      this.lineAPos = gl.getAttribLocation(lineProgram, "aPos");
      this.lineUMVP = gl.getUniformLocation(lineProgram, "uMVP");
      this.lineUColor = gl.getUniformLocation(lineProgram, "uColor");

      gl.enable(gl.DEPTH_TEST);
      gl.disable(gl.CULL_FACE);
    }

    createShader(type, src) {
      const gl = this.gl;
      const sh = gl.createShader(type);
      gl.shaderSource(sh, src);
      gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
        const log = gl.getShaderInfoLog(sh);
        gl.deleteShader(sh);
        throw new Error(log || "shader compile error");
      }
      return sh;
    }

    createProgram(vsSrc, fsSrc) {
      const gl = this.gl;
      const vs = this.createShader(gl.VERTEX_SHADER, vsSrc);
      const fs = this.createShader(gl.FRAGMENT_SHADER, fsSrc);
      const prog = gl.createProgram();
      gl.attachShader(prog, vs);
      gl.attachShader(prog, fs);
      gl.linkProgram(prog);
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
        const log = gl.getProgramInfoLog(prog);
        throw new Error(log || "program link error");
      }
      return prog;
    }

    bindEvents() {
      this.canvas.addEventListener("mousedown", (e) => {
        this.dragging = true;
        this.lastX = e.clientX;
        this.lastY = e.clientY;
      });
      window.addEventListener("mouseup", () => { this.dragging = false; });
      window.addEventListener("mousemove", (e) => {
        if (!this.dragging || !this.meshes.length) return;
        const dx = e.clientX - this.lastX;
        const dy = e.clientY - this.lastY;
        this.lastX = e.clientX;
        this.lastY = e.clientY;
        this.rotY += dx * 0.008;
        this.rotX -= dy * 0.008;
        this.requestRender();
      });
      this.canvas.addEventListener("wheel", (e) => {
        if (!this.meshes.length) return;
        e.preventDefault();
        const delta = Math.sign(e.deltaY);
        this.zoom += delta * 0.18;
        this.zoom = Math.max(1.2, Math.min(10, this.zoom));
        this.requestRender();
      }, { passive: false });
      window.addEventListener("resize", () => this.resize());
    }

    resize() {
      const rect = this.canvas.getBoundingClientRect();
      const dpr = Math.max(1, window.devicePixelRatio || 1);
      const w = Math.max(10, Math.floor(rect.width * dpr));
      const h = Math.max(10, Math.floor(rect.height * dpr));
      if (this.canvas.width !== w || this.canvas.height !== h) {
        this.canvas.width = w;
        this.canvas.height = h;
      }
      this.requestRender();
    }

    resetView() {
      this.rotX = -0.35;
      this.rotY = 0.65;
      this.zoom = 3.0;
      this.requestRender();
    }

    setBackgroundMode(mode) {
      this.bgMode = mode || "dark";
      this.requestRender();
    }

    setLightStrength(value) {
      const n = Number(value);
      if (Number.isFinite(n)) {
        this.lightStrength = Math.max(0.2, Math.min(2.0, n));
        this.requestRender();
      }
    }

    clearGLData() {
      const gl = this.gl;
      this.meshes.forEach((m) => {
        if (m.posBuf) gl.deleteBuffer(m.posBuf);
        if (m.uvBuf) gl.deleteBuffer(m.uvBuf);
        if (m.colBuf) gl.deleteBuffer(m.colBuf);
        if (m.norBuf) gl.deleteBuffer(m.norBuf);
      });
      this.textures.forEach((t) => gl.deleteTexture(t));
      this.meshes = [];
      this.textures = [];
      if (this.trajPosBuf) {
        gl.deleteBuffer(this.trajPosBuf);
        this.trajPosBuf = null;
      }
      this.trajCount = 0;
    }

    setTrajectory(points) {
      const gl = this.gl;
      if (this.trajPosBuf) {
        gl.deleteBuffer(this.trajPosBuf);
        this.trajPosBuf = null;
      }
      this.trajCount = 0;
      if (!points || points.length < 2) {
        this.requestRender();
        return;
      }
      const flat = new Float32Array(points.length * 3);
      for (let i = 0; i < points.length; i++) {
        flat[i * 3] = points[i][0];
        flat[i * 3 + 1] = points[i][1];
        flat[i * 3 + 2] = points[i][2];
      }
      this.trajPosBuf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, this.trajPosBuf);
      gl.bufferData(gl.ARRAY_BUFFER, flat, gl.STATIC_DRAW);
      this.trajCount = points.length;
      this.requestRender();
    }

    setTrajectoryVisible(visible) {
      this.trajVisible = !!visible;
      this.requestRender();
    }

    async setModel(groups, textureFileMap) {
      this.clearGLData();
      const gl = this.gl;
      const loadedTex = new Map();

      for (let i = 0; i < groups.length; i++) {
        const g = groups[i];
        if (!g.positions.length) continue;

        const posBuf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, posBuf);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(g.positions), gl.STATIC_DRAW);

        const uvBuf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, uvBuf);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(g.uvs), gl.STATIC_DRAW);

        const colBuf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, colBuf);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(g.colors), gl.STATIC_DRAW);

        const norBuf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, norBuf);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(g.normals), gl.STATIC_DRAW);

        let texture = null;
        if (g.textureKey && textureFileMap[g.textureKey]) {
          if (loadedTex.has(g.textureKey)) {
            texture = loadedTex.get(g.textureKey);
          } else {
            texture = await this.loadTextureFromFile(textureFileMap[g.textureKey]);
            loadedTex.set(g.textureKey, texture);
            this.textures.push(texture);
          }
        }

        this.meshes.push({
          posBuf,
          uvBuf,
          colBuf,
          norBuf,
          count: g.positions.length / 3,
          texture
        });
      }

      this.resetView();
    }

    loadTextureFromFile(file) {
      return new Promise((resolve, reject) => {
        const gl = this.gl;
        const img = new Image();
        const url = URL.createObjectURL(file);
        img.onload = () => {
          const tex = gl.createTexture();
          gl.bindTexture(gl.TEXTURE_2D, tex);
          const isPOT = (v) => (v & (v - 1)) === 0;
          const pot = isPOT(img.width) && isPOT(img.height);
          let source = img;
          if (!pot) {
            const can = document.createElement("canvas");
            can.width = nextPow2(img.width);
            can.height = nextPow2(img.height);
            const ctx = can.getContext("2d");
            ctx.drawImage(img, 0, 0, can.width, can.height);
            source = can;
          }
          // 统一采用 REPEAT + mipmap，避免 NPOT 贴图被 CLAMP 造成纹理观感错位。
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
          gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, 1);
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
          gl.generateMipmap(gl.TEXTURE_2D);
          URL.revokeObjectURL(url);
          resolve(tex);
        };
        img.onerror = () => {
          URL.revokeObjectURL(url);
          reject(new Error(`纹理加载失败: ${file.name}`));
        };
        img.src = url;
      });
    }

    requestRender() {
      if (this.raf) return;
      this.raf = requestAnimationFrame(() => {
        this.raf = 0;
        this.render();
      });
    }

    render() {
      const gl = this.gl;
      const w = this.canvas.width;
      const h = this.canvas.height;
      if (!gl || w <= 0 || h <= 0) return;

      gl.viewport(0, 0, w, h);
      if (this.bgMode === "light") gl.clearColor(0.93, 0.96, 0.99, 1.0);
      else if (this.bgMode === "blue") gl.clearColor(0.40, 0.50, 0.66, 1.0);
      else gl.clearColor(0.07, 0.12, 0.24, 1.0);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      if (!this.meshes.length) return;

      const proj = matPerspective(Math.PI / 3.2, w / h, 0.1, 100.0);
      const view = matTranslate(0, 0, -this.zoom);
      const model = matMul(matRotateY(this.rotY), matRotateX(this.rotX));
      const mvp = matMul(proj, matMul(view, model));

      gl.useProgram(this.program);
      gl.uniformMatrix4fv(this.uMVP, false, mvp);
      gl.uniformMatrix4fv(this.uModel, false, model);
      gl.uniform1i(this.uTex, 0);
      gl.uniform3f(this.uLightDir, -0.35, 0.75, 0.55);
      gl.uniform1f(this.uLightStrength, this.lightStrength);

      this.meshes.forEach((m) => {
        gl.bindBuffer(gl.ARRAY_BUFFER, m.posBuf);
        gl.enableVertexAttribArray(this.aPos);
        gl.vertexAttribPointer(this.aPos, 3, gl.FLOAT, false, 0, 0);

        gl.bindBuffer(gl.ARRAY_BUFFER, m.uvBuf);
        gl.enableVertexAttribArray(this.aUV);
        gl.vertexAttribPointer(this.aUV, 2, gl.FLOAT, false, 0, 0);

        gl.bindBuffer(gl.ARRAY_BUFFER, m.colBuf);
        gl.enableVertexAttribArray(this.aColor);
        gl.vertexAttribPointer(this.aColor, 3, gl.FLOAT, false, 0, 0);

        gl.bindBuffer(gl.ARRAY_BUFFER, m.norBuf);
        gl.enableVertexAttribArray(this.aNormal);
        gl.vertexAttribPointer(this.aNormal, 3, gl.FLOAT, false, 0, 0);

        if (m.texture) {
          gl.activeTexture(gl.TEXTURE0);
          gl.bindTexture(gl.TEXTURE_2D, m.texture);
          gl.uniform1f(this.uUseTex, 1.0);
        } else {
          gl.bindTexture(gl.TEXTURE_2D, null);
          gl.uniform1f(this.uUseTex, 0.0);
        }
        gl.drawArrays(gl.TRIANGLES, 0, m.count);
      });

      if (this.trajVisible && this.trajPosBuf && this.trajCount > 1) {
        gl.useProgram(this.lineProgram);
        gl.uniformMatrix4fv(this.lineUMVP, false, mvp);
        gl.bindBuffer(gl.ARRAY_BUFFER, this.trajPosBuf);
        gl.enableVertexAttribArray(this.lineAPos);
        gl.vertexAttribPointer(this.lineAPos, 3, gl.FLOAT, false, 0, 0);
        gl.uniform4f(this.lineUColor, 1.0, 0.33, 0.95, 1.0);
        gl.drawArrays(gl.LINE_STRIP, 0, this.trajCount);
        gl.uniform4f(this.lineUColor, 0.20, 0.95, 1.0, 1.0);
        gl.drawArrays(gl.POINTS, 0, this.trajCount);
      }
    }

    clear() {
      this.clearGLData();
      this.trajVisible = false;
      this.requestRender();
    }
  }

  function matMul(a, b) {
    const out = new Float32Array(16);
    for (let r = 0; r < 4; r++) {
      for (let c = 0; c < 4; c++) {
        out[c * 4 + r] =
          a[0 * 4 + r] * b[c * 4 + 0] +
          a[1 * 4 + r] * b[c * 4 + 1] +
          a[2 * 4 + r] * b[c * 4 + 2] +
          a[3 * 4 + r] * b[c * 4 + 3];
      }
    }
    return out;
  }

  function matPerspective(fovy, aspect, near, far) {
    const f = 1.0 / Math.tan(fovy / 2);
    const nf = 1 / (near - far);
    const out = new Float32Array(16);
    out[0] = f / aspect;
    out[5] = f;
    out[10] = (far + near) * nf;
    out[11] = -1;
    out[14] = 2 * far * near * nf;
    return out;
  }

  function parseTrajectoryText(text, normalize) {
    const out = [];
    if (!text || !normalize) return out;
    const re = /X:\s*([-+\d.eE]+)\s*,\s*Y:\s*([-+\d.eE]+)\s*,\s*Z:\s*([-+\d.eE]+)/;
    const lines = text.split(/\r?\n/);
    for (let i = 0; i < lines.length; i++) {
      const m = lines[i].match(re);
      if (!m) continue;
      const x = Number(m[1]);
      const y = Number(m[2]);
      const z = Number(m[3]);
      if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) continue;
      out.push([
        (x - normalize.cx) * normalize.sc,
        (y - normalize.cy) * normalize.sc,
        (z - normalize.cz) * normalize.sc
      ]);
    }
    return out;
  }

  function getTrajInfoUrlFromStatus(data) {
    const files = data?.result_files || [];
    const hit = files.find((f) => f && f.key === "traj_info" && f.url);
    return hit ? hit.url : "";
  }

  function matRotateX(a) {
    const c = Math.cos(a);
    const s = Math.sin(a);
    return new Float32Array([
      1, 0, 0, 0,
      0, c, s, 0,
      0, -s, c, 0,
      0, 0, 0, 1
    ]);
  }

  function matRotateY(a) {
    const c = Math.cos(a);
    const s = Math.sin(a);
    return new Float32Array([
      c, 0, -s, 0,
      0, 1, 0, 0,
      s, 0, c, 0,
      0, 0, 0, 1
    ]);
  }

  function matTranslate(x, y, z) {
    return new Float32Array([
      1, 0, 0, 0,
      0, 1, 0, 0,
      0, 0, 1, 0,
      x, y, z, 1
    ]);
  }

  function nextPow2(v) {
    let n = 1;
    while (n < v) n <<= 1;
    return n;
  }

  let objViewer = null;

  function normalizePath(p) {
    return (p || "").replace(/\\/g, "/").replace(/^\.\//, "").trim();
  }

  function parseMtl(text) {
    const materials = {};
    let current = null;
    const lines = text.split(/\r?\n/);
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line || line.startsWith("#")) continue;
      if (line.startsWith("newmtl ")) {
        const name = line.substring(7).trim();
        current = { kd: [0.72, 0.72, 0.74], mapKd: "" };
        materials[name] = current;
      } else if (current && line.startsWith("Kd ")) {
        const p = line.split(/\s+/);
        if (p.length >= 4) {
          current.kd = [Number(p[1]) || 0.72, Number(p[2]) || 0.72, Number(p[3]) || 0.74];
        }
      } else if (current && line.startsWith("map_Kd ")) {
        const v = line.substring(7).trim();
        // map_Kd 可能包含 -s/-o/-clamp 等参数，这里只提取最终贴图路径
        const t = v.match(/(?:[^\s"]+|"[^"]*")+/g) || [];
        const filtered = t.filter((x) => !x.startsWith("-"));
        const last = (filtered[filtered.length - 1] || "").replace(/^"(.*)"$/, "$1");
        current.mapKd = normalizePath(last);
      }
    }
    return materials;
  }

  function parseObjWithMaterials(text, materials) {
    const pos = [];
    const uv = [];
    const groups = new Map();
    let current = "__default__";

    const ensure = (name) => {
      if (!groups.has(name)) {
        const m = materials[name] || { kd: [0.72, 0.72, 0.74], mapKd: "" };
        groups.set(name, { positions: [], uvs: [], colors: [], normals: [], texturePath: m.mapKd || "", kd: m.kd || [0.72, 0.72, 0.74] });
      }
      return groups.get(name);
    };

    const lines = text.split(/\r?\n/);
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();
      if (!line || line.startsWith("#")) continue;
      if (line.startsWith("v ")) {
        const p = line.split(/\s+/);
        if (p.length >= 4) pos.push([Number(p[1]) || 0, Number(p[2]) || 0, Number(p[3]) || 0]);
      } else if (line.startsWith("vt ")) {
        const p = line.split(/\s+/);
        // V 翻转由 UNPACK_FLIP_Y_WEBGL 处理，这里保持 OBJ 原始 UV。
        if (p.length >= 3) uv.push([Number(p[1]) || 0, Number(p[2]) || 0]);
      } else if (line.startsWith("usemtl ")) {
        current = line.substring(7).trim() || "__default__";
      } else if (line.startsWith("f ")) {
        const group = ensure(current);
        const tokens = line.split(/\s+/).slice(1);
        if (tokens.length < 3) continue;

        const verts = [];
        for (let j = 0; j < tokens.length; j++) {
          const t = tokens[j];
          const a = t.split("/");
          let vi = Number(a[0]);
          let ti = a.length > 1 && a[1] ? Number(a[1]) : 0;
          if (!Number.isInteger(vi) || vi === 0) continue;
          if (vi < 0) vi = pos.length + vi + 1;
          if (ti < 0) ti = uv.length + ti + 1;
          const vp = pos[vi - 1];
          if (!vp) continue;
          const vuv = ti > 0 ? uv[ti - 1] : null;
          verts.push({ p: vp, uv: vuv || [0, 0] });
        }

        for (let j = 1; j + 1 < verts.length; j++) {
          const a = verts[0].p;
          const b = verts[j].p;
          const c = verts[j + 1].p;
          const ux = b[0] - a[0];
          const uy = b[1] - a[1];
          const uz = b[2] - a[2];
          const vx = c[0] - a[0];
          const vy = c[1] - a[1];
          const vz = c[2] - a[2];
          let nx = uy * vz - uz * vy;
          let ny = uz * vx - ux * vz;
          let nz = ux * vy - uy * vx;
          const nl = Math.hypot(nx, ny, nz) || 1.0;
          nx /= nl;
          ny /= nl;
          nz /= nl;

          [verts[0], verts[j], verts[j + 1]].forEach((v) => {
            group.positions.push(v.p[0], v.p[1], v.p[2]);
            group.uvs.push(v.uv[0], v.uv[1]);
            group.colors.push(group.kd[0], group.kd[1], group.kd[2]);
            group.normals.push(nx, ny, nz);
          });
        }
      }
    }

    const allPos = [];
    groups.forEach((g) => {
      for (let i = 0; i < g.positions.length; i += 3) {
        allPos.push([g.positions[i], g.positions[i + 1], g.positions[i + 2]]);
      }
    });
    if (!allPos.length) throw new Error("OBJ 中未检测到可渲染的网格数据");

    let minX = Infinity; let minY = Infinity; let minZ = Infinity;
    let maxX = -Infinity; let maxY = -Infinity; let maxZ = -Infinity;
    allPos.forEach((p) => {
      minX = Math.min(minX, p[0]); minY = Math.min(minY, p[1]); minZ = Math.min(minZ, p[2]);
      maxX = Math.max(maxX, p[0]); maxY = Math.max(maxY, p[1]); maxZ = Math.max(maxZ, p[2]);
    });

    // 预览旋转中心使用网格表面的面积加权几何中心（比包围盒中心更接近真实“模型中心”）
    let areaSum = 0.0;
    let cxSum = 0.0;
    let cySum = 0.0;
    let czSum = 0.0;
    groups.forEach((g) => {
      for (let i = 0; i + 8 < g.positions.length; i += 9) {
        const ax = g.positions[i];
        const ay = g.positions[i + 1];
        const az = g.positions[i + 2];
        const bx = g.positions[i + 3];
        const by = g.positions[i + 4];
        const bz = g.positions[i + 5];
        const cx = g.positions[i + 6];
        const cy = g.positions[i + 7];
        const cz = g.positions[i + 8];

        const ux = bx - ax;
        const uy = by - ay;
        const uz = bz - az;
        const vx = cx - ax;
        const vy = cy - ay;
        const vz = cz - az;

        const nx = uy * vz - uz * vy;
        const ny = uz * vx - ux * vz;
        const nz = ux * vy - uy * vx;
        const area = 0.5 * Math.hypot(nx, ny, nz);
        if (area <= 1e-12) continue;

        const tcx = (ax + bx + cx) / 3.0;
        const tcy = (ay + by + cy) / 3.0;
        const tcz = (az + bz + cz) / 3.0;
        cxSum += tcx * area;
        cySum += tcy * area;
        czSum += tcz * area;
        areaSum += area;
      }
    });

    const bboxCx = (minX + maxX) * 0.5;
    const bboxCy = (minY + maxY) * 0.5;
    const bboxCz = (minZ + maxZ) * 0.5;
    const cx = areaSum > 1e-12 ? (cxSum / areaSum) : bboxCx;
    const cy = areaSum > 1e-12 ? (cySum / areaSum) : bboxCy;
    const cz = areaSum > 1e-12 ? (czSum / areaSum) : bboxCz;

    const span = Math.max(1e-6, maxX - minX, maxY - minY, maxZ - minZ);
    const sc = 2.0 / span;

    const out = [];
    groups.forEach((g, name) => {
      for (let i = 0; i < g.positions.length; i += 3) {
        g.positions[i] = (g.positions[i] - cx) * sc;
        g.positions[i + 1] = (g.positions[i + 1] - cy) * sc;
        g.positions[i + 2] = (g.positions[i + 2] - cz) * sc;
      }
      out.push({
        name,
        positions: g.positions,
        uvs: g.uvs,
        colors: g.colors,
        normals: g.normals,
        texturePath: g.texturePath
      });
    });
    return {
      groups: out,
      normalize: { cx, cy, cz, sc }
    };
  }

  function collectAllAssetFiles() {
    return [
      ...(el.assetFilesInput.files ? Array.from(el.assetFilesInput.files) : []),
      ...(el.assetDirInput.files ? Array.from(el.assetDirInput.files) : [])
    ];
  }

  function buildFileLookup(objFile, assets) {
    const lookup = {};
    const put = (k, f) => {
      const key = normalizePath(k).toLowerCase();
      if (!key) return;
      if (!lookup[key]) lookup[key] = f;
    };

    if (objFile) put(objFile.name, objFile);
    assets.forEach((f) => {
      put(f.name, f);
      if (f.webkitRelativePath) put(f.webkitRelativePath, f);
      const bn = normalizePath(f.name).split("/").pop();
      put(bn, f);
    });
    return lookup;
  }

  function resolveTextureKey(path, lookup) {
    const norm = normalizePath(path).toLowerCase();
    if (!norm) return "";
    if (lookup[norm]) return norm;
    const bn = norm.split("/").pop();
    if (lookup[bn]) return bn;
    return "";
  }

  async function loadMtlMaterials(objText, lookup) {
    const mtllibs = [];
    objText.split(/\r?\n/).forEach((line) => {
      const t = line.trim();
      if (t.startsWith("mtllib ")) {
        t.substring(7).trim().split(/\s+/).forEach((n) => { if (n) mtllibs.push(normalizePath(n)); });
      }
    });

    let mtlFile = null;
    for (let i = 0; i < mtllibs.length; i++) {
      const key = resolveTextureKey(mtllibs[i], lookup);
      if (key && lookup[key]) {
        mtlFile = lookup[key];
        break;
      }
    }

    if (!mtlFile) {
      const anyMtl = Object.keys(lookup).find((k) => k.endsWith(".mtl"));
      if (anyMtl) mtlFile = lookup[anyMtl];
    }

    if (!mtlFile) return {};
    const mtlText = await mtlFile.text();
    return parseMtl(mtlText);
  }

  async function syncTrajectoryOverlayFromStatus(data) {
    if (!objViewer || !state.objNormalize) return;
    const trajUrl = getTrajInfoUrlFromStatus(data);
    const enabled = !!(el.trajOverlayToggle && el.trajOverlayToggle.checked);
    if (!trajUrl || !enabled) {
      objViewer.setTrajectoryVisible(false);
      return;
    }
    if (state.trajOverlayUrlLoaded === trajUrl) {
      objViewer.setTrajectoryVisible(true);
      return;
    }
    const resp = await fetch(trajUrl);
    if (!resp.ok) throw new Error("轨迹文件加载失败");
    const txt = await resp.text();
    const points = parseTrajectoryText(txt, state.objNormalize);
    objViewer.setTrajectory(points);
    objViewer.setTrajectoryVisible(true);
    state.trajOverlayUrlLoaded = trajUrl;
  }

  function updateTrajectoryToggleState(data) {
    if (!el.trajOverlayToggle) return;
    const trajUrl = getTrajInfoUrlFromStatus(data);
    const ready = !!(trajUrl && objViewer && state.objNormalize);
    el.trajOverlayToggle.disabled = !ready || state.lockUI;
    if (!ready) {
      state.trajOverlayVisible = false;
      el.trajOverlayToggle.checked = false;
      if (objViewer) objViewer.setTrajectoryVisible(false);
      return;
    }
    if (state.trajOverlayVisible && el.trajOverlayToggle.checked) {
      syncTrajectoryOverlayFromStatus(data).catch((err) => {
        showToast(err.message || "轨迹加载失败", "error");
      });
    }
  }

  async function requestInteractivePreview() {
    const obj = el.objFileInput.files && el.objFileInput.files[0];
    if (!obj) {
      if (objViewer) objViewer.clear();
      state.objNormalize = null;
      state.trajOverlayUrlLoaded = "";
      state.trajOverlayVisible = false;
      el.objPreviewEmpty.style.display = "grid";
      setSimpleBadge(el.objPreviewBadge, "未加载", "badge--idle");
      el.objPreviewMeta.textContent = "操作提示：按住鼠标左键拖拽旋转，滚轮缩放。上传 MTL/材质后会显示材质效果。";
      el.resetObjViewBtn.disabled = true;
      if (el.trajOverlayToggle) {
        el.trajOverlayToggle.checked = false;
        el.trajOverlayToggle.disabled = true;
      }
      return;
    }

    const reqId = ++state.objPreviewSeq;
    setSimpleBadge(el.objPreviewBadge, "加载中", "badge--running");
    el.objPreviewEmpty.style.display = "grid";
    el.objPreviewEmpty.innerHTML = '<div class="spinner-line">正在构建模型预览，请稍候…</div>';
    el.objPreviewMeta.textContent = "正在解析 OBJ/MTL/材质资源…";
    el.resetObjViewBtn.disabled = true;

    try {
      const objText = await obj.text();
      if (reqId !== state.objPreviewSeq) return;

      const assets = collectAllAssetFiles();
      const lookup = buildFileLookup(obj, assets);
      const materials = await loadMtlMaterials(objText, lookup);
      if (reqId !== state.objPreviewSeq) return;

      const parsedRaw = parseObjWithMaterials(objText, materials);
      const parsed = Array.isArray(parsedRaw)
        ? { groups: parsedRaw, normalize: null }
        : (parsedRaw || { groups: [], normalize: null });
      const groups = (parsed.groups || []).map((g) => ({
        ...g,
        textureKey: resolveTextureKey(g.texturePath, lookup)
      }));

      if (!objViewer) objViewer = new ObjMaterialViewer(el.objPreviewCanvas);
      objViewer.setBackgroundMode(el.objBgSelect.value || "dark");
      objViewer.setLightStrength(el.objLightRange.value || "1.0");
      await objViewer.setModel(groups, lookup);
      state.objNormalize = parsed.normalize;
      state.trajOverlayUrlLoaded = "";
      objViewer.setTrajectory([]);
      objViewer.setTrajectoryVisible(false);
      if (reqId !== state.objPreviewSeq) return;

      el.objPreviewEmpty.style.display = "none";
      setSimpleBadge(el.objPreviewBadge, "可交互", "badge--success");
      el.objPreviewMeta.textContent = Object.keys(materials).length
        ? "模型与材质已加载，可拖拽旋转查看；任务完成后可开启轨迹高亮。"
        : "模型已加载（未检测到可用 MTL 材质），可拖拽旋转查看；任务完成后可开启轨迹高亮。";
      el.resetObjViewBtn.disabled = false;
      updateTrajectoryToggleState({ result_files: [] });
    } catch (err) {
      if (reqId !== state.objPreviewSeq) return;
      if (objViewer) objViewer.clear();
      state.objNormalize = null;
      state.trajOverlayUrlLoaded = "";
      state.trajOverlayVisible = false;
      setSimpleBadge(el.objPreviewBadge, "失败", "badge--danger");
      el.objPreviewEmpty.style.display = "grid";
      el.objPreviewEmpty.innerHTML = "模型预览加载失败，请检查 OBJ/MTL/贴图文件。";
      el.objPreviewMeta.textContent = "请确认模型文件完整后重试。";
      el.resetObjViewBtn.disabled = true;
      if (el.trajOverlayToggle) {
        el.trajOverlayToggle.checked = false;
        el.trajOverlayToggle.disabled = true;
      }
      showToast(err.message || "模型预览加载失败", "error");
    }
  }

  function validateBeforeStart(fd) {
    const obj = fd.get("obj_file");
    const sceneName = (fd.get("scene_name") || "").toString().trim();
    let valid = true;
    if (!obj || !obj.name) {
      setFieldError(el.objFileInput, el.objError, "请先选择 OBJ 文件");
      valid = false;
    } else {
      setFieldError(el.objFileInput, el.objError, "");
    }
    if (!sceneName) {
      el.sceneNameInput.classList.add("input--invalid");
      showToast("场景名称不能为空", "error");
      valid = false;
    } else {
      el.sceneNameInput.classList.remove("input--invalid");
    }
    return valid;
  }

  function bindEvents() {
    el.objFileInput.addEventListener("change", () => {
      const file = el.objFileInput.files && el.objFileInput.files[0];
      updatePickerCaption(el.objFileInput, el.objFileName, "未选择文件");
      if (!file) {
        requestInteractivePreview();
        return;
      }
      const baseName = file.name.replace(/\.[^.]+$/, "");
      if (baseName) el.sceneNameInput.value = baseName;
      setFieldError(el.objFileInput, el.objError, "");
      requestInteractivePreview();
    });

    el.assetFilesInput.addEventListener("change", () => {
      updatePickerCaption(el.assetFilesInput, el.assetFilesName, "未选择文件");
      if (el.objFileInput.files && el.objFileInput.files[0]) requestInteractivePreview();
    });

    el.assetDirInput.addEventListener("change", () => {
      updatePickerCaption(el.assetDirInput, el.assetDirName, "未选择文件夹");
      if (el.objFileInput.files && el.objFileInput.files[0]) requestInteractivePreview();
    });

    el.form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (state.lockUI) {
        showToast("当前正在处理，请等待完成或点击终止。", "info");
        return;
      }

      el.hint.textContent = "";
      const fd = new FormData(el.form);
      if (!validateBeforeStart(fd)) return;

      const hasAssets = (fd.getAll("asset_files") || []).some((f) => f && f.name)
        || (fd.getAll("asset_dir_files") || []).some((f) => f && f.name);
      if (!hasAssets) {
        el.hint.textContent = "提示：未上传材质文件，可能影响 FOV 视频的显示效果。";
      }

      setButtonLoading(el.startBtn, true, "启动中…", "开始运行");
      try {
        await startJob(fd);
      } catch (err) {
        showToast(err.message || "启动失败", "error");
        setFormLocked(false, "idle");
      } finally {
        setButtonLoading(el.startBtn, false, "", "开始运行");
      }
    });

    el.stopBtn.addEventListener("click", cancelCurrent);
    el.resetObjViewBtn.addEventListener("click", () => {
      if (objViewer) objViewer.resetView();
    });
    el.objBgSelect.addEventListener("change", () => {
      if (objViewer) objViewer.setBackgroundMode(el.objBgSelect.value);
    });
    el.objLightRange.addEventListener("input", () => {
      if (objViewer) objViewer.setLightStrength(el.objLightRange.value);
    });
    el.trajOverlayToggle.addEventListener("change", async () => {
      state.trajOverlayVisible = !!el.trajOverlayToggle.checked;
      if (!objViewer) return;
      if (!state.trajOverlayVisible) {
        objViewer.setTrajectoryVisible(false);
        return;
      }
      try {
        const data = await apiFetch(`/api/status?job_id=${state.jobId}`);
        await syncTrajectoryOverlayFromStatus(data);
      } catch (err) {
        el.trajOverlayToggle.checked = false;
        state.trajOverlayVisible = false;
        objViewer.setTrajectoryVisible(false);
        showToast(err.message || "轨迹加载失败", "error");
      }
    });

    el.form.querySelectorAll(".input").forEach((input) => {
      input.addEventListener("input", () => input.classList.remove("input--invalid"));
    });
  }

  function init() {
    renderTimeline([]);
    renderMetrics({}, false);
    renderArtifacts({ status: "idle", video_status: { third_person: "idle", fov: "idle" } });
    renderDownloads({ status: "idle", result_files: [] });
    updatePickerCaption(el.objFileInput, el.objFileName, "未选择文件");
    updatePickerCaption(el.assetFilesInput, el.assetFilesName, "未选择文件");
    updatePickerCaption(el.assetDirInput, el.assetDirName, "未选择文件夹");
    ensureFieldHelpSlots();
    setSimpleBadge(el.objPreviewBadge, "未加载", "badge--idle");
    el.objPreviewMeta.textContent = "操作提示：按住鼠标左键拖拽旋转，滚轮缩放。上传 MTL/材质后会显示材质效果。";
    el.trajOverlayToggle.checked = false;
    el.trajOverlayToggle.disabled = true;
    setStatusBadges("idle", "");
    setNextAction("请先上传 OBJ 文件并点击“开始运行”。");
    bindEvents();
  }

  init();
})();
