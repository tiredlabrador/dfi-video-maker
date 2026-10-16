/*
 * What goes in: the mix, the camera footage, the photo. Plus remembering the
 * session, so a reload (or a restart of the app) doesn't lose your work.
 */
'use strict';

const SESSION = 'divein-session-v1';
let chosenThisVisit = false;              // a choice you've made beats the last session
let audioSeq = 0;                         // the latest mix chosen wins, however slow the others

/* ── the mix ─────────────────────────────────────────────────────── */

async function useAudio(file) {
  if (!file) return;
  chosenThisVisit = true;
  const seq = ++audioSeq;
  $('audio-label').textContent = file.name;
  $('audio-hint').textContent = 'Copying…';
  let info;
  try {
    info = await upload('audio', file);
    if (seq !== audioSeq) return;                   // another mix was chosen meanwhile
  } catch (error) {
    if (seq !== audioSeq) return;
    $('audio-label').textContent = state.audio ? state.audio.name : 'Choose the mix';
    $('audio-hint').textContent = state.audio ? timecode(state.audio.duration) : 'or drop it anywhere';
    setError(error.message);
    return;
  }
  await mixChosen({ ...info, name: file.name }, true);
}

/** A mix is ready on the server: set everything up for it. `fresh`: a new choice, not a restore. */
async function mixChosen(info, fresh) {
  pause();
  closeExact();
  state.audio = { token: info.token, duration: info.duration, name: info.name || 'Mix' };
  $('audio-label').textContent = state.audio.name;
  $('audio-hint').textContent = timecode(state.audio.duration);
  $('audio-drop').classList.add('is-set');
  state.overview = null;
  state.detail = null;
  state.transitions = [];
  state.footage = null;
  show($('lut-row'), false);
  $('footage-status').textContent = '';
  if (fresh) {
    state.starts = [];
    state.sources = [];
    state.framing = [];
    state.selected = 0;
    state.playhead = 0;
    state.view.start = 0;
  }
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
  drawTimeline();
  updateViewer();
  findTransitions(token);                 // a few seconds more; drawn when ready
  findDetail(token);
  if ($('footage-folder').value.trim()) findFootage(true);   // the last folder, ready to go
  saveSession();
}

/* ── the camera footage ──────────────────────────────────────────── */

let footagePolling = null;

/** Look for the mix in the footage folder. `quiet`: done for you, so only report success. */
async function findFootage(quiet = false) {
  if (!state.audio) { if (!quiet) setError('Choose the mix first.'); return; }
  const folder = $('footage-folder').value.trim();
  if (!folder) { if (!quiet) setError('Choose the footage folder first.'); return; }
  save();
  const token = state.audio.token;
  state.footage = null;                   // the old strip belongs to the old folder
  if (!grade.ready) grade.problem = '';    // the colour file may have been added since
  show($('lut-row'), false);
  drawTimeline();
  updateViewer();
  const status = $('footage-status');
  const button = $('footage-btn');
  button.disabled = true;
  status.textContent = 'Looking…';
  let job;
  try {
    job = await (await postJSON('/api/divein/footage', { audio: token, folder })).json();
  } catch (error) {
    if (!quiet) setError(error.message);
    status.textContent = error.message;             // shown even when looked for quietly
    button.disabled = false;
    refreshExport();
    return;
  }
  clearInterval(footagePolling);
  footagePolling = setInterval(async () => {
    let s;
    try { s = await (await fetch(`/api/divein/jobs/${job.id}`)).json(); } catch { return; }
    const current = state.audio && state.audio.token === token
      && $('footage-folder').value.trim() === folder;     // not since removed or replaced
    if (s.status !== 'done' && s.status !== 'error') {
      if (current) status.textContent = `${s.message || 'Listening'}…`;
      return;
    }
    clearInterval(footagePolling);
    button.disabled = false;
    if (!current) return;                 // a different mix was chosen meanwhile
    if (s.status === 'error') {
      status.textContent = s.error || 'Looking through the footage failed.';
      if (!quiet) setError(s.error || 'Looking through the footage failed.');
      refreshExport();
      return;
    }
    state.footage = { audio: token, matches: s.footage.matches };
    show($('lut-row'), s.footage.matches.some((m) => m.log));
    status.textContent = footageSummary(s.footage);
    renderChips();                        // filmed clips now offer the footage
    drawTimeline();
    updateViewer();
    refreshExport();
  }, 400);
}

const VIDEO_FILE = /\.(mp4|mov)$/i;

