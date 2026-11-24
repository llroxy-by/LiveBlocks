"""自包含页面：内联 CSS + 内联 JS，**零外链、零 CDN**（验收③）。

页面只做四件事，全部走本进程的同一套 API（``docs/CAPTURE.md`` 有对照表）：

1. ``getUserMedia`` 打开任意摄像头（设备列表 + 前置/后置 ``facingMode`` 预设 + 最长边降采样），
   手机前置/后置、笔记本内置、USB UVC 走同一条路径；
2. **单帧抓拍**与**连续取帧**：把 ``video`` 画到 ``canvas``、手写灰度转换后 POST ``/api/frame``，
   拿回 §3 ``CardCandidateList`` 与叠加图（浏览器不需要任何 PNG 编码库，服务端也不需要解码彩图）；
3. **人工标注落盘**：卡片 id、角色、可选角点、备注，以及「这帧识别错了」开关（→ ``refused`` 归档）；
4. 列出已采样本，可点开原帧/真值 JSON。

页面里出现 ``fetch`` 的路径只有本进程的相对地址；测试会断言页面里没有 ``http://``/``https://``/
``//cdn`` 之类的外链，所以断网也能加载（相机权限除外）。
"""

from __future__ import annotations

from .protocol import CAPTURE_VERSION, SERVICE_NAME

PAGE_TITLE = "LB-030 Web 摄像头采集与人工辅助测试台"

PAGE_CSS = """
:root { color-scheme: light dark; --bg:#101418; --fg:#e8eef4; --muted:#93a4b4; --acc:#4da3ff; --warn:#ffb454; --err:#ff6b6b; --ok:#5ddc7c; }
* { box-sizing: border-box; }
body { margin:0; font: 14px/1.5 ui-sans-serif, system-ui, "Segoe UI", "Noto Sans SC", sans-serif; background:var(--bg); color:var(--fg); }
header { padding:16px 20px; border-bottom:1px solid #24303c; }
header h1 { font-size:16px; margin:0 0 4px; }
header p { margin:0; color:var(--muted); font-size:12px; }
main { display:grid; grid-template-columns: minmax(320px, 1fr) minmax(320px, 1fr); gap:16px; padding:16px 20px 48px; }
@media (max-width: 900px) { main { grid-template-columns: 1fr; } }
section { border:1px solid #24303c; border-radius:10px; padding:12px; background:#151b22; }
section h2 { font-size:13px; margin:0 0 10px; color:var(--muted); font-weight:600; letter-spacing:.04em; }
video, img.overlay { width:100%; border-radius:8px; background:#000; display:block; }
.row { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:8px; }
label { font-size:12px; color:var(--muted); }
button { font:inherit; padding:6px 12px; border-radius:8px; border:1px solid #33414f; background:#1d2731; color:var(--fg); cursor:pointer; }
button:hover { border-color:var(--acc); }
button:disabled { opacity:.45; cursor:not-allowed; }
button.primary { background:#1b4f80; border-color:#2a6fa8; }
button.danger { background:#5a2330; border-color:#8b3348; }
input[type=text], textarea, select { font:inherit; background:#0e1318; color:var(--fg); border:1px solid #33414f; border-radius:8px; padding:6px 8px; }
textarea { width:100%; min-height:32px; font-family: ui-monospace, monospace; font-size:12px; }
pre { background:#0e1318; border:1px solid #24303c; border-radius:8px; padding:8px; font-size:12px; max-height:240px; overflow:auto; }
.status { font-size:12px; }
.status.ok { color:var(--ok); } .status.err { color:var(--err); } .status.warn { color:var(--warn); } .status.muted { color:var(--muted); }
.cards { display:flex; flex-direction:column; gap:6px; margin-top:8px; }
.chip { display:inline-block; padding:2px 8px; border-radius:999px; border:1px solid #33414f; font-size:12px; margin-right:6px; }
.chip.id { border-color:var(--acc); color:var(--acc); }
ul.samples { list-style:none; margin:8px 0 0; padding:0; display:flex; flex-direction:column; gap:6px; max-height:320px; overflow:auto; }
ul.samples li { border:1px solid #24303c; border-radius:8px; padding:8px; font-size:12px; }
ul.samples a { color:var(--acc); text-decoration:none; }
.hint { color:var(--muted); font-size:12px; margin:6px 0 0; }
.grid2 { display:grid; grid-template-columns: 120px 1fr; gap:6px 8px; align-items:center; }
.wide { grid-column: 1 / -1; }
"""

