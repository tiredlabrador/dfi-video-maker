/*
 * Dive In — the page.
 *
 * Every finished picture comes from the same Python code that makes the export,
 * so the preview can't drift from the real thing. While you drag or zoom, the
 * page draws a quick draft itself (your photo with the same treatment, plus
 * everything above it) and swaps in the exact render when you let go.
 */
'use strict';

const $ = (id) => document.getElementById(id);
const show = (el, on) => { el.hidden = !on; };
const STORE = 'divein-settings-v1';
const ACCENTS = { yellow: '#fffe01', red: '#ea2020', green: '#3dff00', white: '#ffffff' };

const state = {
  photo: null,            // {token, width, height}
  audio: null,            // {token, duration}
  crop: { zoom: 1, cx: 0.5, cy: 0.5 },
  format: 'portrait',
  colour: 'yellow',
  motion: 'kicks',
  overrides: {},
  defaults: null,
  polling: null,
  // The mix timeline
  starts: [],             // clip start times, in seconds
  selected: 0,            // which clip the preview and "moment" use
  overview: null,         // {duration, peaks}
  listening: null,        // index of the clip playing, or null
  // The quick draft
  photoEl: null,
  srcBase: null,
  srcGhost: null,
  srcScale: 1,
  layers: null,
  interacting: false,
  draftQueued: false,
  idleTimer: null,
};

/* ── helpers ─────────────────────────────────────────────────────── */

function setError(message) {
  $('error').textContent = message || '';
  show($('error'), Boolean(message));
}

function setWarning(message) {
  $('warning').textContent = message || '';
  show($('warning'), Boolean(message));
}

function timecode(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  const s = seconds % 60, m = Math.floor(seconds / 60) % 60, h = Math.floor(seconds / 3600);
  const pad = (n) => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function parseTime(text) {
  const t = String(text).trim();
  if (!t || t.startsWith('-')) return null;
  const parts = t.split(':').map(Number);
  if (parts.some((n) => !Number.isFinite(n))) return null;
  return parts.reduce((total, n) => total * 60 + n, 0);
}

function clipLength() {
  return Math.min(120, Math.max(1, Number($('clip-seconds').value) || 25));
}

/** A setting, from the Tuning overrides if set there, else the defaults. */
function setting(path, fallback) {
  const dig = (obj) => path.split('.').reduce((o, k) => (o && k in o ? o[k] : undefined), obj);
  const v = dig(state.overrides);
  if (v !== undefined) return v;
  const d = state.defaults && dig(state.defaults);
  return d !== undefined ? d : fallback;
}

function design(extra = {}) {
  return {
    photo: state.photo && state.photo.token,
    audio: state.audio && state.audio.token,
    crop: state.crop,
    artist: $('artist').value,
    episode: $('episode').value,
    starts: state.starts.map(timecode).join(', '),
    preview_index: state.selected,
    clip_seconds: clipLength(),
    glyph: $('rings-on').checked ? 'rings' : 'none',
    colour: state.colour,
    motion: state.motion,
    twitch: $('twitch').checked,
    overrides: state.overrides,
    ...extra,
  };
}

async function postJSON(path, body) {
  const response = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || `Request failed (${response.status}).`);
  }
  return response;
}

/* ── remembering settings between visits ─────────────────────────── */

function save() {
  try {
    localStorage.setItem(STORE, JSON.stringify({
      artist: $('artist').value, episode: $('episode').value,
      clip: $('clip-seconds').value, rings: $('rings-on').checked,
      colour: state.colour, motion: state.motion, twitch: $('twitch').checked,
      overrides: $('overrides').value,
    }));
  } catch { /* private window: fine */ }
}

