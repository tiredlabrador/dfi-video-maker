/*
 * The Dig — the page. One list of tracks: a single track is just a list of one.
 *
 * Each track shows its artwork (click to replace it), its title and artist,
 * and its waveform with the 25-second clip on it: drag the clip, or click
 * where it should start, and press play to hear it. Preview or render one
 * track or all of them; each row shows its own result.
 *
 * Talks to the small Python server on this machine: the "upload" is a copy
 * between two folders on your own disk.
 */
import WaveSurfer from '/static/vendor/wavesurfer-7.12.12/wavesurfer.esm.js';
import RegionsPlugin from '/static/vendor/wavesurfer-7.12.12/regions.esm.js';
import Sortable from '/static/vendor/sortablejs-1.15.7/sortable.esm.js';

const CLIP = 25;                          // seconds in every video
const $ = (id) => document.getElementById(id);
const show = (el, on) => { el.hidden = !on; };

const state = {
  tracks: [],              // in posting order
  selected: null,
  busy: false,
  polling: null,
  artFor: null,            // the track whose artwork is being replaced
  zip: null,
};
let nextId = 1;

/* ── small helpers ───────────────────────────────────────────────── */

function notify(message, kind = 'error', sticky = kind === 'error') {
  if (!message) return;
  const box = $('toasts');
  let toast = [...box.children].find((t) => t.dataset.text === message);
  if (!toast) {
    toast = document.createElement('div');
    toast.className = `toast ${kind}`;
    toast.dataset.text = message;
    const text = document.createElement('p');
    text.textContent = message;
    const close = document.createElement('button');
    close.type = 'button';
    close.textContent = '×';
    close.title = 'Close';
    close.addEventListener('click', () => toast.remove());
    toast.append(text, close);
    box.append(toast);
  }
  clearTimeout(toast.timer);
  if (!sticky) toast.timer = setTimeout(() => toast.remove(), 6000);
}

