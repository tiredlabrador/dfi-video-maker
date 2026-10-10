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
  motion: 'level',
  overrides: {},
  defaults: null,
  polling: null,
  // The mix timeline
  starts: [],             // clip start times, in seconds
  selected: 0,            // which clip the preview and "moment" use
  overview: null,         // {duration, peaks}
  footage: null,          // {audio, matches}: where the mix was filmed
  transitions: [],        // rough seconds where one record gives way to the next
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
    starts: state.starts,                       // exact seconds, not rounded
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
      // Saved under a new name: an older saved 'kicks' predates Volume
      // becoming the default, so it's left behind rather than carried over.
      colour: state.colour, rings_follow: state.motion, twitch: $('twitch').checked,
      overrides: $('overrides').value, footage_folder: $('footage-folder').value,
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
  $('footage-folder').value = saved.footage_folder ?? '';
  state.colour = saved.colour || 'yellow';
  state.motion = saved.rings_follow || 'level';
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
  state.transitions = [];
  state.footage = null;
  show($('lut-row'), false);
  showFootage();
  $('footage-status').textContent = FOOTAGE_HINT;
  show($('footage-row'), true);
  stopListening();
  state.starts = state.starts.filter((s) => s < state.audio.duration);
  show($('timeline'), true);
  $('timeline-hover').textContent = 'Drawing the mix…';
  startsChanged();
  const token = state.audio.token;
  try {
    const response = await fetch(`/api/divein/overview?audio=${encodeURIComponent(token)}`);
    const overview = response.ok ? await response.json() : null;
    // Only if it's still the mix on screen, and only if it's a real answer.
    if (state.audio && state.audio.token === token && overview && Array.isArray(overview.peaks)) {
      state.overview = overview;
    }
  } catch { /* the timeline still works without the shape */ }
  findTransitions(token);                 // a few seconds more; drawn when ready
  $('timeline-hover').textContent = timelineHint();
  drawTimeline();
}

/* ── the mix timeline ────────────────────────────────────────────── */

function timelineHint() {
  return 'Click to add a clip. Drag a clip to move it.'
    + (state.transitions.length ? ' Dotted lines: likely transitions.' : '');
}

async function findTransitions(token) {
  try {
    const response = await fetch(`/api/divein/transitions?audio=${encodeURIComponent(token)}`);
    const found = response.ok ? await response.json() : null;
    if (!state.audio || state.audio.token !== token || !found || !Array.isArray(found.times)) return;
    state.transitions = found.times;
    $('timeline-hover').textContent = timelineHint();
    drawTimeline();
  } catch { /* the timeline works without them */ }
}

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
  const peaks = (state.overview && state.overview.peaks) || [];
  // The mix's loudness, as a mirrored shape.
  ctx.fillStyle = 'rgba(255,255,255,0.28)';
  for (let x = 0; x < w; x++) {
    const v = peaks.length ? peaks[Math.min(peaks.length - 1, Math.floor(x / w * peaks.length))] : 0.15;
    const bar = Math.max(1, v * (h - 12));
    ctx.fillRect(x, (h - bar) / 2, 1, bar);
  }
  // Likely transitions: dotted lines.
  ctx.strokeStyle = 'rgba(255,255,255,0.55)';
  ctx.lineWidth = 1;
  ctx.setLineDash([2, 3]);
  for (const t of state.transitions) {
    const x = Math.round(t / total * w) + 0.5;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
  }
  ctx.setLineDash([]);
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
  // The parts of the mix the camera filmed: a strip along the bottom.
  if (state.footage && state.audio && state.footage.audio === state.audio.token) {
    ctx.fillStyle = '#fff';
    for (const m of state.footage.matches) {
      const x0 = m.mix_from / total * w, x1 = m.mix_to / total * w;
      ctx.fillRect(x0, h - 4, Math.max(2, x1 - x0), 4);
    }
  }
  // Where playback is.
  const player = $('listen');
  if (state.listening !== null && !player.paused) {
    const x = player.currentTime / total * w;
    ctx.fillStyle = '#fff';
    ctx.fillRect(x, 0, 2, h);
  }
}