function restore() {
  let saved;
  try { saved = JSON.parse(localStorage.getItem(STORE) || 'null'); } catch { saved = null; }
  if (!saved) return;
  $('artist').value = saved.artist ?? '';
  $('episode').value = saved.episode ?? '02.01';
  $('clip-seconds').value = saved.clip ?? 25;
  $('rings-on').checked = saved.rings ?? true;
  $('twitch').checked = saved.twitch ?? true;
  $('overrides').value = saved.overrides ?? '{}';
  state.colour = saved.colour || 'yellow';
  state.motion = saved.motion || 'kicks';
  setSegment('colour-choice', state.colour);
  setSegment('motion-choice', state.motion);
  try { state.overrides = JSON.parse($('overrides').value || '{}'); } catch { state.overrides = {}; }
}

/* ── the exact still (drawn by the server) ───────────────────────── */

let stillSeq = 0;
let stillTimer = null;
let stillController = null;
// Identifies this open page, so the server counts its requests separately
// from any page it replaced (a reload starts the count again from 1).
const CLIENT = Math.random().toString(36).slice(2) + Date.now().toString(36);

function scheduleStill(delay = 120) {
  save();
  clearTimeout(stillTimer);
  stillTimer = setTimeout(refreshStill, delay);
}

async function refreshStill() {
  if (!state.photo) return;
  const seq = ++stillSeq;
  if (stillController) stillController.abort();
  stillController = new AbortController();
  show($('busy'), true);

  const extra = { format: state.format, seq, client: CLIENT };
  if ($('scrub-on').checked && state.audio && state.starts.length && state.format === 'portrait') {
    extra.time = Number($('scrub').value);
  }
  try {
    const response = await fetch('/api/divein/still', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(design(extra)), signal: stillController.signal,
    });
    if (response.status === 204) return;         // superseded by a newer request
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || 'The preview could not be drawn.');
    }
    const warning = decodeURIComponent(response.headers.get('X-Divein-Warning') || '');
    const blob = await response.blob();
    if (seq !== stillSeq) return;
    setWarning(warning);
    const img = $('still');
    const old = img.src;
    img.onload = () => {
      if (!state.interacting && seq === stillSeq) show($('draft'), false);
    };
    img.src = URL.createObjectURL(blob);
    if (old.startsWith('blob:')) URL.revokeObjectURL(old);
    show($('stage-empty'), false);
    show($('player'), false);
    show($('frame'), true);
    setError('');
  } catch (error) {
    if (error.name !== 'AbortError') setError(error.message);
  } finally {
    if (seq === stillSeq) show($('busy'), false);
  }
}

/* ── the quick draft (drawn here, while dragging) ────────────────── */

let layersSeq = 0;
let layersTimer = null;

function scheduleLayers(delay = 150) {
  clearTimeout(layersTimer);
  layersTimer = setTimeout(refreshLayers, delay);
}

async function refreshLayers() {
  const seq = ++layersSeq;
  try {
    const response = await postJSON('/api/divein/layers', design({ format: state.format }));
    const blob = await response.blob();
    if (seq !== layersSeq) return;
    const img = new Image();
    img.src = URL.createObjectURL(blob);
    await img.decode();
    if (seq !== layersSeq) return;
    if (state.layers) URL.revokeObjectURL(state.layers.src);
    state.layers = img;
  } catch { /* the exact still will report any problem */ }
}

function prepareDraftSources() {
  const img = state.photoEl;
  if (!img) return;
  const [W, H] = canvasSize();
  const k = Math.min(1, 2400 / Math.max(img.naturalWidth, img.naturalHeight));
  const w = Math.round(img.naturalWidth * k), h = Math.round(img.naturalHeight * k);
  const make = (filter) => {
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    const ctx = c.getContext('2d');
    ctx.filter = filter;
    ctx.drawImage(img, 0, 0, w, h);
    return c;
  };
  const coverScale = Math.max(W / img.naturalWidth, H / img.naturalHeight);
  const blur = setting('ghost.blur', 5) * k / coverScale;
  state.srcScale = k;
  state.srcBase = make(`grayscale(1) contrast(${setting('photo.contrast', 1.35)}) brightness(${setting('photo.brightness', 0.9)})`);
  state.srcGhost = make(`grayscale(1) contrast(${setting('ghost.contrast', 1.6)}) brightness(${setting('ghost.brightness', 1.3)}) blur(${blur}px)`);
}

