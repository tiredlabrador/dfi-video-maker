/*
 * The viewer: whatever moment the playhead is on.
 *
 * Inside a clip it shows that clip as it will be made (its footage with its
 * framing and the look, or the designed photo). Outside a clip it shows the
 * camera footage at that moment, so you can browse it to find good bits.
 */
'use strict';

/* ── what to show ────────────────────────────────────────────────── */

/** The clip the moment `t` falls in (the selected one if two overlap), or -1. */
function clipAtTime(t) {
  const len = clipLength();
  const inside = (i) => state.starts[i] !== undefined && t >= state.starts[i] && t < state.starts[i] + len;
  if (inside(state.selected)) return state.selected;
  for (let i = 0; i < state.starts.length; i++) if (inside(i)) return i;
  return -1;
}

/** Whether all of clip `i` was filmed. */
function clipFilmed(i) {
  const start = state.starts[i];
  if (start === undefined || !state.footage || !state.audio
      || state.footage.audio !== state.audio.token) return false;
  return state.footage.matches.some((m) => m.mix_from - 0.01 <= start
    && start + clipLength() <= m.mix_to + 0.01);
}

/** What clip `i` will be made from: 'video' (the footage) or 'photo'. */
function clipSource(i) {
  while (state.sources.length < state.starts.length) state.sources.push('auto');
  state.sources.length = state.starts.length;
  if (!clipFilmed(i)) return 'photo';
  return state.sources[i] === 'photo' ? 'photo' : 'video';
}

/** Where moment `t` of the mix is in the footage: {video, time, log}, or null if unfilmed. */
function footageAt(t) {
  if (!state.footage || !state.audio || state.footage.audio !== state.audio.token) return null;
  const m = state.footage.matches.find((f) => t >= f.mix_from && t < f.mix_to);
  return m ? { video: m.video, time: m.offset + m.rate * t, log: Boolean(m.log) } : null;
}

/** What the viewer should show for the playhead: {t, clip, source, at}. */
function viewerTarget() {
  const t = state.playhead;
  const clip = clipAtTime(t);
  const at = footageAt(t);
  let source;
  if (state.format === 'square') source = 'photo';               // the 1:1 JPG is the photo's
  else if (clip >= 0) source = clipSource(clip);
  else source = at ? 'video' : 'photo';
  return { t, clip, source, at: source === 'video' ? at : null };
}

/* ── the viewer, brought up to date ──────────────────────────────── */

/**
 * Show the moment under the playhead. `rough` while dragging: the nearest
 * keyframe of the footage is enough and much quicker.
 */
function updateViewer(rough = false) {
  updateHead();
  updateTime();
  if (state.exact) return;                                       // a rendered preview has the stage
  const v = viewerTarget();
  state.viewing = v;
  $('stage').classList.toggle('is-square', state.format === 'square');
  showFootage(v, rough);
  if (!v.at) {
    if (state.photo && v.source === 'photo') {
      show($('stage-empty'), false);
      show($('frame'), true);
      if (!state.playing) scheduleStill(rough ? 120 : 60);
    } else {
      show($('frame'), false);
      explainEmpty(v);
    }
  }
  updateFrameTools(v);
}

/** The bar above the viewer: the clip under the playhead and what it's made from. */
function updateHead() {
  const clip = state.audio ? clipAtTime(state.playhead) : -1;
  const len = clipLength();
  if (!state.audio) {
    $('clip-title').textContent = 'No mix yet';
    $('clip-range').textContent = '';
  } else if (clip >= 0) {
    const s = state.starts[clip];
    $('clip-title').textContent = `Clip ${clip + 1}`;
    $('clip-range').textContent = `${timecode(s)} – ${timecode(s + len)}`;
  } else {
    $('clip-title').textContent = timecode(state.playhead, true);
    $('clip-range').textContent = state.starts.length ? 'between clips' : '';
  }
  show($('source-seg'), clip >= 0);
  show($('delete-clip'), clip >= 0);
  show($('add-here'), Boolean(state.audio) && clip < 0);
  if (clip >= 0) {
    const filmed = clipFilmed(clip);
    const footageButton = $('source-seg').querySelector('[data-value=video]');
    footageButton.disabled = !filmed;
    footageButton.title = filmed ? '' : 'Not all of this clip was filmed';
    setSegment('source-seg', clipSource(clip));
  }
}

function updateTime() {
  $('time-now').textContent = timecode(state.playhead, true);
  $('time-total').textContent = timecode(duration());
}