/**
 * The clip under `x`, or -1. On a long mix a clip is only a few pixels wide,
 * so each one can be grabbed anywhere from just before its line to the end
 * of its number tag; where two overlap, the one whose line is nearer wins.
 */
function clipAt(x, w) {
  const total = duration(), len = clipLength();
  let best = -1, nearest = Infinity;
  state.starts.forEach((start, i) => {
    const x0 = start / total * w, x1 = (start + len) / total * w;
    if (x >= x0 - 8 && x <= Math.max(x1, x0 + 22) && Math.abs(x - x0) < nearest) {
      best = i; nearest = Math.abs(x - x0);
    }
  });
  return best;
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
    const box0 = canvas.getBoundingClientRect();
    const near = state.transitions.some((s) => Math.abs(s - t) / duration() * box0.width < 5);
    $('timeline-hover').textContent = duration()
      ? `${timecode(t)}${near ? ' — likely transition' : ''} — click to add a clip, drag to move one` : '';
    if (!drag) {
      const box = canvas.getBoundingClientRect();
      canvas.classList.toggle('is-over-clip', clipAt(e.clientX - box.left, box.width) >= 0);
      return;
    }
    const max = Math.max(0, duration() - clipLength());
    state.starts[drag.i] = Math.round(Math.min(max, Math.max(0, t - drag.offset)));
    drag.moved = true;
    renderClips();
    drawTimeline();
    if (drag.i === state.selected) showFootage(true);
  });
  const end = () => {
    if (drag && drag.moved) startsChanged();
    drag = null;
    canvas.classList.remove('is-moving');
  };
  canvas.addEventListener('pointerup', end);
  canvas.addEventListener('pointercancel', end);
  canvas.addEventListener('pointerleave', () => {
    if (!drag) $('timeline-hover').textContent = timelineHint();
  });
  window.addEventListener('resize', drawTimeline);
}

function selectClip(i) {
  state.selected = Math.max(0, Math.min(i, state.starts.length - 1));
  renderClips();
  drawTimeline();
  showFootage();
  if ($('scrub-on').checked) scheduleStill(0);
}

/** The clip list changed: update the typed times, the list and the picture. */
function startsChanged() {
  $('starts').value = state.starts.map(timecode).join(', ');
  if (state.selected >= state.starts.length) state.selected = Math.max(0, state.starts.length - 1);
  renderClips();
  drawTimeline();
  updateScrub();
  showFootage();
  if ($('scrub-on').checked) scheduleStill(0);
}

function startsFromText() {
  const parts = $('starts').value.split(/[,;\n]+/).map((p) => p.trim()).filter(Boolean);
  const times = parts.map(parseTime);
  if (times.some((t) => t === null)) return false;      // keep typing
  stopListening();
  state.starts = times;
  if (state.selected >= times.length) state.selected = 0;
  renderClips();
  drawTimeline();
  updateScrub();
  showFootage();
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
      else if (state.listening !== null && state.listening > i) state.listening -= 1;
      if (state.selected > i) state.selected -= 1;
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
    followListening();
    drawTimeline();
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  renderClips();
}

function stopListening() {
  $('listen').pause();
  state.listening = null;
  showFootage();                          // back to the clip's first frame
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
  if ($('starts').value.trim() && !startsFromText()) {
    setError('One of the typed start times can’t be read. Use mm:ss or h:mm:ss, separated by commas.');
    return;
  }
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
  $('stage').classList.remove('is-footage');      // the finished video takes the stage
  $('footage').pause();
  updateGrade(null);
  show($('frame'), false);
  show($('stage-empty'), false);
  player.src = url;
  show(player, true);
  player.play().catch(() => {});
}

/* ── the camera footage in the preview ───────────────────────────── */

