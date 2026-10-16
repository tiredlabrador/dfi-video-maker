/*
 * The timeline, docked along the bottom like a video editor's: the whole mix
 * (click to go there) and a close-up with a ruler and a playhead.
 *
 *   click or drag    move the playhead (the viewer shows that moment)
 *   double-click     add a clip there (or press A, or "+ Clip")
 *   drag a clip      move it
 *   scroll / pinch   move along / zoom
 */
'use strict';

const DETAIL_PER_SECOND = 8;              // close-up waveform points a second
const RULER = 18;                         // the close-up's ruler band, in pixels

/* ── the clips ───────────────────────────────────────────────────── */

const tenth = (t) => Math.round(t * 10) / 10;

function addClipAt(t) {
  if (!state.audio || !duration()) return;
  const start = Math.max(0, Math.min(tenth(t), duration() - clipLength()));
  state.starts.push(start);
  state.sources.push('auto');
  state.framing.push(undefined);
  state.selected = state.starts.length - 1;
  seek(start);
  startsChanged();
}

function deleteClip(i) {
  if (i < 0 || i >= state.starts.length) return;
  const removed = { i, start: state.starts[i], source: state.sources[i], framing: state.framing[i] };
  state.starts.splice(i, 1);
  state.sources.splice(i, 1);
  state.framing.splice(i, 1);
  if (state.selected >= i) state.selected = Math.max(0, state.selected - 1);
  startsChanged();
  notify(`Clip ${i + 1} removed.`, 'ok', false, { label: 'Undo', run: () => {
    state.starts.splice(removed.i, 0, removed.start);
    state.sources.splice(removed.i, 0, removed.source);
    state.framing.splice(removed.i, 0, removed.framing);
    selectClip(removed.i);
    startsChanged();
  } });
}

/** Make clip `i` the one the clip controls act on, and go to its start. */
function selectClip(i, go = true) {
  if (i < 0 || i >= state.starts.length) return;
  state.selected = i;
  if (go) seek(state.starts[i], { reveal: true });
  renderChips();
  drawTimeline();
  updateViewer();
}

/** The clip list changed: update everything that shows it. */
function startsChanged() {
  $('starts').value = state.starts.map((s) => timecode(s)).join(', ');
  if (state.selected >= state.starts.length) state.selected = Math.max(0, state.starts.length - 1);
  renderChips();
  drawTimeline();
  updateViewer();
  refreshExport();
  saveSession();
}

function startsFromText() {
  const parts = $('starts').value.split(/[,;\n]+/).map((p) => p.trim()).filter(Boolean);
  const times = parts.map(parseTime);
  if (times.some((t) => t === null)) return false;      // keep typing
  if (times.length !== state.starts.length) { state.sources = []; state.framing = []; }
  state.starts = times;
  if (state.selected >= times.length) state.selected = 0;
  renderChips();
  drawTimeline();
  updateViewer();
  saveSession();
  return true;
}

/** The row of clip buttons above the timeline: quick to jump between. */
function renderChips() {
  const box = $('clip-chips');
  box.innerHTML = '';
  const accent = ACCENTS[state.colour] || ACCENTS.yellow;
  state.starts.forEach((start, i) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = `clip-chip${i === state.selected ? ' is-selected' : ''}`;
    const source = clipSource(i);
    b.title = `Clip ${i + 1}: ${source === 'video' ? 'footage' : 'photo'}`;
    b.innerHTML = `<b style="background:${accent}">${i + 1}</b>${timecode(start)}`
      + `<svg><use href="#i-${source === 'video' ? 'film' : 'photo'}"/></svg>`;
    b.addEventListener('click', () => selectClip(i));
    box.append(b);
  });
}

/* ── loading what the timeline shows ─────────────────────────────── */

async function findTransitions(token) {
  try {
    const response = await fetch(`/api/divein/transitions?audio=${encodeURIComponent(token)}`);
    const found = response.ok ? await response.json() : null;
    if (!state.audio || state.audio.token !== token || !found || !Array.isArray(found.times)) return;
    state.transitions = found.times;
    drawTimeline();
  } catch { /* the timeline works without them */ }
}

/** The close-up's finer waveform: a few seconds' work, drawn when it arrives. */
async function findDetail(token) {
  try {
    const response = await fetch(`/api/divein/overview?audio=${encodeURIComponent(token)}`
      + `&per_second=${DETAIL_PER_SECOND}`);
    const detail = response.ok ? await response.json() : null;
    if (!state.audio || state.audio.token !== token || !detail || !Array.isArray(detail.peaks)) return;
    state.detail = detail;
    drawTimeline();
  } catch { /* the close-up uses the coarse shape instead */ }
}