/** When there's nothing to show, say why and what to do, rather than look broken. */
function explainEmpty(v) {
  show($('stage-empty'), true);
  let title = 'Start with the mix';
  let text = 'Choose it on the left, or drop it anywhere on this page.';
  if (state.audio && state.format === 'square') {
    title = 'The 1:1 JPG is made from the photo';
    text = 'Choose a photo on the left.';
  } else if (state.audio && !state.footage && !state.photo) {
    title = 'Find the footage, or add a photo';
    text = 'Drop the camera footage’s folder anywhere on this page, or choose a photo.';
  } else if (state.audio && v.clip >= 0 && clipFilmed(v.clip)) {
    title = `Clip ${v.clip + 1} is set to the photo`;
    text = 'Choose a photo on the left, or switch the clip to Footage.';
  } else if (state.audio && state.footage) {
    const covered = state.footage.matches
      .map((m) => `${timecode(m.mix_from)}–${timecode(m.mix_to)}`).join(' and ');
    title = covered ? 'The camera didn’t film this part' : 'This mix wasn’t found in that footage';
    text = covered
      ? `It has ${covered}: the white strip on the timeline. Move there, or choose a photo.`
      : 'Check the folder, or choose a photo.';
  }
  $('empty-title').textContent = title;
  $('empty-text').textContent = text;
}

/** The zoom and reset over the picture: for the clip's footage, or the photo. */
function updateFrameTools(v) {
  const footage = Boolean(v.at) && v.clip >= 0;
  const photo = !v.at && v.source === 'photo' && Boolean(state.photo) && !state.exact;
  show($('frame-tools'), (footage || photo) && !state.playing);
  $('stage').classList.toggle('can-frame', footage);
  const zoom = $('frame-zoom');
  if (footage) {
    $('frame-what').textContent = `Clip ${v.clip + 1}`;
    zoom.max = String(MAX_FOOTAGE_ZOOM);
    zoom.value = String(framingOf(v.clip).zoom);
  } else if (photo) {
    $('frame-what').textContent = 'Photo';
    zoom.max = '3';
    zoom.value = String(state.crop.zoom);
  }
}

/* ── the camera footage ──────────────────────────────────────────── */

function showFootage(v, rough = false) {
  const el = $('footage');
  const at = v.at;
  $('stage').classList.toggle('is-footage', Boolean(at));
  if (at) { applyFraming(v.clip); applyLook(); } else clearInterval(grainTimer);
  updateGrade(at);
  if (!at) { el.pause(); return; }
  const src = `/api/divein/footage/video?audio=${encodeURIComponent(state.audio.token)}&video=${at.video}`;
  if (el.getAttribute('src') !== src) el.src = src;
  if (state.playing) return;                    // playing keeps it in step itself
  el.pause();
  if (rough && el.fastSeek) el.fastSeek(at.time);
  else if (Math.abs(el.currentTime - at.time) > 0.02) el.currentTime = at.time;
}

/* ── framing the footage (per clip) ──────────────────────────────── */

const MAX_FOOTAGE_ZOOM = 2;

/** Clip i's framing: cx/cy 0..1 (left/top to right/bottom), zoom 1..2. */
function framingOf(i) {
  const f = state.framing[i];
  return f ? { ...f } : { cx: 0.5, cy: 0.5, zoom: 1 };
}

/**
 * Show a clip's framing on the plain video, exactly as the export crops it:
 * object-position slides the 4:5 window along the picture, and a zoom
 * scales about the same point, so the visible part is the same window.
 */
function applyFraming(i) {
  const f = framingOf(i);
  const el = $('footage');
  el.style.objectPosition = `${f.cx * 100}% 50%`;
  el.style.transformOrigin = `${f.cx * 100}% ${f.cy * 100}%`;
  el.style.transform = f.zoom > 1 ? `scale(${f.zoom})` : '';
  state.framedClip = i;
  grade.drawn = '';                              // the corrected picture redraws too
}

function setFraming(i, change) {
  if (i < 0) return;
  const f = { ...framingOf(i), ...change };
  f.cx = Math.min(1, Math.max(0, f.cx));
  f.cy = Math.min(1, Math.max(0, f.cy));
  f.zoom = Math.min(MAX_FOOTAGE_ZOOM, Math.max(1, f.zoom));
  state.framing[i] = f;
  applyFraming(i);
  $('frame-zoom').value = String(f.zoom);
  saveSession();
}

