/*
 * Dive In (test) — the page.
 *
 * Every picture you see is drawn by the same Python code that makes the export,
 * so the preview can't drift from the real thing.
 */
'use strict';

const $ = (id) => document.getElementById(id);
const show = (el, on) => { el.hidden = !on; };
const STORE = 'divein-settings-v1';

const state = {
  photo: null,            // {token, width, height}
  audio: null,            // {token, duration}
  crop: { zoom: 1, cx: 0.5, cy: 0.5 },
  format: 'portrait',
  glyph: 'rings',
  overrides: {},
  polling: null,
};

/* ── helpers ─────────────────────────────────────────────────────── */

function setError(message) {
  $('error').textContent = message || '';
  show($('error'), Boolean(message));
}

function timecode(seconds) {
  const s = Math.floor(seconds % 60), m = Math.floor(seconds / 60) % 60;
  const h = Math.floor(seconds / 3600);
  const pad = (n) => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function design(extra = {}) {
  return {
    photo: state.photo && state.photo.token,
    audio: state.audio && state.audio.token,
    crop: state.crop,
    artist: $('artist').value,
    episode: $('episode').value,
    starts: $('starts').value,
    clip_seconds: Number($('clip-seconds').value) || 25,
    glyph: state.glyph,
    twitch: $('twitch').checked,
    debug: $('debug').checked,
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
      starts: $('starts').value, clip: $('clip-seconds').value,
      glyph: state.glyph, twitch: $('twitch').checked, debug: $('debug').checked,
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
  $('starts').value = saved.starts ?? '';
  $('clip-seconds').value = saved.clip ?? 25;
  $('twitch').checked = saved.twitch ?? true;
  $('debug').checked = saved.debug ?? false;
  $('overrides').value = saved.overrides ?? '{}';
  setSegment('glyph-choice', saved.glyph || 'rings');
  state.glyph = saved.glyph || 'rings';
  try { state.overrides = JSON.parse($('overrides').value || '{}'); } catch { state.overrides = {}; }
}

/* ── the live still ──────────────────────────────────────────────── */

let stillSeq = 0;
let stillTimer = null;
let stillController = null;

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

  const extra = { format: state.format };
  if ($('scrub-on').checked && state.audio && state.format === 'portrait') {
    extra.time = Number($('scrub').value);
  }
  try {
    const response = await fetch('/api/divein/still', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(design(extra)), signal: stillController.signal,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || 'The preview could not be drawn.');
    }
    const blob = await response.blob();
    if (seq !== stillSeq) return;               // a newer one is on its way
    const img = $('still');
    const old = img.src;
    img.src = URL.createObjectURL(blob);
    if (old.startsWith('blob:')) URL.revokeObjectURL(old);
    show($('stage-empty'), false);
    show($('player'), false);
    show(img, true);
    setError('');
  } catch (error) {
    if (error.name !== 'AbortError') setError(error.message);
  } finally {
    if (seq === stillSeq) show($('busy'), false);
  }
}

/* ── uploads ─────────────────────────────────────────────────────── */

