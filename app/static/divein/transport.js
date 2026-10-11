/*
 * The playhead and playing: play/pause from wherever the playhead is,
 * previous/next clip, and the keyboard.
 *
 * The sound is cut by the app a stretch at a time (browsers jump into a long
 * MP3 slowly, and only roughly). Like a video player, picture and sound start
 * together; if the picture stalls, the sound waits for it.
 */
'use strict';

const PLAY_CHUNK = 45;                    // seconds of sound fetched at a time

/** Move the playhead to `t`. While playing, playing carries on from there. */
function seek(t, { rough = false, reveal = false } = {}) {
  const total = duration();
  if (!total) return;
  state.playhead = Math.max(0, Math.min(total, t));
  // Never lose the playhead off the close-up: bring it back if it went off.
  revealTime(state.playhead, reveal ? 0.08 : 0);
  if (state.playing) { restartPlaying(); return; }
  drawTimeline();
  updateViewer(rough);
}

function setPlayIcon() {
  $('play-icon').innerHTML = `<use href="#i-${state.playing ? 'pause' : 'play'}"/>`;
  $('play-btn').title = state.playing ? 'Pause (Space)' : 'Play (Space)';
}

let playToken = 0;

function excerptURL(from, seconds) {
  return `/api/divein/excerpt?audio=${encodeURIComponent(state.audio.token)}`
    + `&start=${from.toFixed(3)}&seconds=${seconds.toFixed(3)}`;
}

async function play() {
  if (!state.audio || state.playing) return;
  closeExact();
  if (state.playhead >= duration() - 0.5) state.playhead = 0;
  state.playing = true;
  setPlayIcon();
  await startPlaying();
}

function pause() {
  playToken += 1;
  const was = state.playing;
  state.playing = false;
  state.heldForPicture = false;
  $('listen').pause();
  $('footage').pause();
  $('footage').playbackRate = 1;
  setPlayIcon();
  if (was) { drawTimeline(); updateViewer(); }
}

function togglePlay() {
  if (state.playing) pause(); else play();
}

function restartPlaying() {
  playToken += 1;
  $('listen').pause();
  startPlaying();
}

/** Load the next stretch of sound from the playhead and set off with the picture. */
async function startPlaying() {
  const token = ++playToken;
  const from = state.playhead;
  const seconds = Math.min(PLAY_CHUNK, duration() - from);
  if (seconds < 0.5) { pause(); return; }
  const player = $('listen');
  state.listenFrom = from;
  state.heldForPicture = false;
  player.src = excerptURL(from, seconds);
  updateViewer();
  drawTimeline();
  if (!(await startTogether(token))) return;
  player.play().then(() => {
    if (token === playToken) requestAnimationFrame(() => tick(token));
  }).catch(() => { if (token === playToken) pause(); });
}

/** Wait until the footage (if it's showing) and the sound are both ready to go. */
async function startTogether(token) {
  const player = $('listen'), el = $('footage');
  const ready = (media, needed) => new Promise((resolve) => {
    if (media.readyState >= needed && !media.seeking) { resolve(); return; }
    const done = () => {
      media.removeEventListener('canplay', done);
      media.removeEventListener('seeked', check);
      resolve();
    };
    const check = () => { if (media.readyState >= needed) done(); };
    media.addEventListener('canplay', done);
    media.addEventListener('seeked', check);
    setTimeout(done, 8000);                         // never wait for ever
  });
  const v = viewerTarget();
  if (v.at) {
    el.pause();
    el.playbackRate = 1;
    el.currentTime = v.at.time;
    await ready(el, 3);
    if (token !== playToken) return false;
    el.play().catch(() => {});
  }
  await ready(player, 3);
  return token === playToken;
}

/** Each frame while playing: move the playhead, keep the picture with the sound. */
function tick(token) {
  if (token !== playToken || !state.playing) return;
  const player = $('listen');
  if (player.ended) {                               // this stretch is done: fetch the next
    state.playhead = state.listenFrom + player.duration;
    if (state.playhead < duration() - 0.5) startPlaying(); else pause();
    return;
  }
  if (player.paused && !state.heldForPicture) { pause(); return; }
  state.playhead = state.listenFrom + player.currentTime;
  const before = state.viewing, v = viewerTarget();
  const changed = !before || v.clip !== before.clip || v.source !== before.source
    || Boolean(v.at) !== Boolean(before.at) || (v.at && before.at && v.at.video !== before.at.video);
  if (changed) { updateViewer(); lastAlign = 0; } else { updateHead(); updateTime(); }
  followPlayback();
  const { start, span } = state.view;               // page the close-up along
  if (state.playhead > start + span * 0.95 || state.playhead < start) {
    state.view.start = state.playhead - span * 0.05;
    clampView();
  }
  drawTimeline();
  requestAnimationFrame(() => tick(token));
}