function cropWindow() {
  const [W, H] = canvasSize();
  const { width: iw, height: ih } = state.photo;
  const scale = Math.max(W / iw, H / ih) * state.crop.zoom;
  const ww = W / scale, wh = H / scale;
  const cx = Math.min(iw - ww / 2, Math.max(ww / 2, state.crop.cx * iw));
  const cy = Math.min(ih - wh / 2, Math.max(wh / 2, state.crop.cy * ih));
  return { x0: cx - ww / 2, y0: cy - wh / 2, ww, wh, scale };
}

function drawDraft() {
  state.draftQueued = false;
  if (!state.srcBase || !state.photo) return;
  const canvas = $('draft');
  const [W, H] = canvasSize();
  const cw = W / 2, ch = H / 2;
  if (canvas.width !== cw || canvas.height !== ch) { canvas.width = cw; canvas.height = ch; }
  const ctx = canvas.getContext('2d');
  const k = state.srcScale;
  const { x0, y0, ww, wh } = cropWindow();
  ctx.globalCompositeOperation = 'source-over';
  ctx.globalAlpha = 1;
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, cw, ch);
  ctx.drawImage(state.srcBase, x0 * k, y0 * k, ww * k, wh * k, 0, 0, cw, ch);
  if (setting('ghost.enabled', true)) {
    const dx = setting('ghost.offset_x', 22) * cw / W, dy = setting('ghost.offset_y', -10) * ch / H;
    ctx.globalCompositeOperation = 'screen';
    ctx.globalAlpha = setting('ghost.opacity', 0.45);
    ctx.drawImage(state.srcGhost, x0 * k, y0 * k, ww * k, wh * k, dx, dy, cw, ch);
    ctx.globalCompositeOperation = 'source-over';
    ctx.globalAlpha = 1;
  }
  if (state.layers) ctx.drawImage(state.layers, 0, 0, cw, ch);
  show(canvas, true);
}

function requestDraft() {
  if (state.draftQueued) return;
  state.draftQueued = true;
  requestAnimationFrame(drawDraft);
}

/** You're moving something: draw drafts now, the exact still once you stop. */
function nudged() {
  state.interacting = true;
  requestDraft();
  clearTimeout(state.idleTimer);
  state.idleTimer = setTimeout(() => { state.interacting = false; scheduleStill(0); }, 180);
}

/* ── uploads ─────────────────────────────────────────────────────── */