/* ── the view ────────────────────────────────────────────────────── */

/** Keep the close-up inside the mix, and no wider than it. */
function clampView() {
  const total = duration();
  state.view.span = Math.min(state.view.span, total || state.view.span);
  state.view.start = Math.max(0, Math.min(state.view.start, Math.max(0, total - state.view.span)));
}

function centreView(t) {
  state.view.start = t - state.view.span / 2;
  clampView();
}

/** Bring `t` into the close-up, moving it only if needed. */
function revealTime(t, margin = 0.08) {
  const { start, span } = state.view;
  if (t < start + span * margin || t > start + span * (1 - margin)) centreView(t);
}

/*
 * Zoom: the slider runs from the whole mix (left) to 20 seconds (right). Each
 * step zooms by the same proportion, so it feels even all the way along.
 */
const CLOSEST = 20;                          // seconds across, zoomed right in

function widest() { return Math.max(CLOSEST, duration() || 3600); }

function spanFromSlider(v) { return widest() * (CLOSEST / widest()) ** (v / 1000); }

function sliderFromSpan(span) {
  return Math.log(widest() / span) / Math.log(widest() / CLOSEST) * 1000 || 0;
}

/** Zoom to `span` seconds, keeping `around` (the playhead, unless said) where it is on screen. */
function setZoom(span, around = state.playhead) {
  const { start, span: was } = state.view;
  const at = around >= start && around <= start + was ? (around - start) / was : 0.5;
  state.view.span = Math.max(CLOSEST, Math.min(widest(), span));
  state.view.start = around - at * state.view.span;
  clampView();
  drawTimeline();
}

/** Zoom in (+1) or out (−1) a notch. */
function zoomStep(direction) {
  setZoom(state.view.span / 1.6 ** direction);
}

/* ── drawing ─────────────────────────────────────────────────────── */

/** Size a canvas for the screen (only when that changed) and hand back its drawing context. */
function prepare(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w) return null;
  const pw = Math.round(w * ratio), ph = Math.round(h * ratio);
  if (canvas.width !== pw || canvas.height !== ph) { canvas.width = pw; canvas.height = ph; }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h, ratio };
}

/*
 * What doesn't move while playing (the ruler, the mix's shape, the filmed
 * strip, the transitions) is drawn once into a layer and reused; each tick
 * only adds the clips and the playhead on top.
 */
const backdrops = new WeakMap();                    // canvas -> {inputs, layer}

function backdrop(canvas, box, t0, t1, peaks, perSecond, close) {
  const { w, h, ratio } = box;
  const footage = state.footage && state.audio && state.footage.audio === state.audio.token
    ? state.footage : null;
  const inputs = [t0, t1, w, h, ratio, peaks, perSecond, footage, state.transitions];
  const kept = backdrops.get(canvas);
  if (kept && kept.inputs.every((v, i) => v === inputs[i])) return kept.layer;
  const layer = (kept && kept.layer) || document.createElement('canvas');
  layer.width = Math.round(w * ratio); layer.height = Math.round(h * ratio);
  const ctx = layer.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  const top = close ? RULER : 0;
  const span = t1 - t0;
  const xOf = (t) => (t - t0) / span * w;
  const th = h - top;

  if (close) {                                      // the ruler
    ctx.fillStyle = '#141416';
    ctx.fillRect(0, 0, w, RULER);
    const step = span <= 90 ? 10 : span <= 300 ? 30 : span <= 1200 ? 60 : 300;
    ctx.fillStyle = 'rgba(255,255,255,0.55)';
    ctx.font = '10px -apple-system, sans-serif';
    for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) {
      const x = xOf(t);
      ctx.fillRect(x, RULER - 6, 1, 6);
      ctx.fillText(timecode(t), x + 3, 11);
    }
  }
  ctx.fillStyle = 'rgba(255,255,255,0.28)';         // the mix's loudness, as one shape
  ctx.beginPath();
  for (let x = 0; x < w; x++) {
    const i = Math.floor((t0 + (x + 0.5) / w * span) * perSecond);
    const v = peaks.length ? peaks[Math.max(0, Math.min(peaks.length - 1, i))] : 0.15;
    const bar = Math.max(1, v * (th - 10));
    ctx.rect(x, top + (th - bar) / 2, 1, bar);
  }
  ctx.fill();
  if (footage) {
    ctx.fillStyle = '#fff';                         // filmed: a strip along the bottom
    for (const m of footage.matches) {
      const a = xOf(m.mix_from), b = xOf(m.mix_to);
      if (b > 0 && a < w) ctx.fillRect(a, h - 3, Math.max(2, b - a), 3);
    }
  }
  ctx.strokeStyle = 'rgba(255,255,255,0.5)';        // likely transitions: dotted
  ctx.lineWidth = 1;
  ctx.setLineDash([2, 3]);
  ctx.beginPath();
  for (const t of state.transitions) {
    if (t < t0 || t > t1) continue;
    const x = Math.round(xOf(t)) + 0.5;
    ctx.moveTo(x, top); ctx.lineTo(x, h);
  }
  ctx.stroke();
  ctx.setLineDash([]);
  backdrops.set(canvas, { inputs, layer });
  return layer;
}

