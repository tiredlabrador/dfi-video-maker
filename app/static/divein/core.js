/*
 * Dive In — the page. Core: the shared state, small helpers, messages, and
 * remembering settings between visits.
 *
 * The page is an editor: project on the left, a viewer of whatever moment the
 * playhead is on, style on the right, the timeline along the bottom. Every
 * finished picture comes from the same Python code that makes the export, so
 * the preview can't drift from the real thing.
 *
 * Plain scripts sharing one scope (no build step): core, viewer, look,
 * timeline, transport, media, export, then main wires them up.
 */
'use strict';

const $ = (id) => document.getElementById(id);
const show = (el, on) => { el.hidden = !on; };
const STORE = 'divein-settings-v1';
const ACCENTS = { yellow: '#fffe01', red: '#ea2020', green: '#3dff00', white: '#ffffff' };

const state = {
  photo: null,            // {token, width, height}
  audio: null,            // {token, duration, name}
  crop: { zoom: 1, cx: 0.5, cy: 0.5 },     // the photo's framing (all photo clips)
  format: 'portrait',     // what the viewer shows: 'portrait' (the video) or 'square' (the JPG)
  aspect: '4:5',          // the video's shape: '4:5' or '9:16'
  colour: 'yellow',
  motion: 'level',
  overrides: {},
  defaults: null,
  polling: null,
  squareWanted: true,     // make the 1:1 JPG (when there's a photo)
  // The clips
  starts: [],             // clip start times, in seconds
  sources: [],            // for each clip: 'auto' (footage if filmed), 'photo' or 'video'
  framing: [],            // for each clip: how its footage is framed {cx, cy, zoom}
  selected: 0,            // the clip the clip controls act on
  // The mix and what was found in it
  overview: null,         // {duration, peaks}
  detail: null,           // the same, finer, for the close-up
  transitions: [],        // rough seconds where one record gives way to the next
  footage: null,          // {audio, matches}: where the mix was filmed
  view: { start: 0, span: 240 },   // which part of the mix the close-up shows
  // Playing
  playhead: 0,            // the moment of the mix the viewer shows, in seconds
  playing: false,
  listenFrom: 0,          // where the sound now loaded in the player starts
  heldForPicture: false,  // the sound is paused while the footage catches up
  exact: false,           // a rendered preview has the viewer
  // The photo's quick draft (drawn here while dragging)
  photoEl: null,
  srcBase: null,
  srcGhost: null,
  srcScale: 1,
  layers: null,
  interacting: false,
  draftQueued: false,
  idleTimer: null,
};

/* ── messages ────────────────────────────────────────────────────── */

/**
 * A message at the top right. Errors stay until closed; notes go by
 * themselves. The same message twice just stays up longer.
 */
function notify(message, kind = 'error', sticky = kind === 'error', action = null) {
  if (!message) return;
  const box = $('toasts');
  let toast = [...box.children].find((t) => t.dataset.text === message);
  if (toast && action) { toast.remove(); toast = null; }   // a fresh one, with the new action
  if (!toast) {
    toast = document.createElement('div');
    toast.className = `toast ${kind}`;
    toast.dataset.text = message;
    toast.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    const text = document.createElement('p');
    text.textContent = message;
    const close = document.createElement('button');
    close.type = 'button';
    close.title = 'Close';
    close.textContent = '×';
    close.addEventListener('click', () => toast.remove());
    toast.append(text);
    if (action) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'toast-action';
      button.textContent = action.label;
      button.addEventListener('click', () => { toast.remove(); action.run(); });
      toast.append(button);
    }
    toast.append(close);
    box.append(toast);
    while (box.children.length > 4) box.firstChild.remove();
  }
  clearTimeout(toast.timer);
  if (!sticky) toast.timer = setTimeout(() => toast.remove(), 6000);
}

function setError(message) {
  if (message) notify(message, 'error');
}

function setWarning(message) {
  if (message) notify(message, 'warn');
}

/* ── small helpers ───────────────────────────────────────────────── */

function timecode(seconds, tenths = false) {
  const whole = tenths ? Math.max(0, seconds) : Math.max(0, Math.round(seconds));
  const s = Math.floor(whole % 60), m = Math.floor(whole / 60) % 60, h = Math.floor(whole / 3600);
  const pad = (n) => String(n).padStart(2, '0');
  const tail = tenths ? `.${Math.floor((whole * 10) % 10)}` : '';
  return h ? `${h}:${pad(m)}:${pad(s)}${tail}` : `${m}:${pad(s)}${tail}`;
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

function duration() {
  return (state.overview && state.overview.duration) || (state.audio && state.audio.duration) || 0;
}

/** A setting, from the Tuning overrides if set there, else the defaults. */
function setting(path, fallback) {
  const dig = (obj) => path.split('.').reduce((o, k) => (o && k in o ? o[k] : undefined), obj);
  const v = dig(state.overrides);
  if (v !== undefined) return v;
  const d = state.defaults && dig(state.defaults);
  return d !== undefined ? d : fallback;
}

function isOn(id) {
  return $(id).getAttribute('aria-pressed') === 'true';
}

/** Everything the server needs to draw or make this design. */
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
    sources: state.starts.map((_, i) => clipSource(i)),
    footage_crops: state.starts.map((_, i) => framingOf(i)),
    footage_look: footageLook(),
    lut: $('lut-on').checked,
    aspect: state.aspect,
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
      footage_look: footageLook(), aspect: state.aspect,
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
  showFolder(saved.footage_folder ?? '');
  for (const [k, on] of Object.entries(saved.footage_look || {})) {
    if (k === 'brightness') $('look-brightness').value = String(Number(on) || 0);
    else if ($(`look-${k}`)) $(`look-${k}`).setAttribute('aria-pressed', String(Boolean(on)));
  }
  state.aspect = saved.aspect === '9:16' ? '9:16' : '4:5';
  setSegment('format-choice', state.aspect);
  state.colour = saved.colour || 'yellow';
  state.motion = saved.rings_follow || 'level';
  setSegment('colour-choice', state.colour);
  setSegment('motion-choice', state.motion);
  try { state.overrides = JSON.parse($('overrides').value || '{}'); } catch { state.overrides = {}; }
}

/* ── segmented controls ──────────────────────────────────────────── */

function setSegment(id, value) {
  for (const b of document.querySelectorAll(`#${id} button`)) {
    b.setAttribute('aria-checked', String(b.dataset.value === value));
  }
}

function wireSegment(id, onChange) {
  document.querySelectorAll(`#${id} button`).forEach((b) =>
    b.addEventListener('click', () => {
      if (b.disabled) return;
      setSegment(id, b.dataset.value);
      onChange(b.dataset.value);
    }));
}
