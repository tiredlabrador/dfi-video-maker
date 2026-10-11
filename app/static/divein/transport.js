/*
 * The playhead and playing: play/pause from wherever the playhead is,
 * previous/next clip, and the keyboard.
 *
 * The sound is cut by the app a stretch at a time (browsers jump into a long
 * MP3 slowly, and only roughly). Like a video player, picture and sound start
 * together; if the picture stalls, the sound waits for it.
 */
'use strict';

const PLAY_CHUNK = 60;                    // seconds of sound fetched at a time
const AHEAD = 8;                          // start loading the next stretch this long before the end

/* Two players take turns: one plays while the other loads what comes next,
   so there's no pause every minute while the next stretch of sound arrives. */
let active = 0;
const sound = () => (active === 0 ? $('listen') : $('listen-next'));
const spare = () => (active === 0 ? $('listen-next') : $('listen'));

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
let ticker = null;

function excerptURL(from, seconds) {
  return `/api/divein/excerpt?audio=${encodeURIComponent(state.audio.token)}`
    + `&start=${from.toFixed(3)}&seconds=${seconds.toFixed(3)}`;
}

/** Where in the mix the sound has got to. */
function listenTime() {
  return (state.listenFrom || 0) + sound().currentTime;
}

async function play() {
  if (!state.audio || state.playing) return;
  closeExact();
  if (state.playhead >= duration() - 0.5) state.playhead = 0;
  state.playing = true;
  setPlayIcon();
  updateViewer();                                   // the frame tools hide while playing
  await startPlaying();
}

function stopTicking() {
  clearInterval(ticker);
  ticker = null;
}

function pause() {
  playToken += 1;
  stopTicking();
  const was = state.playing;
  state.playing = false;
  state.heldForPicture = false;
  state.nextFrom = null;
  $('listen').pause();
  $('listen-next').pause();
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
  stopTicking();
  $('listen').pause();
  $('listen-next').pause();
  startPlaying();
}

/** Load sound from the playhead, get the picture ready too, then set off together. */
async function startPlaying() {
  const token = ++playToken;
  const from = state.playhead;
  const seconds = Math.min(PLAY_CHUNK, duration() - from);
  if (seconds < 0.5) { pause(); return; }
  spare().removeAttribute('src');                   // whatever it had loaded is no use now
  state.nextFrom = null;
  state.listenFrom = from;
  state.heldForPicture = false;
  const player = sound();
  player.src = excerptURL(from, seconds);
  updateViewer();
  drawTimeline();
  if (!(await bothReady(token))) return;
  if (player.error) { notify('The sound couldn’t be loaded. Is the app still running?'); pause(); return; }
  const footage = $('stage').classList.contains('is-footage') ? $('footage') : null;
  try {
    await Promise.all([player.play(), footage ? footage.play() : null]);
  } catch {
    if (token === playToken) pause();
    return;
  }
  if (token !== playToken) return;
  stopTicking();
  // A timer rather than animation frames: it keeps going in a background tab.
  ticker = setInterval(() => tick(token), 40);
}

/** Resolve once `media` has enough loaded to play (or gave up, or 8s passed). */
function whenReady(media) {
  return new Promise((resolve) => {
    if (media.readyState >= 3 && !media.seeking) { resolve(); return; }
    const done = () => {
      for (const ev of ['canplay', 'seeked', 'error']) media.removeEventListener(ev, check);
      clearTimeout(timer);
      resolve();
    };
    const check = (e) => { if (e.type === 'error' || media.readyState >= 3) done(); };
    for (const ev of ['canplay', 'seeked', 'error']) media.addEventListener(ev, check);
    const timer = setTimeout(done, 8000);           // never wait for ever
  });
}

/** The footage (if it's showing) at the right frame, and the sound, both ready to go. */
async function bothReady(token) {
  const el = $('footage');
  const v = viewerTarget();
  const waits = [whenReady(sound())];
  if (v.at) {
    el.pause();
    el.playbackRate = 1;
    el.currentTime = v.at.time;
    waits.push(whenReady(el));
  }
  await Promise.all(waits);
  return token === playToken;
}

/** Every 40ms while playing: move the playhead, keep the picture with the sound. */
function tick(token) {
  if (token !== playToken || !state.playing) { if (token !== playToken) return; stopTicking(); return; }
  const player = sound();
  const chunkEnd = state.listenFrom + (Number.isFinite(player.duration) ? player.duration : PLAY_CHUNK);
  // Nearly through this stretch: load the next in the other player.
  if (state.nextFrom == null && Number.isFinite(player.duration)
      && player.duration - player.currentTime < AHEAD && chunkEnd < duration() - 0.5) {
    state.nextFrom = chunkEnd;
    spare().src = excerptURL(chunkEnd, Math.min(PLAY_CHUNK, duration() - chunkEnd));
    spare().load();
  }
  if (player.ended) {
    if (state.nextFrom == null) {                   // the end of the mix
      state.playhead = Math.min(duration(), chunkEnd);
      pause();
      return;
    }
    active = 1 - active;                            // over to the stretch already loaded
    state.listenFrom = state.nextFrom;
    state.nextFrom = null;
    sound().currentTime = 0;
    sound().play().catch(() => { if (token === playToken) pause(); });
    return;
  }
  if (player.paused && !state.heldForPicture) { pause(); return; }
  state.playhead = listenTime();
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
  if (!document.hidden) drawTimeline();
}

/**
 * Keep the footage with the sound. If the picture stalls (loading more of a
 * 17GB file), the sound waits; small drift is corrected by speed, not jumps.
 */
let lastAlign = 0;

function followPlayback() {
  const el = $('footage');
  const player = sound();
  const at = footageAt(state.playhead);
  // No footage on show (any more): the sound mustn't stay held for it.
  if (!$('stage').classList.contains('is-footage') || !at) {
    if (at === null || !$('stage').classList.contains('is-footage')) el.pause();
    if (state.heldForPicture) { state.heldForPicture = false; player.play().catch(() => {}); }
    return;
  }
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
    // 4 = the browser can't use what it got: the footage is gone (the app was
    // restarted, or the drive unplugged), or this browser can't play it.
    if (el.error && el.error.code === 4) {
      fetch(el.getAttribute('src'), { headers: { Range: 'bytes=0-0' } }).then((r) => {
        if (r.ok) { notify('This browser can’t play this footage. Try Safari.', 'error'); return; }
        state.footage = null;
        updateViewer();
        drawTimeline();
        notify('The footage needs finding again: press Find (is the drive plugged in?).', 'warn');
      }).catch(() => notify('The footage stopped loading. Is the app still running?', 'error'));
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
    const target = e.target;
    // Typing: every key is for the box. A focused switch, slider or button
    // keeps Space and the arrows for itself (Space ticks a box, arrows move a slider).
    if (target.matches && target.matches('input:not([type]), input[type=text], input[type=number], textarea, select')) return;
    if (target.matches && target.matches('button, input[type=checkbox], input[type=range]')
        && [' ', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) return;
    const clip = clipAtTime(state.playhead);
    switch (e.key) {
      case ' ':
        e.preventDefault();
        if (state.exact) {                          // a rendered preview: play or pause it
          const player = $('player');
          if (player.paused) player.play().catch(() => {}); else player.pause();
        } else {
          togglePlay();
        }
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
    refreshExport();
    saveSession();
  });
  wireRecovery();
  wireKeys();
}