function timecode(seconds) {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function parseTime(text) {
  const parts = String(text || '').trim().split(':').map(Number);
  if (!parts.length || parts.some((n) => !Number.isFinite(n) || n < 0)) return null;
  return parts.reduce((total, n) => total * 60 + n, 0);
}

async function postForm(path, form) {
  const response = await fetch(path, { method: 'POST', body: form });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
  return payload;
}

/* ── health ──────────────────────────────────────────────────────── */

async function checkHealth() {
  const status = $('health');
  try {
    const health = await (await fetch('/api/health')).json();
    const missing = [];
    if (!health.ffmpeg || !health.ffprobe) missing.push('ffmpeg');
    if (!health.brand_font) missing.push('the font');
    if (!health.overlay) missing.push('the logo overlay');
    if (!health.fallback_art) missing.push('the fallback artwork');
    status.textContent = missing.length ? `Missing ${missing.join(', ')}` : 'Ready';
    status.className = `status ${missing.length ? 'bad' : 'ok'}`;
    if (missing.length) status.title = 'See INSTALL.md. Videos may not look right.';
  } catch {
    status.textContent = 'Not connected';
    status.className = 'status bad';
  }
}

/* ── adding tracks ───────────────────────────────────────────────── */

function addFiles(files) {
  const audio = [...files].filter((f) => (f.type || '').startsWith('audio/')
    || /\.(mp3|wav|flac|m4a|aiff?|aac|ogg)$/i.test(f.name));
  if (!audio.length) { notify('Those aren’t audio files.'); return; }
  for (const file of audio) {
    const track = {
      id: nextId++, file, url: URL.createObjectURL(file), name: file.name,
      title: file.name.replace(/\.[^.]+$/, ''), artist: '', start: 0, duration: 0,
      token: null, reading: true, problem: '', art: null, override: null, result: null,
    };
    state.tracks.push(track);
    track.el = buildRow(track);
    $('track-list').append(track.el);
    inspect(track);
  }
  if (!state.selected) select(state.tracks[0]);
  refresh();
}

/** Read the track's tags and artwork (and keep a copy on the server to render from). */
async function inspect(track) {
  const form = new FormData();
  form.append('audio', track.file);
  try {
    const info = await postForm('/api/inspect', form);
    track.token = info.upload_token;
    if (info.track) track.title = info.track;
    if (info.artist) track.artist = info.artist;
    // No tags, but named "Artist - Title": use the name.
    const named = track.name.replace(/\.[^.]+$/, '').match(/^(.+?)\s+[-–]\s+(.+)$/);
    if (named && !info.artist) { track.artist = named[1].trim(); if (!info.track) track.title = named[2].trim(); }
    track.duration = info.duration || track.duration;
    track.art = { preview: info.artwork_preview || '', source: info.artwork_source };
    if (!info.readable) track.problem = info.error || 'This file couldn’t be read as audio.';
  } catch (error) {
    track.problem = error.message;
  }
  track.reading = false;
  fillRow(track);
  if (state.selected === track) showViewer();
  refresh();
}

/* ── a row ───────────────────────────────────────────────────────── */

function buildRow(track) {
  const li = document.createElement('li');
  li.className = 'track';
  li.dataset.id = String(track.id);
  li.innerHTML = `
    <span class="grip" title="Drag to reorder"><svg><use href="#i-grip"/></svg></span>
    <span class="num"></span>
    <button type="button" class="art" title="Replace the artwork"><svg><use href="#i-image"/></svg><span class="swap">Replace</span></button>
    <div class="fields">
      <input class="input title" type="text" placeholder="Track title" aria-label="Track title">
      <input class="input artist" type="text" placeholder="Artist" aria-label="Artist">
    </div>
    <button type="button" class="icon-btn remove" title="Remove"><svg><use href="#i-close"/></svg></button>
    <div class="clip-row">
      <button type="button" class="icon-btn listen" title="Play the clip (Space)"><svg><use href="#i-play"/></svg></button>
      <div class="wave" title="Click where the clip should start, or drag it"></div>
      <input class="input clip-time" type="text" value="0:00" aria-label="Clip start" title="Clip start (m:ss)">
    </div>
    <div class="side">
      <p class="clip-note"></p>
      <span class="state">Reading…</span>
      <div class="links"></div>
      <button type="button" class="btn btn--small btn--ghost mini preview" title="A small, quick version">Preview</button>
    </div>`;
  const q = (s) => li.querySelector(s);
  q('.title').value = track.title;
  q('.title').addEventListener('input', (e) => { track.title = e.target.value; staleResult(track); });
  q('.artist').addEventListener('input', (e) => { track.artist = e.target.value; staleResult(track); });
  q('.clip-time').addEventListener('change', (e) => {
    const t = parseTime(e.target.value);
    if (t === null) { notify('Use m:ss for the clip start, like 1:05.'); e.target.value = timecode(track.start); return; }
    setStart(track, t);
  });
  q('.art').addEventListener('click', (e) => { e.stopPropagation(); state.artFor = track; $('art-input').click(); });
  q('.remove').addEventListener('click', (e) => { e.stopPropagation(); removeTrack(track); });
  q('.preview').addEventListener('click', (e) => { e.stopPropagation(); select(track); render([track], true); });
  q('.listen').addEventListener('click', (e) => { e.stopPropagation(); select(track); toggleListen(track); });
  li.addEventListener('pointerdown', () => select(track));
  buildWave(track, q('.wave'));
  return li;
}

function fillRow(track) {
  const q = (s) => track.el.querySelector(s);
  if (document.activeElement !== q('.title')) q('.title').value = track.title;
  if (document.activeElement !== q('.artist')) q('.artist').value = track.artist;
  const art = q('.art');
  const src = track.override ? track.override.url : (track.art && track.art.preview);
  art.querySelector('img')?.remove();
  art.querySelector('.badge')?.remove();
  if (src) {
    const img = document.createElement('img');
    img.alt = '';
    img.src = src;
    art.prepend(img);
  }
  if (track.override) art.insertAdjacentHTML('beforeend', '<span class="badge">YOURS</span>');
  track.el.classList.toggle('has-problem', Boolean(track.problem));
  q('.clip-time').value = timecode(track.start);
  const short = track.duration && track.start + CLIP > track.duration + 0.05;
  q('.clip-note').textContent = track.problem
    || (short ? `The track is only ${timecode(track.duration)} long, so this clip will run short.` : '');
  renderState(track);
}

function renderState(track) {
  const label = track.el.querySelector('.state');
  const links = track.el.querySelector('.links');
  links.innerHTML = '';
  const r = track.result;
  label.className = 'state';
  if (track.reading) label.textContent = 'Reading…';
  else if (track.problem) { label.textContent = 'Can’t be used'; label.classList.add('error'); }
  else if (!r) label.textContent = '';
  else if (r.status === 'pending') label.textContent = r.running ? 'Rendering…' : 'Waiting';
  else if (r.status === 'error') { label.textContent = 'Failed'; label.classList.add('error'); label.title = r.error || ''; }
  else if (r.status === 'done') {
    label.textContent = r.preview ? 'Preview ready' : 'Rendered ✓';
    if (!r.preview) label.classList.add('done');
    const watch = document.createElement('button');
    watch.type = 'button';
    watch.textContent = 'Watch';
    watch.addEventListener('click', (e) => { e.stopPropagation(); select(track); playResult(track); });
    const download = document.createElement('a');
    download.href = r.download_url;
    download.download = r.filename || 'video.mp4';
    download.textContent = 'Download';
    links.append(watch, download);
  }
  track.el.querySelector('.preview').disabled = state.busy || track.reading || Boolean(track.problem);
}

/** Changing a track makes its finished video out of date. */
function staleResult(track) {
  if (track.result && track.result.status === 'done') {
    track.result = null;
    renderState(track);
    if (state.selected === track) showViewer();
  }
}

function removeTrack(track) {
  if (track.ws) track.ws.destroy();
  URL.revokeObjectURL(track.url);
  track.el.remove();
  state.tracks = state.tracks.filter((t) => t !== track);
  if (state.selected === track) select(state.tracks[0] || null);
  refresh();
}

/* ── the waveform and the clip on it ─────────────────────────────── */

function buildWave(track, container) {
  const ws = WaveSurfer.create({
    container, url: track.url, height: 40, normalize: true,
    waveColor: '#55555c', progressColor: '#55555c', cursorColor: '#ffffff', cursorWidth: 1,
    barWidth: 2, barGap: 1, barRadius: 1, dragToSeek: false,
  });
  const regions = ws.registerPlugin(RegionsPlugin.create());
  track.ws = ws;
  ws.on('decode', (seconds) => {
    track.duration = track.duration || seconds;
    track.region = regions.addRegion({
      start: track.start, end: Math.min(seconds, track.start + CLIP),
      color: 'rgba(255, 225, 77, 0.28)', drag: true, resize: false,
    });
    track.region.on('update-end', () => setStart(track, track.region.start, false));
    fillRow(track);
  });
  // Click anywhere on the waveform: the clip starts there.
  ws.on('interaction', (t) => setStart(track, t));
  ws.on('finish', () => setListenIcon(track, false));
  ws.on('pause', () => setListenIcon(track, false));
  ws.on('play', () => setListenIcon(track, true));
  ws.on('error', () => { /* the waveform is a nicety: the render doesn't need it */ });
}

function setStart(track, t, moveRegion = true) {
  const latest = Math.max(0, (track.duration || t + CLIP) - CLIP);
  track.start = Math.round(Math.max(0, Math.min(t, latest)));
  if (track.region && moveRegion) {
    track.region.setOptions({ start: track.start, end: Math.min(track.duration || Infinity, track.start + CLIP) });
  }
  if (track.ws && track.ws.isPlaying()) track.region?.play();
  staleResult(track);
  fillRow(track);
}

function setListenIcon(track, playing) {
  const use = track.el.querySelector('.listen use');
  use.setAttribute('href', playing ? '#i-pause' : '#i-play');
}

function toggleListen(track) {
  if (!track.ws) return;
  for (const other of state.tracks) if (other !== track && other.ws?.isPlaying()) other.ws.pause();
  if (track.ws.isPlaying()) { track.ws.pause(); return; }
  if (track.region) track.region.play(); else track.ws.play(track.start);
}

/* ── order and numbers ───────────────────────────────────────────── */

function refresh() {
  state.tracks.forEach((t, i) => { t.el.querySelector('.num').textContent = String(i + 1).padStart(2, '0'); });
  const n = state.tracks.length;
  $('tracks-count').textContent = n ? `${n} track${n === 1 ? '' : 's'}` : 'Tracks';
  show($('tracks-empty'), n === 0);
  show($('tracks-hint'), n > 1);
  $('render-all').textContent = n > 1 ? `Render ${n}` : 'Render';
  $('preview-all').textContent = n > 1 ? 'Preview all' : 'Preview';
  const ready = n > 0 && state.tracks.every((t) => !t.reading && !t.problem) && !state.busy;
  $('render-all').disabled = !ready;
  $('preview-all').disabled = !ready;
  for (const t of state.tracks) renderState(t);
  show($('zip-download'), Boolean(state.zip));
}

function reorderFromList() {
  const order = [...$('track-list').children].map((li) => Number(li.dataset.id));
  state.tracks.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
  state.zip = null;                      // the old zip's numbers no longer match
  refresh();
}

/* ── the viewer ──────────────────────────────────────────────────── */

function select(track) {
  if (state.selected === track) return;
  state.selected = track;
  for (const t of state.tracks) t.el.classList.toggle('is-selected', t === track);
  showViewer();
}

function showViewer() {
  const track = state.selected;
  const player = $('player');
  if (!track) {
    show($('stage-empty'), true); show($('stage-art'), false); show(player, false); show($('info'), false);
    return;
  }
  show($('info'), true);
  $('info-title').textContent = `${track.title || 'Untitled'}${track.artist ? ` — ${track.artist}` : ''}`;
  $('info-art').textContent = track.override ? 'Artwork: your image.'
    : ({ embedded: 'Artwork: the track’s own cover.', fallback: 'Artwork: the DFI label design (the track has none).',
         none: 'No artwork found.' }[track.art?.source] || '');
  const r = track.result;
  if (r && r.status === 'done') {
    playResult(track, false);
  } else {
    player.pause();
    show(player, false);
    const src = track.override ? track.override.url : track.art?.preview;
    show($('stage-empty'), !src);
    show($('stage-art'), Boolean(src));
    if (src) $('art-preview').src = src;
    $('facts').innerHTML = '';
    show($('download'), false);
  }
}

function playResult(track, autoplay = true) {
  const r = track.result;
  if (!r || r.status !== 'done') return;
  const player = $('player');
  if (!player.src.endsWith(r.download_url)) player.src = r.download_url;
  show($('stage-empty'), false);
  show($('stage-art'), false);
  show(player, true);
  if (autoplay) player.play().catch(() => {});
  showFacts(r);
}

/** What was made, checked against what it should be ("it rendered" isn't "it's right"). */
function showFacts(r) {
  const p = r.probe || {};
  const facts = {
    Size: `${p.width}×${p.height}`,
    Length: `${(p.duration || 0).toFixed(2)} s`,
    Video: `${p.video_codec} (${p.pix_fmt})`,
    Audio: `${p.audio_codec}, ${p.channels} ch`,
    'File size': `${((p.size_bytes || 0) / 1048576).toFixed(1)} MB`,
  };
  const checks = r.preview ? {} : {
    Size: p.width === 1080 && p.height === 1350,
    Length: Math.abs((p.duration || 0) - CLIP) <= 0.05,
    Video: p.video_codec === 'h264' && p.pix_fmt === 'yuv420p',
    Audio: p.audio_codec === 'aac' && p.channels === 2,
  };
  const list = $('facts');
  list.innerHTML = '';
  for (const [label, value] of Object.entries(facts)) {
    const dt = document.createElement('dt');
    dt.textContent = label;
    const dd = document.createElement('dd');
    dd.textContent = value;
    if (label in checks) dd.className = checks[label] ? 'pass' : 'fail';
    list.append(dt, dd);
  }
  const download = $('download');
  download.href = r.download_url;
  download.download = r.filename || 'video.mp4';
  download.textContent = r.preview ? 'Download the preview' : 'Download the MP4';
  show(download, true);
}

/* ── artwork ─────────────────────────────────────────────────────── */

async function replaceArtwork(file) {
  const track = state.artFor;
  state.artFor = null;
  if (!track || !file) return;
  const form = new FormData();
  form.append('artwork', file);
  try {
    const { upload_token: token } = await postForm('/api/upload', form);
    if (track.override) URL.revokeObjectURL(track.override.url);
    track.override = { token, url: URL.createObjectURL(file) };
    staleResult(track);
    fillRow(track);
    select(track);
    showViewer();
    notify('Artwork replaced. Click it again to choose another.', 'ok');
  } catch (error) {
    notify(error.message);
  }
}

/* ── rendering ───────────────────────────────────────────────────── */

async function render(tracks, preview) {
  if (state.busy || !tracks.length) return;
  if (tracks.some((t) => t.reading)) { notify('Still reading a track. Give it a second.', 'warn'); return; }
  const usable = tracks.filter((t) => !t.problem);
  if (!usable.length) return;
  state.busy = true;
  if (tracks.length === state.tracks.length) state.zip = null;
  for (const t of usable) { t.result = { status: 'pending' }; }
  refresh();
  show($('batch-progress'), true);
  $('progress-fill').style.width = '0%';
  $('progress-text').textContent = preview ? 'Making previews…' : 'Starting…';
  let job;
  try {
    const response = await fetch('/api/batch', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        preview,
        items: usable.map((t) => ({
          upload_token: t.token, track: t.title.trim(), artist: t.artist.trim(),
          clip_start: timecode(t.start), artwork_token: t.override ? t.override.token : undefined,
        })),
      }),
    });
    job = await response.json();
    if (!response.ok) throw new Error(job.error || 'That couldn’t start.');
  } catch (error) {
    notify(error.message);
    for (const t of usable) t.result = null;
    state.busy = false;
    show($('batch-progress'), false);
    refresh();
    return;
  }
  clearInterval(state.polling);
  state.polling = setInterval(() => follow(job.id, usable, preview), 500);
}