async function upload(kind, file) {
  const response = await fetch(`/api/divein/upload?kind=${kind}`, {
    method: 'POST',
    // Encoded: browsers refuse header text beyond Latin-1 ("Dom’s mix.mp3").
    headers: { 'Content-Type': 'application/octet-stream',
               'X-Filename': encodeURIComponent(file.name || 'pasted.png') },
    body: file,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || 'Upload failed.');
  return payload;
}

async function usePhoto(file) {
  if (!file) return;
  if (file.type && !file.type.startsWith('image/')) { setError('That isn’t a photo.'); return; }
  const name = file.name || 'Pasted photo';
  $('photo-label').textContent = `Reading ${name}…`;
  try {
    state.photo = await upload('photo', file);
  } catch (error) {
    $('photo-label').textContent = 'Drop a photo here, or click to choose';
    setError(error.message);
    return;
  }
  $('photo-label').textContent = name;
  $('photo-drop').classList.add('is-set');
  resetCrop();
  show($('zoom-row'), true);
  scheduleStill(0);
  scheduleLayers(0);

  const img = new Image();
  img.src = URL.createObjectURL(file);
  try {
    await img.decode();
    if (state.photoEl) URL.revokeObjectURL(state.photoEl.src);
    state.photoEl = img;
    prepareDraftSources();
  } catch { state.photoEl = null; state.srcBase = null; }
}

async function useAudio(file) {
  if (!file) return;
  $('audio-label').textContent = `Copying ${file.name}…`;
  $('audio-hint').textContent = 'Big mixes take a few seconds';
  try {
    state.audio = await upload('audio', file);
  } catch (error) {
    $('audio-label').textContent = 'Drop the mix here, or click to choose';
    $('audio-hint').textContent = 'Any length';
    setError(error.message);
    return;
  }
  $('audio-label').textContent = file.name;
  $('audio-hint').textContent = `${timecode(state.audio.duration)} long`;
  $('audio-drop').classList.add('is-set');

  // Listening plays the file straight from your disk: nothing to wait for.
  const player = $('listen');
  if (player.src.startsWith('blob:')) URL.revokeObjectURL(player.src);
  player.src = URL.createObjectURL(file);

  state.overview = null;
  state.starts = state.starts.filter((s) => s < state.audio.duration);
  show($('timeline'), true);
  $('timeline-hover').textContent = 'Drawing the mix…';
  renderClips();
  drawTimeline();
  updateScrub();
  try {
    state.overview = await (await fetch(`/api/divein/overview?audio=${encodeURIComponent(state.audio.token)}`)).json();
  } catch { /* the timeline still works without the shape */ }
  $('timeline-hover').textContent = 'Click to add a clip. Drag a clip to move it.';
  drawTimeline();
}

/* ── the mix timeline ────────────────────────────────────────────── */

function duration() {
  return (state.overview && state.overview.duration) || (state.audio && state.audio.duration) || 0;
}

function drawTimeline() {
  const canvas = $('timeline-canvas');
  const total = duration();
  const ratio = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w) return;
  canvas.width = w * ratio; canvas.height = h * ratio;
  const ctx = canvas.getContext('2d');
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, w, h);
  $('timeline-length').textContent = total ? timecode(total) : '';
  if (!total) return;

  const accent = ACCENTS[state.colour] || ACCENTS.yellow;
  const peaks = state.overview ? state.overview.peaks : [];
  // The mix's loudness, as a mirrored shape.
  ctx.fillStyle = 'rgba(255,255,255,0.28)';
  for (let x = 0; x < w; x++) {
    const v = peaks.length ? peaks[Math.min(peaks.length - 1, Math.floor(x / w * peaks.length))] : 0.15;
    const bar = Math.max(1, v * (h - 12));
    ctx.fillRect(x, (h - bar) / 2, 1, bar);
  }
  // The clips.
  const len = clipLength();
  state.starts.forEach((start, i) => {
    const x0 = start / total * w, x1 = Math.min(w, (start + len) / total * w);
    ctx.globalAlpha = i === state.selected ? 0.5 : 0.3;
    ctx.fillStyle = accent;
    ctx.fillRect(x0, 0, Math.max(2, x1 - x0), h);
    ctx.globalAlpha = 1;
    ctx.fillRect(x0, 0, 2, h);
    ctx.fillStyle = '#000';
    ctx.font = 'bold 11px -apple-system, sans-serif';
    ctx.fillStyle = accent;
    ctx.fillRect(x0, 0, 16, 16);
    ctx.fillStyle = '#000';
    ctx.fillText(String(i + 1), x0 + 4.5, 12);
  });
  // Where playback is.
  const player = $('listen');
  if (state.listening !== null && !player.paused) {
    const x = player.currentTime / total * w;
    ctx.fillStyle = '#fff';
    ctx.fillRect(x, 0, 2, h);
  }
}

function clipAt(x, w) {
  const total = duration(), len = clipLength();
  for (let i = state.starts.length - 1; i >= 0; i--) {
    const x0 = state.starts[i] / total * w, x1 = (state.starts[i] + len) / total * w;
    if (x >= x0 - 3 && x <= Math.max(x1, x0 + 8)) return i;
  }
  return -1;
}