PAGE_JS = r"""
'use strict';
const state = { stream:null, timer:null, busy:false, last:null, deviceId:null, facing:null, info:null, sending:0 };
const $ = (id) => document.getElementById(id);
const setStatus = (text, kind) => { const el = $('status'); el.textContent = text; el.className = 'status ' + (kind || 'muted'); };

async function loadInfo() {
  const response = await fetch('/api/info');
  const info = await response.json();
  state.info = info;
  const side = $('maxSide');
  const cardId = $('cardId');
  cardId.placeholder = info.cards.slice(0, 4).map(c => c.card_id).join(' / ') || 'C01';
  $('layout').value = info.card_layout;
  $('layoutHint').textContent = '当前卡纸：' + info.card_layout + '（可用：' + info.card_layouts.join('、') + '）';
  void side;
}
async function readText(response) {
  const text = await response.text();
  try { return JSON.parse(text); } catch (_e) { return { raw: text }; }
}
async function refreshDevices() {
  const devices = await navigator.mediaDevices.enumerateDevices();
  const cameras = devices.filter(d => d.kind === 'videoinput');
  const select = $('deviceSelect');
  const current = select.value;
  select.innerHTML = '';
  cameras.forEach((device, index) => {
    const option = document.createElement('option');
    option.value = device.deviceId;
    option.textContent = device.label || ('摄像头 ' + (index + 1) + '（未授权时没有名字）');
    select.appendChild(option);
  });
  if (current) { select.value = current; }
  $('deviceHint').textContent = cameras.length ? (cameras.length + ' 个摄像头') : '还没拿到摄像头列表：先点「开启相机」授权一次';
}
async function openCamera(deviceId, facing) {
  stopCamera();
  const video = $('video');
  const constraints = { audio:false, video: deviceId ? { deviceId: { exact: deviceId } } : (facing ? { facingMode: { ideal: facing } } : true) };
  try {
    state.stream = await navigator.mediaDevices.getUserMedia(constraints);
  } catch (error) {
    setStatus('打不开摄像头：' + error.name + ' ' + error.message + '（手机上需要 HTTPS 或 localhost）', 'err');
    return;
  }
  video.srcObject = state.stream;
  await video.play().catch(() => {});
  const track = state.stream.getVideoTracks()[0];
  const settings = track ? track.getSettings() : {};
  state.deviceId = settings.deviceId || deviceId || null;
  state.facing = facing || null;
  setStatus('相机已开：' + (track ? track.label : '未知设备') + ' ' + (settings.width || '?') + 'x' + (settings.height || '?'), 'ok');
  await refreshDevices();
}
function stopCamera() {
  if (state.stream) { state.stream.getTracks().forEach(track => track.stop()); state.stream = null; }
  $('video').srcObject = null;
}
function grabGray() {
  const video = $('video');
  if (!video.videoWidth) { throw new Error('画面还没有就绪（先开启相机）'); }
  const maxSide = Number($('maxSide').value) || 480;
  const scale = Math.min(1, maxSide / Math.max(video.videoWidth, video.videoHeight));
  const width = Math.max(8, Math.round(video.videoWidth * scale));
  const height = Math.max(8, Math.round(video.videoHeight * scale));
  const canvas = $('canvas');
  canvas.width = width; canvas.height = height;
  const ctx = canvas.getContext('2d', { willReadFrequently:true });
  ctx.drawImage(video, 0, 0, width, height);
  const rgba = ctx.getImageData(0, 0, width, height).data;
  const gray = new Uint8Array(width * height);
  for (let i = 0, p = 0; i < gray.length; i++, p += 4) {
    gray[i] = (rgba[p] * 299 + rgba[p + 1] * 587 + rgba[p + 2] * 114 + 500) / 1000;
  }
  return { width, height, gray };
}
async function sendFrame() {
  if (state.busy) { return null; }
  state.busy = true;
  try {
    const frame = grabGray();
    const query = new URLSearchParams({ width:String(frame.width), height:String(frame.height), frame_id:'f-cam-' + Date.now() });
    if ($('layout').value) { query.set('card_layout', $('layout').value); }
    const started = performance.now();
    const response = await fetch('/api/frame?' + query.toString(), { method:'POST', headers:{ 'Content-Type':'application/x-liveblocks-gray' }, body:frame.gray });
    const payload = await readText(response);
    const elapsed = Math.round(performance.now() - started);
    if (!response.ok) { setStatus('识别失败：' + describeError(payload), 'err'); return null; }
    state.sending += 1;
    renderResult(payload, frame, elapsed);
    return payload;
  } catch (error) {
    setStatus('取帧失败：' + error.message, 'err');
    return null;
  } finally { state.busy = false; }
}
function describeError(payload) {
  if (payload && payload.error) { return payload.error.code + ' ' + payload.error.message; }
  if (payload && payload.raw) { return payload.raw.slice(0, 200); }
  return '未知错误';
}
function renderResult(payload, frame, elapsed) {
  const cards = payload.card_candidate_list.cards || [];
  state.last = { token:payload.frame_token, cards, frameId:payload.frame.frame_id, width:frame.width, height:frame.height };
  state.lastMarkers = payload.marker_candidate_list.markers || [];
  $('overlay').src = 'data:image/png;base64,' + payload.overlay.png_base64;
  const list = $('cardsList');
  list.innerHTML = '';
  if (!cards.length) {
    const empty = document.createElement('div');
    empty.className = 'status warn';
    empty.textContent = '这一帧没识别到卡片（标记可能太小、太斜或光不够）：调整后重拍。';
    list.appendChild(empty);
  }
  cards.forEach(card => {
    const row = document.createElement('div');
    const id = document.createElement('span'); id.className = 'chip id'; id.textContent = card.card_id;
    const face = document.createElement('span'); face.className = 'chip'; face.textContent = card.card_face;
    const status = document.createElement('span'); status.className = 'chip'; status.textContent = card.status;
    const conf = document.createElement('span'); conf.className = 'chip'; conf.textContent = Number(card.confidence).toFixed(2);
    row.append(id, face, status, conf);
    list.appendChild(row);
  });
  $('cardsJson').textContent = JSON.stringify({
    frame_id: payload.card_candidate_list.frame_id,
    marker_count: (payload.marker_candidate_list.markers || []).length,
    cards: cards.map(c => ({ candidate_id:c.candidate_id, card_id:c.card_id, card_face:c.card_face, status:c.status, confidence:c.confidence, marker_ids:c.marker_ids })),
  }, null, 2);
  if (!state.lastCardPrefilled && cards.length) {
    $('cardId').value = cards[0].card_id === 'unknown' ? '' : cards[0].card_id;
    $('role').value = cards[0].card_type && cards[0].card_type !== 'unknown' ? cards[0].card_type : '';
    state.lastCardPrefilled = true;
  }
  setStatus('识别完成：' + cards.length + ' 张卡，' + frame.width + 'x' + frame.height + '，' + elapsed + ' ms' + (state.timer ? '（连续取帧中）' : ''), 'ok');
}
function toggleContinuous() {
  if ($('continuous').checked) {
    const interval = Math.max(200, Number($('interval').value) || 900);
    state.timer = setInterval(() => { void sendFrame(); }, interval);
    setStatus('连续取帧中：每 ' + interval + ' ms 一帧', 'ok');
    void sendFrame();
  } else if (state.timer) {
    clearInterval(state.timer); state.timer = null;
    setStatus('连续取帧已停', 'muted');
  }
}
async function submitCapture(event) {
  event.preventDefault();
  if (!state.last || !state.last.token) { setStatus('先抓一帧（单帧抓拍），再落盘', 'err'); return; }
  const outcome = $('wrong').checked ? 'misrecognised' : 'accepted';
  const cornersRaw = $('corners').value.trim();
  const annotation = { card_id:$('cardId').value.trim(), role:$('role').value.trim() || null, notes:$('annotationNotes').value.trim() || null };
  if (cornersRaw) {
    try { annotation.corners_px = JSON.parse(cornersRaw); }
    catch (error) { setStatus('角点不是合法 JSON：' + error.message, 'err'); return; }
  }
  const body = {
    frame_token: state.last.token,
    card_layout: $('layout').value || null,
    outcome,
    notes: $('captureNotes').value.trim() || null,
    annotations: [annotation],
  };
  const response = await fetch('/api/capture', { method:'POST', headers:{ 'Content-Type':'application/json' }, body: JSON.stringify(body) });
  const payload = await readText(response);
  if (!response.ok) { setStatus('落盘失败：' + describeError(payload), 'err'); return; }
  const capture = payload.capture;
  const warnings = capture.warnings || [];
  const lines = ['已落盘 ' + capture.directory + '（' + capture.outcome + '）'];
  if (capture.recognize_report_command) { lines.push('可直接跑：' + capture.recognize_report_command); }
  lines.push('识别一致：' + capture.cards.filter(c => c.match).length + '/' + capture.cards.length);
  if (warnings.length) { lines.push('警告：' + warnings.join('；')); }
  $('captureResult').textContent = lines.join('\n');
  setStatus('样本已落盘（' + capture.outcome + '）', capture.outcome === 'misrecognised' ? 'warn' : 'ok');
  $('wrong').checked = false;
  $('captureNotes').value = '';
  await refreshCaptures();
}
function fillCornersFromDetection() {
  if (!state.last || !state.last.cards.length) { setStatus('先抓一帧，才有检出角点可填', 'err'); return; }
  const card = state.last.cards[0];
  const markerId = card.marker_ids && card.marker_ids.length ? String(card.marker_ids[0]) : null;
  if (!markerId) { setStatus('这张卡没有 marker_ids（身份未知），只能手填角点', 'warn'); return; }
  const marker = (state.lastMarkers || []).find(m => String(m.marker_id) === markerId);
  if (!marker) { setStatus('没找到标记 ' + markerId + ' 的角点', 'warn'); return; }
  $('corners').value = JSON.stringify(marker.corners_px.map(p => [Math.round(p[0]), Math.round(p[1])]));
  setStatus('已填入检出的角点（提交时会记成 corner_source=manual，会被当真值用，请先眼睛核对）', 'warn');
}
async function refreshCaptures() {
  const response = await fetch('/api/captures');
  const payload = await response.json();
  const list = $('samples');
  list.innerHTML = '';
  (payload.captures || []).forEach(capture => {
    const item = document.createElement('li');
    const head = document.createElement('div');
    head.textContent = capture.capture_id + ' · ' + capture.outcome + ' · ' + capture.card_ids.join(',') + ' · ' + capture.width + 'x' + capture.height;
    const links = document.createElement('div');
    const pairs = [['原帧', capture.frame_url], ['真值', capture.truth_url], ['记录', capture.record_url]];
    pairs.forEach(([label, url]) => {
      const anchor = document.createElement('a');
      anchor.href = url; anchor.textContent = label; anchor.target = '_blank'; anchor.rel = 'noreferrer';
      links.appendChild(anchor);
      links.appendChild(document.createTextNode(' '));
    });
    const meta = document.createElement('div');
    meta.className = 'status muted';
    meta.textContent = '目录 ' + capture.directory + (capture.report_usable ? '' : '（没写进 scene.json，recognize report 读不到）');
    item.append(head, links, meta);
    list.appendChild(item);
  });
  if (!(payload.captures || []).length) {
    const empty = document.createElement('li');
    empty.className = 'status muted';
    empty.textContent = '还没有样本。抓一帧 → 填卡片 id → 落盘。';
    list.appendChild(empty);
  }
}
function wire() {
  $('start').addEventListener('click', () => openCamera($('deviceSelect').value || null, null));
  $('rear').addEventListener('click', () => openCamera(null, 'environment'));
  $('front').addEventListener('click', () => openCamera(null, 'user'));
  $('stop').addEventListener('click', () => { stopCamera(); setStatus('相机已关', 'muted'); });
  $('refreshDevices').addEventListener('click', () => { void refreshDevices(); });
  $('grabOnce').addEventListener('click', () => { void sendFrame(); });
  $('continuous').addEventListener('change', toggleContinuous);
  $('captureForm').addEventListener('submit', (event) => { void submitCapture(event); });
  $('fillCorners').addEventListener('click', fillCornersFromDetection);
  $('refreshCaptures').addEventListener('click', () => { void refreshCaptures(); });
  navigator.mediaDevices.addEventListener('devicechange', () => { void refreshDevices(); });
  void loadInfo().then(refreshDevices).then(refreshCaptures).catch(error => setStatus('初始化失败：' + error.message, 'err'));
}
window.addEventListener('DOMContentLoaded', wire);
window.addEventListener('beforeunload', stopCamera);
"""

PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>__TITLE__</title>
<style>__CSS__</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <p>服务 <code>__SERVICE__</code> v__CAPTURE_VERSION__ · 本页与它的 API 都只跑在本机：不连外网、不含 CDN、不需要账号。
     手机访问需要 HTTPS 或 localhost（公网入口与 token 由运维侧隧道负责，本服务不做鉴权）。</p>
</header>
<main>
  <section>
    <h2>1. 相机</h2>
    <video id="video" autoplay playsinline muted></video>
    <div class="row">
      <label for="deviceSelect">设备</label>
      <select id="deviceSelect"></select>
      <button id="refreshDevices">刷新设备</button>
    </div>
    <div class="row">
      <button id="start" class="primary">用所选设备开启</button>
      <button id="rear">后置 / 环境相机</button>
      <button id="front">前置相机</button>
      <button id="stop">关闭</button>
    </div>
    <div class="row">
      <label for="maxSide">最长边</label>
      <select id="maxSide">
        <option value="320">320</option>
        <option value="480" selected>480</option>
        <option value="640">640</option>
        <option value="960">960</option>
      </select>
      <span class="hint" id="deviceHint">先点「用所选设备开启」授权一次，才有设备名字列表</span>
    </div>
    <p class="hint">识别在服务端跑：最长边越小越快，标记在画面里至少要有约 40 px 才稳。</p>
  </section>

  <section>
    <h2>2. 取帧与识别</h2>
    <div class="row">
      <button id="grabOnce" class="primary">单帧抓拍 → 识别</button>
      <label><input type="checkbox" id="continuous" /> 连续取帧</label>
      <label for="interval">间隔(ms)</label>
      <input type="text" id="interval" value="900" size="5" />
      <span id="status" class="status muted">待机</span>
    </div>
    <div class="row">
      <label for="layout">卡纸版式</label>
      <input type="text" id="layout" value="" size="12" />
      <span class="hint" id="layoutHint"></span>
    </div>
    <img id="overlay" class="overlay" alt="识别叠加图（服务端画在原帧上）" />
    <div class="cards" id="cardsList"></div>
    <pre id="cardsJson">还没有识别结果。</pre>
    <canvas id="canvas" style="display:none"></canvas>
  </section>

  <section>
    <h2>3. 人工标注并落盘</h2>
    <form id="captureForm" class="grid2">
      <label for="cardId">卡片 id</label>
      <input type="text" id="cardId" placeholder="C01" required />
      <label for="role">角色</label>
      <input type="text" id="role" placeholder="action / control / number / operator" />
      <label for="corners">角点(可选)</label>
      <textarea id="corners" placeholder="[[x,y],[x,y],[x,y],[x,y]]：标记四角，左上→右上→右下→左下；留空就用检出角点"></textarea>
      <div class="wide row">
        <button type="button" id="fillCorners">用检出角点填入</button>
        <span class="hint">填了角点就当真值用（corner_source=manual），提交前请核对。</span>
      </div>
      <label for="annotationNotes">标注备注</label>
      <input type="text" id="annotationNotes" placeholder="可选：这张卡的实际用途" />
      <label for="captureNotes">样本备注</label>
      <input type="text" id="captureNotes" placeholder="可选：光照、距离、纸张等条件" />
      <label for="wrong">识别结论</label>
      <div><label><input type="checkbox" id="wrong" /> 这帧识别错了（单独归档到 fixtures/live/refused/）</label></div>
      <div class="wide row">
        <button type="submit" class="primary">落盘这一帧</button>
        <button type="button" id="refreshCaptures">刷新样本</button>
      </div>
    </form>
    <pre id="captureResult">落盘后会在这里显示目录与可直接复制的 recognize report 命令。</pre>
  </section>

  <section>
    <h2>4. 已采样本</h2>
    <ul class="samples" id="samples"><li class="status muted">加载中…</li></ul>
    <p class="hint">样本目录就是场景目录：<code>liveblocks recognize report --dir fixtures/live/&lt;id&gt;</code>。</p>
  </section>
</main>
<script>__JS__</script>
</body>
</html>
"""


def render_page() -> bytes:
    """返回整页 HTML（单文件，无外链）。"""
    html = (
        PAGE_HTML.replace("__TITLE__", PAGE_TITLE)
        .replace("__CSS__", PAGE_CSS.strip())
        .replace("__SERVICE__", SERVICE_NAME)
        .replace("__CAPTURE_VERSION__", CAPTURE_VERSION)
        .replace("__JS__", PAGE_JS.strip())
    )
    return html.encode("utf-8")


__all__ = ["PAGE_CSS", "PAGE_HTML", "PAGE_JS", "PAGE_TITLE", "render_page"]