/** Draw moments t0..t1 of the mix: shape, filmed parts, transitions, clips, playhead. */
function drawStrip(canvas, t0, t1, peaks, perSecond, close) {
  const box = prepare(canvas);
  if (!box) return null;
  const { ctx, w, h } = box;
  ctx.drawImage(backdrop(canvas, box, t0, t1, peaks, perSecond, close), 0, 0, w, h);
  const top = close ? RULER : 0;
  const span = t1 - t0;
  const xOf = (t) => (t - t0) / span * w;
  const accent = ACCENTS[state.colour] || ACCENTS.yellow;
  const th = h - top;
  const len = clipLength();
  state.starts.forEach((start, i) => {
    if (start + len < t0 || start > t1) return;
    const a = xOf(start), b = xOf(start + len);
    ctx.fillStyle = accent;
    ctx.globalAlpha = i === state.selected ? 0.42 : 0.24;
    ctx.fillRect(a, top, Math.max(2, b - a), th);
    ctx.globalAlpha = 1;
    ctx.fillRect(a, top, 2, th);
    if (close) {                                    // number tags only where there's room
      ctx.fillRect(a, top, 18, 16);
      ctx.fillStyle = '#000';
      ctx.font = 'bold 11px -apple-system, sans-serif';
      ctx.fillText(String(i + 1), a + (i + 1 > 9 ? 2.5 : 5.5), top + 12);
    }
  });
  if (state.audio) {                                // the playhead
    const x = xOf(state.playhead);
    ctx.fillStyle = '#fff';
    ctx.fillRect(Math.round(x) - 1, 0, 2, h);
    if (close) {
      ctx.beginPath();
      ctx.moveTo(x - 6, 0); ctx.lineTo(x + 6, 0); ctx.lineTo(x, 8); ctx.closePath(); ctx.fill();
    }
  }
  return { ctx, w, h, xOf };
}

function drawTimeline() {
  const total = duration();
  show($('dock-empty'), !total);
  if (!total) return;
  clampView();
  const { start, span } = state.view;
  $('zoom-slider').value = String(Math.round(sliderFromSpan(span)));
  const coarse = (state.overview && state.overview.peaks) || [];
  const whole = drawStrip($('overview-canvas'), 0, total, coarse, coarse.length / total, false);
  if (whole) {                                      // the close-up's window on the whole
    const a = whole.xOf(start), b = whole.xOf(start + span);
    whole.ctx.strokeStyle = 'rgba(255,255,255,.8)';
    whole.ctx.lineWidth = 1.5;
    whole.ctx.strokeRect(a + 0.75, 0.75, Math.max(3, b - a) - 1.5, whole.h - 1.5);
  }
  const fine = state.detail ? state.detail.peaks : coarse;
  const rate = state.detail ? state.detail.peaks.length / total : coarse.length / total;
  drawStrip($('closeup-canvas'), start, start + span, fine, rate, true);
}

/* ── pointing at it ──────────────────────────────────────────────── */

/** The clip under `x` in the close-up, or -1 (nearest line wins where they overlap). */
function clipAt(x, w) {
  const { start: v0, span } = state.view, len = clipLength();
  let best = -1, nearest = Infinity;
  state.starts.forEach((start, i) => {
    const x0 = (start - v0) / span * w, x1 = (start + len - v0) / span * w;
    if (x >= x0 - 6 && x <= Math.max(x1, x0 + 20) && Math.abs(x - x0) < nearest) {
      best = i; nearest = Math.abs(x - x0);
    }
  });
  return best;
}