function wireTimeline() {
  const canvas = $('timeline-canvas');
  let drag = null;
  const timeAt = (e) => {
    const box = canvas.getBoundingClientRect();
    return Math.max(0, Math.min(1, (e.clientX - box.left) / box.width)) * duration();
  };
  canvas.addEventListener('pointerdown', (e) => {
    if (!duration()) return;
    const box = canvas.getBoundingClientRect();
    const i = clipAt(e.clientX - box.left, box.width);
    const t = timeAt(e);
    if (i >= 0) {
      drag = { i, offset: t - state.starts[i], moved: false };
      selectClip(i);
    } else {
      const start = Math.min(Math.round(t), Math.max(0, Math.floor(duration() - clipLength())));
      state.starts.push(start);
      selectClip(state.starts.length - 1);
      drag = { i: state.starts.length - 1, offset: t - start, moved: true };
      startsChanged();
    }
    canvas.setPointerCapture(e.pointerId);
    canvas.classList.add('is-moving');
  });
  canvas.addEventListener('pointermove', (e) => {
    const t = timeAt(e);
    $('timeline-hover').textContent = duration() ? `${timecode(t)} — click to add a clip, drag to move one` : '';
    if (!drag) return;
    const max = Math.max(0, duration() - clipLength());
    state.starts[drag.i] = Math.round(Math.min(max, Math.max(0, t - drag.offset)));
    drag.moved = true;
    renderClips();
    drawTimeline();
  });
  const end = () => {
    if (drag && drag.moved) startsChanged();
    drag = null;
    canvas.classList.remove('is-moving');
  };
  canvas.addEventListener('pointerup', end);
  canvas.addEventListener('pointercancel', end);
  canvas.addEventListener('pointerleave', () => {
    if (!drag) $('timeline-hover').textContent = 'Click to add a clip. Drag a clip to move it.';
  });
  window.addEventListener('resize', drawTimeline);
}

function selectClip(i) {
  state.selected = Math.max(0, Math.min(i, state.starts.length - 1));
  renderClips();
  drawTimeline();
  if ($('scrub-on').checked) scheduleStill(0);
}

/** The clip list changed: update the typed times, the list and the picture. */
function startsChanged() {
  $('starts').value = state.starts.map(timecode).join(', ');
  if (state.selected >= state.starts.length) state.selected = Math.max(0, state.starts.length - 1);
  renderClips();
  drawTimeline();
  updateScrub();
  if ($('scrub-on').checked) scheduleStill(0);
}

function startsFromText() {
  const parts = $('starts').value.split(/[,;\n]+/).map((p) => p.trim()).filter(Boolean);
  const times = parts.map(parseTime);
  if (times.some((t) => t === null)) return false;      // keep typing
  state.starts = times;
  if (state.selected >= times.length) state.selected = 0;
  renderClips();
  drawTimeline();
  updateScrub();
  return true;
}

function renderClips() {
  const list = $('clips');
  list.innerHTML = '';
  const len = clipLength();
  state.starts.forEach((start, i) => {
    const li = document.createElement('li');
    if (i === state.selected) li.className = 'is-selected';
    const num = document.createElement('span');
    num.className = 'clip-num';
    num.textContent = String(i + 1);
    num.style.background = ACCENTS[state.colour];
    const time = document.createElement('span');
    time.className = 'clip-time';
    time.textContent = `${timecode(start)} – ${timecode(start + len)}`;
    const listen = document.createElement('button');
    listen.type = 'button';
    listen.textContent = state.listening === i ? '■ Stop' : '▶ Listen';
    listen.addEventListener('click', (e) => { e.stopPropagation(); toggleListen(i); });
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'clip-remove';
    remove.textContent = '✕';
    remove.title = 'Remove this clip';
    remove.addEventListener('click', (e) => {
      e.stopPropagation();
      if (state.listening === i) stopListening();
      state.starts.splice(i, 1);
      startsChanged();
    });
    li.append(num, time, listen, remove);
    li.addEventListener('click', () => selectClip(i));
    list.append(li);
  });
  $('preview-btn').textContent = state.starts.length > 1
    ? `Preview clip ${state.selected + 1} with sound` : 'Preview with sound';
}