async function upload(kind, file) {
  const response = await fetch(`/api/divein/upload?kind=${kind}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream', 'X-Filename': file.name },
    body: file,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || 'Upload failed.');
  return payload;
}

async function usePhoto(file) {
  if (!file) return;
  $('photo-label').textContent = `Reading ${file.name}…`;
  try {
    state.photo = await upload('photo', file);
  } catch (error) {
    $('photo-label').textContent = 'Drop a photo here, or click to choose';
    setError(error.message);
    return;
  }
  $('photo-label').textContent = file.name;
  $('photo-drop').classList.add('is-set');
  resetCrop();
  show($('zoom-row'), true);
  scheduleStill(0);
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
  updateScrub();
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

function wireDrag() {
  const img = $('still');
  let last = null;
  img.addEventListener('pointerdown', (e) => {
    if (!state.photo) return;
    last = { x: e.clientX, y: e.clientY };
    img.setPointerCapture(e.pointerId);
    img.classList.add('is-dragging');
  });
  img.addEventListener('pointermove', (e) => {
    if (!last) return;
    const [W, H] = canvasSize();
    const { width: iw, height: ih } = state.photo;
    const perDisplayPx = W / img.clientWidth;            // canvas px per screen px
    const scale = Math.max(W / iw, H / ih) * state.crop.zoom;
    state.crop.cx -= (e.clientX - last.x) * perDisplayPx / scale / iw;
    state.crop.cy -= (e.clientY - last.y) * perDisplayPx / scale / ih;
    last = { x: e.clientX, y: e.clientY };
    clampCrop();
    scheduleStill(60);
  });
  const end = () => { last = null; img.classList.remove('is-dragging'); };
  img.addEventListener('pointerup', end);
  img.addEventListener('pointercancel', end);
  img.addEventListener('wheel', (e) => {
    if (!state.photo) return;
    e.preventDefault();
    const zoom = Math.min(3, Math.max(1, state.crop.zoom * (e.deltaY < 0 ? 1.04 : 1 / 1.04)));
    state.crop.zoom = zoom;
    $('zoom').value = zoom;
    clampCrop();
    scheduleStill(60);
  }, { passive: false });
}

/* ── scrubbing through the clip ──────────────────────────────────── */

function updateScrub() {
  const seconds = Number($('clip-seconds').value) || 25;
  $('scrub').max = seconds;
  show($('scrub-row'), Boolean(state.audio));
  $('scrub').disabled = !$('scrub-on').checked;
  $('scrub-label').textContent = $('scrub-on').checked
    ? `${Number($('scrub').value).toFixed(2)}s into the clip` : 'at rest';
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
      if (s.status === 'error') { setError(s.error || 'That failed.'); return; }
      showFiles(s.files, kind);
    }
  }, 400);
}

function showFiles(files, kind) {
  const list = $('file-list');
  list.innerHTML = '';
  for (const f of files) {
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
  show($('files'), true);
  if (kind === 'preview' && files[0]) playVideo(files[0].url);
}

function playVideo(url) {
  const player = $('player');
  show($('still'), false);
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
  input.addEventListener('change', (e) => { onFile(e.target.files[0]); e.target.value = ''; });
  ['dragenter', 'dragover'].forEach((ev) => zone.addEventListener(ev, (e) => {
    e.preventDefault(); zone.classList.add('is-over');
  }));
  ['dragleave', 'drop'].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove('is-over')));
  zone.addEventListener('drop', (e) => { e.preventDefault(); onFile(e.dataTransfer?.files?.[0]); });
}

async function loadDefaults() {
  try {
    const data = await (await fetch('/api/divein/defaults')).json();
    $('defaults').textContent = JSON.stringify(data.defaults, null, 2);
    const missing = Object.entries(data.fonts).filter(([, ok]) => !ok).map(([n]) => n);
    if (missing.length) {
      $('health').textContent = `Missing ${missing.join(', ')}`;
      $('health').classList.add('bad');
    } else {
      $('health').textContent = 'Ready';
    }
  } catch {
    $('health').textContent = 'Not connected';
    $('health').classList.add('bad');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  restore();
  loadDefaults();
  wireDrag();
  wireDrop($('photo-drop'), $('photo-input'), usePhoto);
  wireDrop($('audio-drop'), $('audio-input'), useAudio);

  for (const id of ['artist', 'episode']) $(id).addEventListener('input', () => scheduleStill(250));
  $('starts').addEventListener('input', () => { save(); if ($('scrub-on').checked) scheduleStill(400); });
  $('clip-seconds').addEventListener('input', () => { save(); updateScrub(); });
  $('twitch').addEventListener('change', () => { save(); if ($('scrub-on').checked) scheduleStill(0); });
  $('debug').addEventListener('change', () => scheduleStill(0));
  $('zoom').addEventListener('input', () => {
    state.crop.zoom = Number($('zoom').value); clampCrop(); scheduleStill(60);
  });
  $('reset-crop').addEventListener('click', () => { resetCrop(); scheduleStill(0); });
  wireSegment('glyph-choice', (v) => { state.glyph = v; scheduleStill(0); });
  wireSegment('format-choice', (v) => { state.format = v; clampCrop(); scheduleStill(0); });

  $('scrub-on').addEventListener('change', () => { updateScrub(); scheduleStill(0); });
  $('scrub').addEventListener('input', () => { updateScrub(); scheduleStill(80); });

  $('apply-overrides').addEventListener('click', () => {
    try {
      state.overrides = JSON.parse($('overrides').value || '{}');
      setError('');
      scheduleStill(0);
    } catch (error) {
      setError(`That isn't valid JSON: ${error.message}`);
    }
  });
  $('clear-overrides').addEventListener('click', () => {
    $('overrides').value = '{}'; state.overrides = {}; scheduleStill(0);
  });

  $('preview-btn').addEventListener('click', () => startJob('preview'));
  $('export-btn').addEventListener('click', () => startJob('export'));
});