function wireTimeline() {
  // The whole mix: click or drag to go there.
  const whole = $('overview-canvas');
  let scrubbing = false;
  const goTo = (e) => {
    const box = whole.getBoundingClientRect();
    const t = Math.max(0, Math.min(1, (e.clientX - box.left) / box.width)) * duration();
    centreView(t);
    seek(t);
  };
  let resume = false;
  whole.addEventListener('pointerdown', (e) => {
    if (!duration()) return;
    resume = state.playing;
    if (resume) pause();
    scrubbing = true;
    whole.setPointerCapture(e.pointerId);
    goTo(e);
  });
  whole.addEventListener('pointermove', (e) => { if (scrubbing) goTo(e); });
  const stopScrub = () => {
    if (scrubbing && resume) play();
    scrubbing = false;
    resume = false;
  };
  whole.addEventListener('pointerup', stopScrub);
  whole.addEventListener('pointercancel', stopScrub);

  // The close-up: move the playhead, add and move clips.
  const canvas = $('closeup-canvas');
  let drag = null;
  const timeAt = (e) => {
    const box = canvas.getBoundingClientRect();
    const f = Math.max(0, Math.min(1, (e.clientX - box.left) / box.width));
    return state.view.start + f * state.view.span;
  };
  canvas.addEventListener('pointerdown', (e) => {
    if (!duration()) return;
    const box = canvas.getBoundingClientRect();
    const inRuler = e.clientY - box.top < RULER;
    const i = inRuler ? -1 : clipAt(e.clientX - box.left, box.width);
    const t = timeAt(e);
    const resume = state.playing;                   // dragging pauses; letting go carries on
    if (resume) pause();
    if (i >= 0) {
      drag = { kind: 'clip', i, offset: t - state.starts[i], moved: false, resume };
      if (i !== state.selected || clipAtTime(state.playhead) !== i) selectClip(i);
    } else {
      drag = { kind: 'scrub', resume };
      seek(t, { rough: true });
    }
    canvas.setPointerCapture(e.pointerId);
    canvas.classList.add('is-moving');
  });
  canvas.addEventListener('pointermove', (e) => {
    const t = timeAt(e);
    const box = canvas.getBoundingClientRect();
    const near = state.transitions.some((s) => Math.abs(s - t) / state.view.span * box.width < 5);
    $('timeline-hover').textContent = duration() ? `${timecode(t, true)}${near ? ' · likely transition' : ''}` : '';
    if (!drag) {
      const over = e.clientY - box.top >= RULER && clipAt(e.clientX - box.left, box.width) >= 0;
      canvas.classList.toggle('is-over-clip', over);
      return;
    }
    if (drag.kind === 'scrub') { seek(t, { rough: true }); return; }
    const max = Math.max(0, duration() - clipLength());
    state.starts[drag.i] = tenth(Math.min(max, Math.max(0, t - drag.offset)));
    drag.moved = true;
    seek(state.starts[drag.i], { rough: true });
    renderChips();
  });
  const end = () => {
    if (drag && drag.kind === 'clip' && drag.moved) startsChanged();
    if (drag && drag.kind === 'scrub') updateViewer();
    if (drag && drag.resume) play();
    drag = null;
    canvas.classList.remove('is-moving');
  };
  canvas.addEventListener('pointerup', end);
  canvas.addEventListener('pointercancel', end);
  canvas.addEventListener('pointerleave', () => { if (!drag) $('timeline-hover').textContent = ''; });
  canvas.addEventListener('dblclick', (e) => {
    const box = canvas.getBoundingClientRect();
    if (e.clientY - box.top < RULER || clipAt(e.clientX - box.left, box.width) >= 0) return;
    addClipAt(timeAt(e));
  });
  // Scroll sideways (or up and down) to move along; pinch to zoom.
  canvas.addEventListener('wheel', (e) => {
    if (!duration()) return;
    e.preventDefault();
    if (e.ctrlKey) {
      setZoom(state.view.span * Math.exp(e.deltaY * 0.01), timeAt(e));   // around the pointer
      return;
    } else {
      const delta = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
      state.view.start += delta / canvas.clientWidth * state.view.span;
      clampView();
    }
    drawTimeline();
  }, { passive: false });

  $('zoom-slider').addEventListener('input', (e) => setZoom(spanFromSlider(Number(e.target.value))));
  $('zoom-in').addEventListener('click', () => zoomStep(1));
  $('zoom-out').addEventListener('click', () => zoomStep(-1));
  window.addEventListener('resize', drawTimeline);
}