async function follow(jobId, tracks, preview) {
  let job;
  try { job = await (await fetch(`/api/jobs/${jobId}`)).json(); } catch { return; }
  $('progress-fill').style.width = `${Math.round((job.progress || 0) * 100)}%`;
  $('progress-text').textContent = job.message || 'Working…';
  const items = job.items || [];
  tracks.forEach((t, i) => {
    const item = items[i];
    if (!item) { t.result = { status: 'pending', running: i === items.length }; return; }
    const was = t.result && t.result.status;
    t.result = { ...item, preview, running: item.status === 'pending' };
    if (item.status === 'done' && was !== 'done' && state.selected === t) showViewer();
  });
  for (const t of tracks) renderState(t);
  if (job.status === 'done' || job.status === 'error') {
    clearInterval(state.polling);
    state.busy = false;
    show($('batch-progress'), false);
    if (job.status === 'error') notify(job.error || 'That failed.');
    const failed = items.filter((i) => i.status === 'error').length;
    if (failed) notify(`${failed} track${failed === 1 ? '' : 's'} couldn’t be made: hover “Failed” to see why.`, 'warn');
    if (!preview && tracks.length === state.tracks.length && job.zip_url) {
      state.zip = job.zip_url;
      $('zip-download').href = job.zip_url;
    }
    if (state.selected && tracks.includes(state.selected)) showViewer();
    refresh();
  }
}