/**
 * Keep the footage with the sound. If the picture stalls (loading more of a
 * 17GB file), the sound waits; small drift is corrected by speed, not jumps.
 */
let lastAlign = 0;

function followPlayback() {
  const el = $('footage');
  if (!$('stage').classList.contains('is-footage')) return;
  const player = $('listen');
  const at = footageAt(state.playhead);
  if (!at) { el.pause(); return; }
  if (el.readyState < 3 || el.seeking) {
    if (!player.paused) { state.heldForPicture = true; player.pause(); }
    return;
  }
  if (state.heldForPicture) {
    state.heldForPicture = false;
    player.play().catch(() => {});
  }
  const now = performance.now();
  const behind = at.time - el.currentTime;          // positive: the picture is late
  if (Math.abs(behind) > 1 && now - lastAlign > 3000) {
    el.currentTime = at.time;                       // badly out (rare): one jump, then wait
    lastAlign = now;
    return;
  }
  el.playbackRate = Math.abs(behind) < 0.02 ? 1 : 1 + Math.max(-0.15, Math.min(0.15, behind * 2));
  if (el.paused) el.play().catch(() => {});
}

/** The clip before or after the playhead. */
function jumpClip(direction) {
  const order = state.starts.map((s, i) => [s, i]).sort((a, b) => a[0] - b[0]);
  const t = state.playhead;
  const hit = direction < 0
    ? [...order].reverse().find(([s]) => s < t - 0.3)
    : order.find(([s]) => s > t + 0.05);
  if (hit) selectClip(hit[1]);
}

/* ── when things go wrong ────────────────────────────────────────── */

let footageRetries = 0;

function wireRecovery() {
  const el = $('footage');
  el.addEventListener('loadeddata', () => { footageRetries = 0; });
  el.addEventListener('error', () => {
    if (!el.getAttribute('src')) return;
    // 4 = this browser can't play the file at all: retrying won't help.
    if (el.error && el.error.code === 4) {
      notify('This browser can’t play this footage. Try Safari.', 'error');
      return;
    }
    if (footageRetries >= 3) {
      notify('The footage stopped loading. Is the drive still plugged in?', 'error');
      return;
    }
    footageRetries += 1;
    setTimeout(() => { el.load(); updateViewer(); }, 500 * footageRetries);
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) return;
    if (!state.playing) { updateViewer(); return; }
    lastAlign = 0;                                  // back in view: line up again straight away
    followPlayback();
  });
}

/* ── the keyboard ────────────────────────────────────────────────── */

function wireKeys() {
  document.addEventListener('keydown', (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.target.closest && e.target.closest('input, textarea, select')) return;
    const clip = clipAtTime(state.playhead);
    switch (e.key) {
      case ' ':
        e.preventDefault();
        togglePlay();
        break;
      case 'ArrowLeft':
      case 'ArrowRight': {
        if (!duration()) return;
        e.preventDefault();
        const step = (e.shiftKey ? 5 : 1) * (e.key === 'ArrowLeft' ? -1 : 1);
        if (clip >= 0 && !state.playing) {        // nudge the clip, and the playhead with it
          const max = Math.max(0, duration() - clipLength());
          const moved = Math.min(max, Math.max(0, tenth(state.starts[clip] + step)));
          const shift = moved - state.starts[clip];
          state.starts[clip] = moved;
          state.selected = clip;
          seek(state.playhead + shift, { reveal: true });
          startsChanged();
        } else {
          seek(state.playhead + step, { reveal: true });
        }
        break;
      }
      case 'Delete':
      case 'Backspace':
        if (clip >= 0) { e.preventDefault(); deleteClip(clip); }
        break;
      case 'a':
      case 'A':
        if (state.audio && clip < 0) { e.preventDefault(); addClipAt(state.playhead); }
        break;
      case '[':
        jumpClip(-1);
        break;
      case ']':
        jumpClip(1);
        break;
      case 'Escape':
        if (state.exact) closeExact(); else closeExport();
        break;
      default:
    }
  });
}

function wireTransport() {
  $('play-btn').addEventListener('click', togglePlay);
  $('prev-clip').addEventListener('click', () => jumpClip(-1));
  $('next-clip').addEventListener('click', () => jumpClip(1));
  $('add-clip').addEventListener('click', () => addClipAt(state.playhead));
  $('add-here').addEventListener('click', () => addClipAt(state.playhead));
  $('delete-clip').addEventListener('click', () => deleteClip(clipAtTime(state.playhead)));
  wireSegment('source-seg', (value) => {
    const clip = clipAtTime(state.playhead);
    if (clip < 0) return;
    state.sources[clip] = value;
    renderChips();
    updateViewer();
    saveSession();
  });
  wireRecovery();
  wireKeys();
}