/** Show the chosen folder (or none) on the footage box. */
function showFolder(path) {
  $('footage-folder').value = path || '';
  const parts = (path || '').split('/').filter(Boolean);
  const where = parts.slice(parts[0] === 'Volumes' ? 1 : 0, -1).join(' › ');
  $('footage-label').textContent = parts.length ? parts[parts.length - 1] : 'Choose the footage folder';
  $('footage-hint').textContent = parts.length ? (where || '/') : 'or drop it anywhere';
  $('footage-drop').title = path || 'Click to choose the folder, or drop it anywhere on this page';
  $('footage-drop').classList.toggle('is-set', Boolean(path));
  show($('footage-actions'), Boolean(path));
}

/** A folder was chosen or dropped: remember it, and look for the mix in it. */
function useFolder(path) {
  showFolder(path);
  save();
  if (state.audio) findFootage();
  else $('footage-status').textContent = 'Now choose the mix: it’s looked for in this footage.';
}

function removeFolder() {
  clearInterval(footagePolling);
  $('footage-btn').disabled = false;
  showFolder('');
  save();
  state.footage = null;
  show($('lut-row'), false);
  $('footage-status').textContent = '';
  renderChips();
  drawTimeline();
  updateViewer();
  refreshExport();
}

/** Click the box: the Mac's own folder chooser opens (the app runs on this Mac). */
let choosing = false;
async function chooseFolder() {
  if (choosing) return;
  choosing = true;
  const hint = $('footage-hint');
  const before = hint.textContent;
  hint.textContent = 'Choose it in the window that just opened…';
  try {
    const { folder } = await (await postJSON('/api/divein/choose-folder',
      { start: $('footage-folder').value })).json();
    if (folder) useFolder(folder); else hint.textContent = before;
  } catch (error) {
    hint.textContent = before;
    setError(error.message);
  } finally {
    choosing = false;
  }
}

/**
 * A browser never says where a dropped folder lives, only its name and what's
 * in it. So the app looks on this Mac's drives for that folder with those
 * exact videos (sizes to the byte).
 */
async function locateDropped(name, videos) {
  $('footage-label').textContent = name || 'Footage';
  $('footage-hint').textContent = 'Finding it on this Mac…';
  try {
    const { folder } = await (await postJSON('/api/divein/locate-folder', {
      name, files: videos.map((f) => ({ name: f.name, size: f.size })),
    })).json();
    useFolder(folder);
  } catch (error) {
    showFolder($('footage-folder').value);
    setError(error.message);
  }
}

async function dropFolder(entry) {
  let videos;
  try {
    const reader = entry.createReader();
    const entries = [];
    for (;;) {                            // handed over a hundred or so at a time
      const batch = await new Promise((ok, fail) => reader.readEntries(ok, fail));
      if (!batch.length) break;
      entries.push(...batch);
    }
    videos = await Promise.all(entries
      .filter((e) => e.isFile && !e.name.startsWith('.') && VIDEO_FILE.test(e.name))
      .slice(0, 6)
      .map((e) => new Promise((ok, fail) => e.file(ok, fail))));
  } catch {
    setError('Couldn’t read that folder.');
    return;
  }
  if (!videos.length) { setError('That folder has no videos in it (MP4 or MOV).'); return; }
  locateDropped(entry.name, videos);
}

function footageSummary(found) {
  const matches = found.matches || [];
  if (!matches.length) return 'This mix isn’t in that footage.';
  const videos = new Set(matches.map((m) => m.video)).size;
  const covered = matches.map((m) => `${timecode(m.mix_from)}–${timecode(m.mix_to)}`).join(', ');
  return `Filmed ${covered} · ${videos} video${videos === 1 ? '' : 's'}`;
}

/* ── the photo ───────────────────────────────────────────────────── */

async function usePhoto(file) {
  if (!file) return;
  chosenThisVisit = true;
  if (file.type && !file.type.startsWith('image/')) { setError('That isn’t a photo.'); return; }
  const name = file.name || 'Pasted photo';
  $('photo-label').textContent = 'Reading…';
  let info;
  try {
    info = await upload('photo', file);
  } catch (error) {
    $('photo-label').textContent = state.photo ? state.photo.name : 'Choose a photo';
    setError(error.message);
    return;
  }
  resetCrop();
  await photoChosen({ ...info, name }, URL.createObjectURL(file));
}

/** A photo is ready on the server: show it and set up its quick drafts. */
async function photoChosen(info, url) {
  if (state.photoURL && state.photoURL.startsWith('blob:') && state.photoURL !== url) {
    URL.revokeObjectURL(state.photoURL);            // the old photo's copy in memory
  }
  state.photoURL = url;
  state.photo = { token: info.token, width: info.width, height: info.height, name: info.name };
  $('photo-label').textContent = info.name;
  $('photo-drop').classList.add('is-set');
  $('photo-thumb').innerHTML = `<img alt="" src="${url}">`;
  designChanged(0);
  updateViewer();
  refreshExport();
  saveSession();
  const img = new Image();
  img.src = url;
  try {
    await img.decode();
    state.photoEl = img;
    prepareDraftSources();
  } catch { state.photoEl = null; state.srcBase = null; }
}