function toggleListen(i) {
  if (state.listening === i) { stopListening(); return; }
  const player = $('listen');
  state.listening = i;
  selectClip(i);
  player.currentTime = state.starts[i];
  player.play().catch(() => stopListening());
  const tick = () => {
    if (state.listening !== i) return;
    if (player.paused || player.currentTime >= state.starts[i] + clipLength()) { stopListening(); return; }
    drawTimeline();
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  renderClips();
}

function stopListening() {
  $('listen').pause();
  state.listening = null;
  renderClips();
  drawTimeline();
}

/* ── crop: drag and zoom ─────────────────────────────────────────── */

function canvasSize() {
  return state.format === 'square' ? [1080, 1080] : [1080, 1350];
}

function clampCrop() {
  if (!state.photo) return;
  const [W, H] = canvasSize();
  const { width: iw, height: ih } = state.photo;
  const scale = Math.max(W / iw, H / ih) * state.crop.zoom;
  const halfW = W / scale / 2 / iw, halfH = H / scale / 2 / ih;
  state.crop.cx = Math.min(1 - halfW, Math.max(halfW, state.crop.cx));
  state.crop.cy = Math.min(1 - halfH, Math.max(halfH, state.crop.cy));
}

function resetCrop() {
  state.crop = { zoom: 1, cx: 0.5, cy: 0.5 };
  $('zoom').value = 1;
}

/** Zoom to `zoom`, keeping the photo point under (fx, fy) of the frame still. */
function zoomAt(zoom, fx = 0.5, fy = 0.5) {
  if (!state.photo) return;
  zoom = Math.min(3, Math.max(1, zoom));
  const before = cropWindow();
  const px = before.x0 + fx * before.ww, py = before.y0 + fy * before.wh;
  state.crop.zoom = zoom;
  const after = cropWindow();
  const { width: iw, height: ih } = state.photo;
  state.crop.cx = (px - fx * after.ww + after.ww / 2) / iw;
  state.crop.cy = (py - fy * after.wh + after.wh / 2) / ih;
  clampCrop();
  $('zoom').value = zoom;
  nudged();
}

function wireDrag() {
  const frame = $('frame');
  let last = null;
  frame.addEventListener('pointerdown', (e) => {
    if (!state.photo) return;
    last = { x: e.clientX, y: e.clientY };
    frame.setPointerCapture(e.pointerId);
    frame.classList.add('is-dragging');
  });
  frame.addEventListener('pointermove', (e) => {
    if (!last) return;
    const [W] = canvasSize();
    const { width: iw, height: ih } = state.photo;
    const { scale } = cropWindow();
    const perScreenPx = W / frame.clientWidth;
    state.crop.cx -= (e.clientX - last.x) * perScreenPx / scale / iw;
    state.crop.cy -= (e.clientY - last.y) * perScreenPx / scale / ih;
    last = { x: e.clientX, y: e.clientY };
    clampCrop();
    nudged();
  });
  const end = () => { last = null; frame.classList.remove('is-dragging'); };
  frame.addEventListener('pointerup', end);
  frame.addEventListener('pointercancel', end);
  frame.addEventListener('wheel', (e) => {
    if (!state.photo) return;
    e.preventDefault();
    const box = frame.getBoundingClientRect();
    const fx = (e.clientX - box.left) / box.width, fy = (e.clientY - box.top) / box.height;
    const rate = e.ctrlKey ? 0.01 : 0.0025;       // pinch arrives as ctrl+wheel
    zoomAt(state.crop.zoom * Math.exp(-e.deltaY * rate), fx, fy);
  }, { passive: false });
}

/* ── scrubbing through the selected clip ─────────────────────────── */

function updateScrub() {
  $('scrub').max = clipLength();
  show($('scrub-row'), Boolean(state.audio && state.starts.length && $('rings-on').checked));
  $('scrub').disabled = !$('scrub-on').checked;
  $('scrub-label').textContent = $('scrub-on').checked
    ? `${Number($('scrub').value).toFixed(2)}s into clip ${state.selected + 1}` : 'at rest';
}

/* ── preview and export ──────────────────────────────────────────── */

function setBusy(busy) {
  $('preview-btn').disabled = busy;
  $('export-btn').disabled = busy;
}

async function startJob(kind) {
  setError('');
  if (!state.photo) { setError('Choose a photo first.'); return; }
  if (!state.audio) { setError('Choose the mix first.'); return; }
  if (!state.starts.length) { setError('Add a clip: click on the mix where it should start.'); return; }
  stopListening();
  setBusy(true);
  show($('files'), false);
  show($('progress'), true);
  $('progress-fill').style.width = '0%';
  $('progress-text').textContent = kind === 'preview' ? 'Making a preview…' : 'Starting the export…';
  let job;
  try {
    job = await (await postJSON(`/api/divein/${kind}`, design())).json();
  } catch (error) {
    setError(error.message); setBusy(false); show($('progress'), false);
    return;
  }
  clearInterval(state.polling);
  state.polling = setInterval(async () => {
    let s;
    try { s = await (await fetch(`/api/divein/jobs/${job.id}`)).json(); } catch { return; }
    $('progress-fill').style.width = `${Math.round((s.progress || 0) * 100)}%`;
    $('progress-text').textContent = s.message || 'Working…';
    if (s.status === 'done' || s.status === 'error') {
      clearInterval(state.polling);
      setBusy(false);
      show($('progress'), false);
      if (s.status === 'error') {
        setError(s.error || 'That failed.');
        if (s.files && s.files.length) showFiles(s, kind);
        return;
      }
      showFiles(s, kind);
    }
  }, 400);
}

function showFiles(job, kind) {
  const list = $('file-list');
  list.innerHTML = '';
  for (const f of job.files) {
    const li = document.createElement('li');
    li.className = 'is-done';
    const name = document.createElement('span');
    name.className = 'result-name';
    name.textContent = f.name;
    const size = document.createElement('span');
    size.className = 'result-size';
    size.textContent = `${(f.size / 1048576).toFixed(1)} MB`;
    const link = document.createElement('a');
    link.href = f.url;
    link.download = f.name;
    link.textContent = 'Download';
    li.append(name, size);
    if (f.name.endsWith('.mp4')) {
      const play = document.createElement('button');
      play.type = 'button'; play.className = 'result-play'; play.textContent = 'Watch';
      play.addEventListener('click', () => playVideo(f.url));
      li.append(play);
    }
    li.append(link);
    list.append(li);
  }
  const zip = $('zip-link');
  if (job.zip_url) { zip.href = job.zip_url; zip.download = 'Dive In.zip'; }
  show(zip, Boolean(job.zip_url));
  show($('files'), true);
  if (kind === 'preview' && job.files[0]) playVideo(job.files[0].url);
}

function playVideo(url) {
  const player = $('player');
  show($('frame'), false);
  show($('stage-empty'), false);
  player.src = url;
  show(player, true);
  player.play().catch(() => {});
}

/* ── wiring ──────────────────────────────────────────────────────── */

function setSegment(id, value) {
  for (const b of document.querySelectorAll(`#${id} button`)) {
    b.setAttribute('aria-checked', String(b.dataset.value === value));
  }
}

function wireSegment(id, onChange) {
  document.querySelectorAll(`#${id} button`).forEach((b) =>
    b.addEventListener('click', () => { setSegment(id, b.dataset.value); onChange(b.dataset.value); }));
}

function wireDrop(zone, input, onFile) {
  if (input) input.addEventListener('change', (e) => { onFile(e.target.files[0]); e.target.value = ''; });
  ['dragenter', 'dragover'].forEach((ev) => zone.addEventListener(ev, (e) => {
    e.preventDefault(); zone.classList.add('is-over');
  }));
  ['dragleave', 'drop'].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove('is-over')));
  zone.addEventListener('drop', (e) => { e.preventDefault(); onFile(e.dataTransfer?.files?.[0]); });
}