/** Where moment `t` of the mix is in the footage: {video, time}, or null if unfilmed. */
function footageAt(t) {
  if (!state.footage || !state.audio || state.footage.audio !== state.audio.token) return null;
  const m = state.footage.matches.find((f) => t >= f.mix_from && t < f.mix_to);
  return m ? { video: m.video, time: m.offset + m.rate * t, log: Boolean(m.log) } : null;
}

/**
 * Show the footage at the selected clip's start, if that moment was filmed.
 * `rough` while dragging: the nearest keyframe is enough and much quicker.
 */
function showFootage(rough = false) {
  const el = $('footage');
  const start = state.starts.length ? state.starts[state.selected] : null;
  const at = start === null || state.format !== 'portrait' ? null : footageAt(start);
  $('stage').classList.toggle('is-footage', Boolean(at));
  updateGrade(at);
  if (!at) { el.pause(); return; }
  $('player').pause();
  const src = `/api/divein/footage/video?audio=${encodeURIComponent(state.audio.token)}&video=${at.video}`;
  if (el.getAttribute('src') !== src) el.src = src;
  if (state.listening !== null) return;              // listening keeps it in step itself
  el.pause();
  if (rough && el.fastSeek) el.fastSeek(at.time);
  else el.currentTime = at.time;
}

/**
 * While a clip plays, keep the footage with the mix (re-aligning if it slips).
 * A jump in a 17GB file can take a moment: never ask for another while one is
 * under way, or the picture keeps restarting its jump and never plays.
 */
let lastAlign = 0;
function followListening() {
  const el = $('footage');
  if (!$('stage').classList.contains('is-footage')) return;
  const at = footageAt($('listen').currentTime);
  if (!at) { el.pause(); return; }
  const now = performance.now();
  // A jump that's taken over two seconds has stalled (likely after the tab
  // sat in the background): ask again rather than wait for ever.
  const stalled = el.seeking && now - lastAlign > 2000;
  if ((!el.seeking && Math.abs(el.currentTime - at.time) > 0.2 && now - lastAlign > 600) || stalled) {
    el.currentTime = at.time;
    lastAlign = now;
  }
  if (el.paused) el.play().catch(() => {});
}

/** If the browser gives up on the footage (it can, after the tab sat in the
 *  background), load it afresh and go back to the right moment. */
let footageRetries = 0;
function wireFootageRecovery() {
  const el = $('footage');
  el.addEventListener('loadeddata', () => { footageRetries = 0; });
  el.addEventListener('error', () => {
    if (!el.getAttribute('src')) return;
    // 4 = this browser can't play the file at all: retrying won't help.
    if (el.error && el.error.code === 4) {
      $('footage-status').textContent = 'This browser can’t play this footage. Try Safari.';
      return;
    }
    if (footageRetries >= 3) {
      $('footage-status').textContent = 'The footage stopped loading. Is the drive still plugged in?';
      return;
    }
    footageRetries += 1;
    setTimeout(() => { el.load(); showFootage(); }, 500 * footageRetries);
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) return;
    if (state.listening === null) { showFootage(); return; }
    lastAlign = 0;                          // back in view: line up again straight away
    followListening();
  });
}

/* ── correcting D-Log colours (DJI's colour file, applied by the GPU) ── */

const grade = { gl: null, video: null, size: 0, crop: null, ready: false, problem: '', loop: null, drawn: '' };

/** Read DJI's .cube file: its size, and its colours (red changing fastest). */
function parseCube(text) {
  let size = 0;
  const values = [];
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    if (t.startsWith('LUT_3D_SIZE')) { size = parseInt(t.split(/\s+/)[1], 10); continue; }
    if (/^[A-Z_]/.test(t)) continue;                     // TITLE, DOMAIN_MIN and the like
    const parts = t.split(/\s+/).map(Number);
    if (parts.length === 3 && parts.every(Number.isFinite)) values.push(...parts);
  }
  if (!size || values.length !== size * size * size * 3) throw new Error('That colour file isn’t a 3D LUT.');
  return { size, data: new Float32Array(values) };
}