/* ── dropping files anywhere ─────────────────────────────────────── */

function useDroppedFile(file) {
  if (!file) return;
  const isImage = (file.type || '').startsWith('image/') || /\.(jpe?g|png|webp|heic|tiff?)$/i.test(file.name);
  if (isImage) usePhoto(file); else useAudio(file);
}

function wireMedia() {
  $('audio-drop').addEventListener('click', () => $('audio-input').click());
  $('photo-drop').addEventListener('click', () => $('photo-input').click());
  $('audio-input').addEventListener('change', (e) => { useAudio(e.target.files[0]); e.target.value = ''; });
  $('photo-input').addEventListener('change', (e) => { usePhoto(e.target.files[0]); e.target.value = ''; });
  // Drop a mix or a photo anywhere on the page.
  let depth = 0;
  document.addEventListener('dragenter', (e) => { e.preventDefault(); depth += 1; $('stage').classList.add('is-over'); });
  document.addEventListener('dragover', (e) => e.preventDefault());
  document.addEventListener('dragleave', () => { depth -= 1; if (depth <= 0) { depth = 0; $('stage').classList.remove('is-over'); } });
  document.addEventListener('drop', (e) => {
    e.preventDefault();
    depth = 0;
    $('stage').classList.remove('is-over');
    // A folder, or some videos: the camera footage. Read now: the drop's
    // contents can't be read once this handler returns.
    const entry = e.dataTransfer?.items?.[0]?.webkitGetAsEntry?.();
    if (entry && entry.isDirectory) { dropFolder(entry); return; }
    const files = [...(e.dataTransfer?.files || [])];
    if (files.length && files.every((f) => VIDEO_FILE.test(f.name))) {
      locateDropped(null, files.slice(0, 6));
      return;
    }
    useDroppedFile(files[0]);
  });
  document.addEventListener('paste', (e) => {           // paste a photo from anywhere...
    const items = [...(e.clipboardData?.items || [])];
    const typing = e.target.closest && e.target.closest('input, textarea');
    // ...except into a text box when there's text to paste.
    if (typing && items.some((i) => i.type === 'text/plain')) return;
    const item = items.find((i) => i.type.startsWith('image/'));
    if (item) { e.preventDefault(); usePhoto(item.getAsFile()); }
  });
  $('footage-drop').addEventListener('click', chooseFolder);
  $('footage-btn').addEventListener('click', () => findFootage());
  $('footage-clear').addEventListener('click', removeFolder);
}

/* ── remembering the session ─────────────────────────────────────── */

let sessionTimer = null;

/** Remember the mix, photo, clips and framing, so a reload picks up where you were. */
function saveSession() {
  clearTimeout(sessionTimer);
  sessionTimer = setTimeout(() => {
    try {
      localStorage.setItem(SESSION, JSON.stringify({
        audio: state.audio, photo: state.photo, crop: state.crop,
        starts: state.starts, sources: state.sources, framing: state.framing,
        selected: state.selected, playhead: state.playhead, view: state.view,
      }));
    } catch { /* private window: fine */ }
  }, 300);
}

/** Pick up the last session, if the app still has its files. */
async function restoreSession() {
  let saved;
  try { saved = JSON.parse(localStorage.getItem(SESSION) || 'null'); } catch { saved = null; }
  if (!saved || !saved.audio || !saved.audio.token) return;
  // Ask for the mix's outline: if the app still has the file, it answers.
  const check = await fetch(`/api/divein/overview?audio=${encodeURIComponent(saved.audio.token)}`)
    .catch(() => null);
  if (!check || !check.ok || chosenThisVisit) return;
  state.starts = (saved.starts || []).filter((s) => Number.isFinite(s));
  state.sources = saved.sources || [];
  state.framing = saved.framing || [];
  state.selected = Math.min(saved.selected || 0, Math.max(0, state.starts.length - 1));
  state.playhead = Number(saved.playhead) || 0;
  if (saved.view) state.view = { ...state.view, ...saved.view };
  if (saved.crop) state.crop = saved.crop;
  await mixChosen(saved.audio, false);
  if (saved.photo && saved.photo.token && !state.photo) {
    const url = `/api/divein/photo?token=${encodeURIComponent(saved.photo.token)}`;
    const photo = await fetch(url).catch(() => null);
    if (chosenThisVisit && state.photo) { /* you chose another meanwhile */ }
    else if (photo && photo.ok) await photoChosen(saved.photo, url);
    else notify('The photo from last time is gone: choose it again.', 'warn');
  }
  notify('Picked up where you left off.', 'ok');
}
