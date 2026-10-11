/*
 * What goes in: the mix, the camera footage, the photo. Plus remembering the
 * session, so a reload (or a restart of the app) doesn't lose your work.
 */
'use strict';

const SESSION = 'divein-session-v1';

/* ── the mix ─────────────────────────────────────────────────────── */

async function useAudio(file) {
  if (!file) return;
  $('audio-label').textContent = file.name;
  $('audio-hint').textContent = 'Copying…';
  let info;
  try {
    info = await upload('audio', file);
  } catch (error) {
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
  if (!folder) { if (!quiet) setError('Paste the footage folder’s location first.'); return; }
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
    status.textContent = '';
    button.disabled = false;
    return;
  }
  clearInterval(footagePolling);
  footagePolling = setInterval(async () => {
    let s;
    try { s = await (await fetch(`/api/divein/jobs/${job.id}`)).json(); } catch { return; }
    const current = state.audio && state.audio.token === token;
    if (s.status !== 'done' && s.status !== 'error') {
      if (current) status.textContent = `${s.message || 'Listening'}…`;
      return;
    }
    clearInterval(footagePolling);
    button.disabled = false;
    if (!current) return;                 // a different mix was chosen meanwhile
    if (s.status === 'error') {
      status.textContent = '';
      if (!quiet) setError(s.error || 'Looking through the footage failed.');
      return;
    }
    state.footage = { audio: token, matches: s.footage.matches };
    show($('lut-row'), s.footage.matches.some((m) => m.log));
    status.textContent = footageSummary(s.footage);
    renderChips();                        // filmed clips now offer the footage
    drawTimeline();
    updateViewer();
    if (!$('export-panel').hidden) renderExportList();
  }, 400);
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
  state.photo = { token: info.token, width: info.width, height: info.height, name: info.name };
  $('photo-label').textContent = info.name;
  $('photo-drop').classList.add('is-set');
  $('photo-thumb').innerHTML = `<img alt="" src="${url}">`;
  designChanged(0);
  updateViewer();
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
    useDroppedFile(e.dataTransfer?.files?.[0]);
  });
  document.addEventListener('paste', (e) => {           // paste a photo from anywhere...
    const items = [...(e.clipboardData?.items || [])];
    const typing = e.target.closest && e.target.closest('input, textarea');
    // ...except into a text box when there's text to paste.
    if (typing && items.some((i) => i.type === 'text/plain')) return;
    const item = items.find((i) => i.type.startsWith('image/'));
    if (item) { e.preventDefault(); usePhoto(item.getAsFile()); }
  });
  $('footage-btn').addEventListener('click', () => findFootage());
  $('footage-folder').addEventListener('keydown', (e) => { if (e.key === 'Enter') findFootage(); });
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
  if (!check || !check.ok) return;
  state.starts = (saved.starts || []).filter((s) => Number.isFinite(s));
  state.sources = saved.sources || [];
  state.framing = saved.framing || [];
  state.selected = Math.min(saved.selected || 0, Math.max(0, state.starts.length - 1));
  state.playhead = Number(saved.playhead) || 0;
  if (saved.view) state.view = { ...state.view, ...saved.view };
  if (saved.crop) state.crop = saved.crop;
  await mixChosen(saved.audio, false);
  if (saved.photo && saved.photo.token) {
    const url = `/api/divein/photo?token=${encodeURIComponent(saved.photo.token)}`;
    const photo = await fetch(url).catch(() => null);
    if (photo && photo.ok) await photoChosen(saved.photo, url);
  }
  notify('Picked up where you left off.', 'ok');
}