function compile(gl, type, source) {
  const s = gl.createShader(type);
  gl.shaderSource(s, source);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
  return s;
}

/** Fetch the colour file once and set up the drawing. False if it can't be done. */
async function loadGrade() {
  if (grade.ready || grade.problem) return grade.ready;
  try {
    const response = await fetch('/api/divein/lut');
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error
      || 'The colour file couldn’t be loaded.');
    const lut = parseCube(await response.text());
    const gl = $('graded').getContext('webgl2', { premultipliedAlpha: false });
    if (!gl) throw new Error('This browser can’t correct colours live. The export still will.');
    const program = gl.createProgram();
    gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, `#version 300 es
      in vec2 corner; out vec2 uv;
      void main() { uv = corner * 0.5 + 0.5; gl_Position = vec4(corner, 0.0, 1.0); }`));
    gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, `#version 300 es
      precision highp float; precision highp sampler3D;
      in vec2 uv; out vec4 colour;
      uniform sampler2D picture; uniform sampler3D lut; uniform vec4 crop; uniform float size;
      void main() {
        vec3 c = texture(picture, crop.xy + uv * crop.zw).rgb;
        colour = vec4(texture(lut, c * ((size - 1.0) / size) + 0.5 / size).rgb, 1.0);
      }`));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    gl.useProgram(program);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const corner = gl.getAttribLocation(program, 'corner');
    gl.enableVertexAttribArray(corner);
    gl.vertexAttribPointer(corner, 2, gl.FLOAT, false, 0, 0);

    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_3D, gl.createTexture());
    gl.texImage3D(gl.TEXTURE_3D, 0, gl.RGB16F, lut.size, lut.size, lut.size, 0,
      gl.RGB, gl.FLOAT, lut.data);
    for (const [k, v] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR],
      [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE],
      [gl.TEXTURE_WRAP_R, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_3D, k, v);
    gl.uniform1i(gl.getUniformLocation(program, 'lut'), 1);
    gl.uniform1f(gl.getUniformLocation(program, 'size'), lut.size);

    gl.activeTexture(gl.TEXTURE0);
    grade.video = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, grade.video);
    for (const [k, v] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR],
      [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) {
      gl.texParameteri(gl.TEXTURE_2D, k, v);
    }
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    gl.uniform1i(gl.getUniformLocation(program, 'picture'), 0);
    grade.crop = gl.getUniformLocation(program, 'crop');
    grade.gl = gl;
    grade.ready = true;
  } catch (error) {
    grade.problem = error.message || 'The colours couldn’t be corrected.';
  }
  return grade.ready;
}

/** Draw the current video frame, colour-corrected, cropped to 4:5 from the centre. */
function drawGraded() {
  const el = $('footage'), canvas = $('graded'), gl = grade.gl;
  if (!gl || el.readyState < 2 || !el.videoWidth) return;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.round(canvas.clientWidth * ratio), h = Math.round(canvas.clientHeight * ratio);
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  gl.viewport(0, 0, w, h);
  gl.bindTexture(gl.TEXTURE_2D, grade.video);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, el);
  // The same crop as the plain video's object-fit: cover.
  const frame = w / h, picture = el.videoWidth / el.videoHeight;
  const uw = picture > frame ? frame / picture : 1, vh = picture > frame ? 1 : picture / frame;
  gl.uniform4f(grade.crop, (1 - uw) / 2, (1 - vh) / 2, uw, vh);
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
}

/** Redraw only when there's a new frame to show (or the size changed). */
function gradeLoop(loop) {
  if (grade.loop !== loop) return;                   // a newer loop has taken over
  const el = $('footage'), canvas = $('graded');
  const key = `${el.currentTime}|${canvas.clientWidth}|${canvas.clientHeight}|${el.readyState}`;
  if (key !== grade.drawn) { drawGraded(); grade.drawn = key; }
  requestAnimationFrame(() => gradeLoop(loop));
}