/* ── wiring ──────────────────────────────────────────────────────── */

function wire() {
  checkHealth();
  $('add-tracks').addEventListener('click', () => $('tracks-input').click());
  $('tracks-empty').addEventListener('click', () => $('tracks-input').click());
  $('tracks-input').addEventListener('change', (e) => { addFiles(e.target.files); e.target.value = ''; });
  $('art-input').addEventListener('change', (e) => { replaceArtwork(e.target.files[0]); e.target.value = ''; });
  $('render-all').addEventListener('click', () => render(state.tracks, false));
  $('preview-all').addEventListener('click', () => render(state.tracks, true));

  // Drop audio anywhere on the page; drop an image on a track's artwork.
  let depth = 0;
  document.addEventListener('dragenter', (e) => { e.preventDefault(); depth += 1; document.body.classList.add('is-over'); });
  document.addEventListener('dragover', (e) => e.preventDefault());
  document.addEventListener('dragleave', () => { depth -= 1; if (depth <= 0) { depth = 0; document.body.classList.remove('is-over'); } });
  document.addEventListener('drop', (e) => {
    e.preventDefault();
    depth = 0;
    document.body.classList.remove('is-over');
    const files = [...(e.dataTransfer?.files || [])];
    const art = e.target.closest && e.target.closest('.art');
    const image = files.find((f) => (f.type || '').startsWith('image/'));
    if (art && image) {
      state.artFor = state.tracks.find((t) => t.el.contains(art));
      replaceArtwork(image);
      return;
    }
    if (files.length) addFiles(files);
  });

  Sortable.create($('track-list'), { handle: '.grip', animation: 150, onEnd: reorderFromList });

  // Space plays the selected track's clip.
  document.addEventListener('keydown', (e) => {
    if (e.key !== ' ' || (e.target.closest && e.target.closest('input, textarea, button'))) return;
    if (!state.selected) return;
    e.preventDefault();
    toggleListen(state.selected);
  });
  refresh();
}

wire();