function wireFraming() {
  const box = $('footage-frame');
  let drag = null;
  box.addEventListener('pointerdown', (e) => {
    const v = state.viewing;
    if (!v || v.clip < 0 || !$('footage').videoWidth) return;
    drag = { x: e.clientX, y: e.clientY, clip: v.clip, start: framingOf(v.clip) };
    box.setPointerCapture(e.pointerId);
    box.classList.add('is-dragging');
  });
  box.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const el = $('footage'), w = box.clientWidth, h = box.clientHeight;
    // How far the picture can travel each way at this zoom, in screen pixels.
    const roomX = h * (el.videoWidth / el.videoHeight) * drag.start.zoom - w;
    const roomY = h * drag.start.zoom - h;
    setFraming(drag.clip, {
      cx: roomX > 0 ? drag.start.cx - (e.clientX - drag.x) / roomX : drag.start.cx,
      cy: roomY > 0 ? drag.start.cy - (e.clientY - drag.y) / roomY : drag.start.cy,
    });
  });
  const end = () => { drag = null; box.classList.remove('is-dragging'); };
  box.addEventListener('pointerup', end);
  box.addEventListener('pointercancel', end);
  box.addEventListener('wheel', (e) => {
    const v = state.viewing;
    if (!v || v.clip < 0) return;
    e.preventDefault();
    setFraming(v.clip, { zoom: framingOf(v.clip).zoom * Math.exp(-e.deltaY * 0.002) });
  }, { passive: false });

  // The zoom slider and reset act on whatever the viewer is framing.
  $('frame-zoom').addEventListener('input', () => {
    const v = state.viewing, zoom = Number($('frame-zoom').value) || 1;
    if (v && v.at && v.clip >= 0) setFraming(v.clip, { zoom });
    else zoomAt(zoom);
  });
  $('frame-reset').addEventListener('click', () => {
    const v = state.viewing;
    if (v && v.at && v.clip >= 0) setFraming(v.clip, { cx: 0.5, cy: 0.5, zoom: 1 });
    else { resetCrop(); nudged(); }
  });
}

/* ── the photo: the exact still (drawn by the server) ────────────── */

let stillSeq = 0;
let stillTimer = null;
let stillController = null;
// Identifies this open page, so the server counts its requests separately
// from any page it replaced (a reload starts the count again from 1).
const CLIENT = Math.random().toString(36).slice(2) + Date.now().toString(36);

function scheduleStill(delay = 120) {
  clearTimeout(stillTimer);
  stillTimer = setTimeout(refreshStill, delay);
}

async function refreshStill() {
  if (!state.photo) return;
  const seq = ++stillSeq;
  if (stillController) stillController.abort();
  stillController = new AbortController();
  show($('busy'), true);

  // Inside a photo clip, paused part-way: show the rings at that moment.
  const extra = { format: state.format, seq, client: CLIENT };
  const v = state.viewing;
  if (v && v.clip >= 0 && state.format === 'portrait' && state.audio) {
    extra.preview_index = v.clip;
    const into = state.playhead - state.starts[v.clip];
    if (into > 0.05 && $('rings-on').checked) extra.time = into;
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
  } catch (error) {
    if (error.name !== 'AbortError') setError(error.message);
  } finally {
    if (seq === stillSeq) show($('busy'), false);
  }
}

/* ── the photo: a quick draft drawn here while dragging ──────────── */

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

function canvasSize() {
  return state.format === 'square' ? [1080, 1080] : [1080, 1350];
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
  state.idleTimer = setTimeout(() => { state.interacting = false; scheduleStill(0); save(); saveSession(); }, 180);
}

/* ── the photo's framing: drag and zoom (all photo clips) ────────── */

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
  $('frame-zoom').value = 1;
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
  $('frame-zoom').value = zoom;
  nudged();
}

function wirePhotoDrag() {
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

/** The design itself changed (not just the framing): redraw both. */
function designChanged(delay = 250) {
  save();
  scheduleStill(delay);
  scheduleLayers(delay);
}

function setFormat(value) {
  state.format = value;
  clampCrop();
  show($('draft'), false);
  prepareDraftSources();
  updateViewer();
  designChanged(0);
}

/* ── a rendered preview, played in the viewer ────────────────────── */

function playExact(url) {
  pause();
  state.exact = true;
  $('stage').classList.remove('is-footage');
  $('footage').pause();
  updateGrade(null);
  show($('frame'), false);
  show($('stage-empty'), false);
  show($('frame-tools'), false);
  const player = $('player');
  player.src = url;
  show(player, true);
  show($('exact-bar'), true);
  player.play().catch(() => {});
}

function closeExact() {
  if (!state.exact) return;
  state.exact = false;
  const player = $('player');
  player.pause();
  show(player, false);
  show($('exact-bar'), false);
  updateViewer();
}