/** Correct the colours when the footage on show was filmed flat and it's wanted. */
let gradeRequest = 0;
async function updateGrade(at) {
  const request = ++gradeRequest;
  const wanted = Boolean(at && at.log && $('lut-on').checked);
  if (wanted && !(await loadGrade())) {
    $('footage-status').textContent = grade.problem;
  }
  if (request !== gradeRequest) return;              // a newer call has decided
  const on = wanted && grade.ready;
  show($('graded'), on);
  if (on && !grade.loop) {
    grade.loop = {};
    grade.drawn = '';
    const loop = grade.loop;
    requestAnimationFrame(() => gradeLoop(loop));
  }
  if (!on) grade.loop = null;
}

/* ── finding the mix in camera footage ───────────────────────────── */

const FOOTAGE_HINT = 'The folder with the camera\u2019s videos. In Finder, right-click it, '
  + 'hold Option, and choose Copy as Pathname.';
let footagePolling = null;

async function findFootage() {
  setError('');
  if (!state.audio) { setError('Choose the mix first.'); return; }
  const folder = $('footage-folder').value.trim();
  if (!folder) { setError('Paste the footage folder\u2019s location first.'); return; }
  save();
  const token = state.audio.token;
  state.footage = null;                   // the old strip belongs to the old folder
  if (!grade.ready) grade.problem = '';    // the colour file may have been added since
  show($('lut-row'), false);
  drawTimeline();
  showFootage();
  const status = $('footage-status');
  const button = $('footage-btn');
  button.disabled = true;
  status.textContent = 'Starting\u2026';
  let job;
  try {
    job = await (await postJSON('/api/divein/footage', { audio: token, folder })).json();
  } catch (error) {
    setError(error.message); status.textContent = FOOTAGE_HINT; button.disabled = false;
    return;
  }
  clearInterval(footagePolling);
  footagePolling = setInterval(async () => {
    let s;
    try { s = await (await fetch(`/api/divein/jobs/${job.id}`)).json(); } catch { return; }
    const current = state.audio && state.audio.token === token;
    if (s.status !== 'done' && s.status !== 'error') {
      if (current) {
        // No percentage: reading a video's sound is one long step.
        status.textContent = `${s.message || 'Listening'}\u2026 `
          + '(the first look at each video takes a minute or two)';
      }
      return;
    }
    clearInterval(footagePolling);
    button.disabled = false;
    if (!current) return;                 // a different mix was chosen meanwhile
    if (s.status === 'error') {
      setError(s.error || 'Looking through the footage failed.');
      status.textContent = FOOTAGE_HINT;
      return;
    }
    state.footage = { audio: token, matches: s.footage.matches };
    show($('lut-row'), s.footage.matches.some((m) => m.log));
    status.textContent = s.footage.matches.length
      ? `${s.footage.message} The white strip under the mix shows the filmed parts.`
      : s.footage.message;
    drawTimeline();
    showFootage();
  }, 500);
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
  showFootage();
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
  wireFootageRecovery();
  wireDrop($('photo-drop'), $('photo-input'), usePhoto);
  wireDrop($('stage'), null, usePhoto);                 // drop straight on the preview
  wireDrop($('audio-drop'), $('audio-input'), useAudio);
  document.addEventListener('paste', (e) => {           // paste a photo from anywhere...
    const items = [...(e.clipboardData?.items || [])];
    const typing = e.target.closest && e.target.closest('input, textarea');
    // ...except into a text box when there's text to paste (copied cells from
    // a spreadsheet carry a picture as well as the text).
    if (typing && items.some((i) => i.type === 'text/plain')) return;
    const item = items.find((i) => i.type.startsWith('image/'));
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

  $('footage-btn').addEventListener('click', findFootage);
  $('lut-on').addEventListener('change', () => showFootage());
  $('footage-folder').addEventListener('keydown', (e) => { if (e.key === 'Enter') findFootage(); });
  $('preview-btn').addEventListener('click', () => startJob('preview'));
  $('export-btn').addEventListener('click', () => startJob('export'));
  renderClips();
});
