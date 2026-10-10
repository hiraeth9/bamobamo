/* ==========================================================================
   main.js — 白膜预演工作台前端逻辑
   职责：
   1. 提示词输入 → 提交任务到后端
   2. 轮询任务状态 → 渲染 5 步进度 + 帧号跳动
   3. 加载成品视频列表 → 回放
   后端接口契约见 README。未连接后端时自动降级为演示模式。
   ========================================================================== */

const API_BASE = ""; // 同源后端，留空；如后端独立部署可填 http://host:port

const STEPS = [
  { name: "解析提示词", desc: "理解分镜意图" },
  { name: "生成分镜 scene.json", desc: "角色 / 动作 / 相机轨道" },
  { name: "Blender 建场景", desc: "加载角色 + 编排动作" },
  { name: "渲染帧序列", desc: "" },
  { name: "合成视频", desc: "ffmpeg → MP4" },
];

const state = {
  running: false,
  taskId: null,
  pollTimer: null,
};

/* ---------- DOM 引用 ---------- */
const $ = (sel) => document.querySelector(sel);
const promptInput = $("#promptInput");
const genBtn = $("#genBtn");
const resSelect = $("#resSelect");
const fpsSelect = $("#fpsSelect");
const durInput = $("#durInput");
const chipRow = $("#chipRow");
const pipeline = $("#pipeline");
const progressFill = $("#progressFill");
const progressPct = $("#progressPct");
const progressTime = $("#progressTime");
const progressBadge = $("#progressBadge");
const frameTicker = $("#frameTicker");
const logConsole = $("#logConsole");
const videoGrid = $("#videoGrid");
const videoCount = $("#videoCount");
const statusDot = $("#statusDot");
const statusText = $("#statusText");

/* ---------- 入场序列 ---------- */
document.querySelectorAll(".reveal").forEach((el, i) => {
  el.style.transitionDelay = `${i * 80}ms`;
  requestAnimationFrame(() => el.classList.add("in"));
});

/* ---------- 日志 ---------- */
function log(msg, cls = "") {
  const line = document.createElement("div");
  line.className = `log-line ${cls}`;
  line.textContent = `[${new Date().toLocaleTimeString("zh-CN", { hour12: false })}] ${msg}`;
  logConsole.appendChild(line);
  logConsole.scrollTop = logConsole.scrollHeight;
}

/* ---------- 状态显示 ---------- */
function setStatus(kind, text) {
  statusText.textContent = text;
  statusDot.className = "status-dot";
  if (kind === "busy") statusDot.classList.add("busy");
  if (kind === "live") statusDot.classList.add("live");
}

/* ---------- 进度渲染 ---------- */
function renderProgress(task) {
  // task: { stage: 0..5, total_frames, current_frame, percent, message }
  const stage = Math.min(task.stage, 5);
  const steps = pipeline.querySelectorAll(".step");
  steps.forEach((el, i) => {
    el.classList.remove("active", "done", "error");
    if (i < stage) el.classList.add("done");
    else if (i === stage) el.classList.add("active");
  });

  // 帧号跳动（第 4 步渲染时）
  if (task.stage === 3 && task.current_frame != null) {
    frameTicker.textContent = `${task.current_frame} / ${task.total_frames}`;
  } else if (task.stage > 3) {
    frameTicker.textContent = `${task.total_frames} 帧`;
  }

  const pct = task.percent ?? Math.min(100, (stage / 5) * 100);
  progressFill.style.width = `${pct}%`;
  progressPct.textContent = `${Math.round(pct)}%`;
  progressBadge.textContent = task.message || STEPS[Math.min(stage, 4)].name;

  if (task.elapsed != null) {
    progressTime.textContent = `${task.elapsed.toFixed(1)}s`;
  }
}

function resetProgress() {
  pipeline.querySelectorAll(".step").forEach((el) => el.classList.remove("active", "done", "error"));
  progressFill.style.width = "0%";
  progressPct.textContent = "0%";
  progressTime.textContent = "—";
  progressBadge.textContent = "就绪";
  frameTicker.textContent = "—";
}