async function loadDefaults() {
  try {
    const data = await (await fetch('/api/divein/defaults')).json();
    state.defaults = data.defaults;
    $('defaults').textContent = JSON.stringify(data.defaults, null, 2);
    const missing = Object.entries(data.fonts).filter(([, ok]) => !ok).map(([n]) => n);
    if (missing.length) {
      $('health').textContent = `Missing ${missing.join(', ')}`;
      $('health').classList.add('bad');
    } else {
      $('health').textContent = 'Ready';
    }
    prepareDraftSources();
  } catch {
    $('health').textContent = 'Not connected';
    $('health').classList.add('bad');
  }
}

/** The design itself changed (not just the crop): redraw both. */
function designChanged(delay = 250) {
  scheduleStill(delay);
  scheduleLayers(delay);
}

function setFormat(value) {
  state.format = value;
  $('frame').classList.toggle('is-square', value === 'square');
  clampCrop();
  show($('draft'), false);
  prepareDraftSources();
  designChanged(0);
}

function showMotionOptions() {
  const on = $('rings-on').checked;
  show($('motion-options'), on);
  updateScrub();
}

document.addEventListener('DOMContentLoaded', () => {
  restore();
  showMotionOptions();
  loadDefaults();
  wireDrag();
  wireTimeline();
  wireDrop($('photo-drop'), $('photo-input'), usePhoto);
  wireDrop($('stage'), null, usePhoto);                 // drop straight on the preview
  wireDrop($('audio-drop'), $('audio-input'), useAudio);
  document.addEventListener('paste', (e) => {           // paste a photo from anywhere
    const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith('image/'));
    if (item) { e.preventDefault(); usePhoto(item.getAsFile()); }
  });

  for (const id of ['artist', 'episode']) $(id).addEventListener('input', () => designChanged(250));
  $('starts').addEventListener('input', () => { if (startsFromText() && $('scrub-on').checked) scheduleStill(400); });
  $('clip-seconds').addEventListener('input', () => { save(); renderClips(); drawTimeline(); updateScrub(); });
  $('twitch').addEventListener('change', () => { save(); if ($('scrub-on').checked) scheduleStill(0); });
  $('rings-on').addEventListener('change', () => { showMotionOptions(); designChanged(0); });
  $('zoom').addEventListener('input', () => zoomAt(Number($('zoom').value) || 1));
  $('reset-crop').addEventListener('click', () => { resetCrop(); nudged(); });
  wireSegment('colour-choice', (v) => { state.colour = v; renderClips(); drawTimeline(); designChanged(0); });
  wireSegment('motion-choice', (v) => { state.motion = v; save(); if ($('scrub-on').checked) scheduleStill(0); });
  wireSegment('format-choice', setFormat);

  $('scrub-on').addEventListener('change', () => { updateScrub(); scheduleStill(0); });
  $('scrub').addEventListener('input', () => { updateScrub(); scheduleStill(80); });

  $('apply-overrides').addEventListener('click', () => {
    try {
      state.overrides = JSON.parse($('overrides').value || '{}');
      setError('');
      prepareDraftSources();
      designChanged(0);
    } catch (error) {
      setError(`That isn't valid JSON: ${error.message}`);
    }
  });
  $('clear-overrides').addEventListener('click', () => {
    $('overrides').value = '{}'; state.overrides = {}; prepareDraftSources(); designChanged(0);
  });

  $('preview-btn').addEventListener('click', () => startJob('preview'));
  $('export-btn').addEventListener('click', () => startJob('export'));
  renderClips();
});