/* ---------- 提交任务 ---------- */
async function submitTask(prompt, res, fps, duration) {
  const payload = { prompt, resolution: res, fps, duration };
  log(`提交任务: ${prompt.slice(0, 30)}…`);
  try {
    const resp = await fetch(`${API_BASE}/api/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    return data.task_id;
  } catch (e) {
    // 降级：后端未连接时进入演示模式
    log("后端未连接，进入演示模式", "err");
    return demoSubmit(payload);
  }
}

/* ---------- 轮询任务 ---------- */
async function pollTask(taskId) {
  try {
    const resp = await fetch(`${API_BASE}/api/tasks/${taskId}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const task = await resp.json();
    return task;
  } catch (e) {
    return null; // 演示模式由 demo 状态机接管
  }
}

/* ---------- 演示模式（无后端时的完整模拟） ---------- */
let demoState = null;
function demoSubmit(payload) {
  const taskId = "demo-" + Date.now();
  demoState = {
    stage: 0,
    percent: 0,
    current_frame: 0,
    total_frames: Math.round(payload.duration * payload.fps),
    elapsed: 0,
    message: "",
  };
  log("演示模式已启动（未连接后端）");
  runDemoLoop();
  return taskId;
}

function runDemoLoop() {
  const total = demoState.total_frames;
  state.pollTimer = setInterval(() => {
    demoState.elapsed += 0.2;
    if (demoState.stage === 0) {
      demoState.stage = 1;
      demoState.percent = 12;
      demoState.message = "生成 scene.json…";
      log("分镜脚本已生成", "ok");
    } else if (demoState.stage === 1) {
      demoState.stage = 2;
      demoState.percent = 30;
      demoState.message = "Blender 建场景…";
      log("场景构建中：加载角色 + 编排动作", "ok");
    } else if (demoState.stage === 2) {
      demoState.stage = 3;
      demoState.percent = 38;
      demoState.message = "渲染中…";
      log("开始渲染帧序列", "ok");
    } else if (demoState.stage === 3) {
      demoState.current_frame += 1;
      demoState.percent = 38 + (demoState.current_frame / total) * 55;
      if (demoState.current_frame >= total) {
        demoState.stage = 4;
        demoState.percent = 95;
        demoState.message = "合成视频…";
        log("渲染完成，ffmpeg 合成中", "ok");
      }
    } else if (demoState.stage === 4) {
      demoState.percent = 100;
      demoState.stage = 5;
      demoState.message = "完成";
      clearInterval(state.pollTimer);
      finishDemo();
    }
    renderProgress(demoState);
  }, 200);
}

function finishDemo() {
  setStatus("live", "空闲");
  state.running = false;
  genBtn.disabled = false;
  genBtn.innerHTML = "<span>生成白膜视频</span>";
  log("演示完成（未连接后端，无真实视频文件）", "ok");
  addDemoVideoCard();
}

function addDemoVideoCard() {
  const card = document.createElement("div");
  card.className = "video-card";
  card.innerHTML = `
    <div class="video-preview">
      <div class="video-placeholder">DEMO<br>无后端，未产出真实视频</div>
    </div>
    <div class="video-info">
      <div class="video-title">演示任务</div>
      <div class="video-meta"><span class="num">864×480 · 24fps</span><span class="num">演示</span></div>
    </div>`;
  removeEmptyState();
  videoGrid.appendChild(card);
  videoCount.textContent = `${videoGrid.querySelectorAll(".video-card").length} 个`;
}

/* ---------- 视频列表加载 ---------- */
async function loadVideos() {
  try {
    const resp = await fetch(`${API_BASE}/api/videos`);
    if (!resp.ok) return;
    const videos = await resp.json();
    if (!videos.length) return;
    removeEmptyState();
    videos.forEach((v) => addVideoCard(v));
  } catch (e) {
    /* 无后端，保持空态 */
  }
}

function addVideoCard(v) {
  const card = document.createElement("div");
  card.className = "video-card";
  const src = v.url || v.path;
  const isVideo = /\.(mp4|webm|mov)$/i.test(src);
  card.innerHTML = `
    <div class="video-preview">
      ${isVideo
        ? `<video controls preload="metadata" src="${src}"></video>`
        : `<div class="video-placeholder">${v.name || "视频"}</div>`}
    </div>
    <div class="video-info">
      <div class="video-title">${v.name || "未命名"}</div>
      <div class="video-meta"><span class="num">${v.resolution || ""}</span><span class="num">${v.created || ""}</span></div>
    </div>`;
  videoGrid.appendChild(card);
  videoCount.textContent = `${videoGrid.querySelectorAll(".video-card").length} 个`;
}

function removeEmptyState() {
  const empty = videoGrid.querySelector(".empty-state");
  if (empty) empty.remove();
}

/* ---------- 事件绑定 ---------- */
chipRow.addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (chip) promptInput.value = chip.dataset.prompt;
});

genBtn.addEventListener("click", async () => {
  const prompt = promptInput.value.trim();
  if (!prompt) {
    log("请输入提示词", "err");
    promptInput.focus();
    return;
  }
  if (state.running) return;

  state.running = true;
  genBtn.disabled = true;
  genBtn.innerHTML = "<span>生成中…</span>";
  resetProgress();
  setStatus("busy", "运行中");

  const res = resSelect.value;
  const fps = parseInt(fpsSelect.value, 10);
  const duration = parseInt(durInput.value, 10);

  const taskId = await submitTask(prompt, res, fps, duration);

  if (state.pollTimer) return; // 演示模式已接管

  // 真实后端轮询
  state.pollTimer = setInterval(async () => {
    const task = await pollTask(taskId);
    if (!task) return;
    renderProgress(task);
    if (task.stage >= 5) {
      clearInterval(state.pollTimer);
      setStatus("live", "空闲");
      state.running = false;
      genBtn.disabled = false;
      genBtn.innerHTML = "<span>生成白膜视频</span>";
      log("任务完成", "ok");
      loadVideos();
    }
  }, 500);
});

/* ---------- 初始化 ---------- */
loadVideos();
