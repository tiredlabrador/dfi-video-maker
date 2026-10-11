/*
 * Making the files: a rendered preview of one clip, and the export of all of
 * them (plus the 1:1 JPG). Export opens its own panel: what will be made,
 * progress, then the downloads with each footage clip's measured sync.
 */
'use strict';

/* ── the export panel ────────────────────────────────────────────── */

function openExport() {
  pause();
  renderExportList();
  show($('export-panel'), true);
}

function closeExport() {
  show($('export-panel'), false);
}

/** Keep the open panel up to date as clips, the photo or the footage change. */
function refreshExport() {
  if (!$('export-panel').hidden) renderExportList();
}

/**
 * The footage folder is set but its footage isn't loaded (still looking, the
 * drive is unplugged, or the app restarted). Clips that would use it mustn't
 * quietly turn into photo clips.
 */
function footageMissing() {
  return Boolean($('footage-folder').value.trim()) && !state.footage;
}

/** What will be made: one line per clip, and anything that would stop it. */
function renderExportList() {
  const list = $('export-list');
  list.innerHTML = '';
  const accent = ACCENTS[state.colour] || ACCENTS.yellow;
  let blocked = '';
  state.starts.forEach((start, i) => {
    const li = document.createElement('li');
    const source = clipSource(i);
    const problem = source === 'photo' && !state.photo ? 'needs a photo' : '';
    if (problem && !blocked) blocked = `Clip ${i + 1} uses the photo: choose one, or move it into the filmed part.`;
    li.innerHTML = `<span class="num" style="background:${accent}">${i + 1}</span>`
      + `<span class="when">${timecode(start)} – ${timecode(start + clipLength())}</span>`
      + `<span class="${problem ? 'problem' : 'src'}">${problem || (source === 'video' ? 'Footage' : 'Photo')}</span>`;
    list.append(li);
  });
  if (!state.starts.length) {
    blocked = state.audio ? 'Add a clip first: double-click the timeline, or press “+ Clip”.'
      : 'Choose the mix first.';
  } else if (footageMissing() && state.starts.some((_, i) => state.sources[i] !== 'photo')) {
    blocked = 'The footage isn’t loaded (is the drive plugged in?). Press Find, '
      + 'or clear the footage folder to make photo clips.';
  }
  const square = $('export-square');
  square.disabled = !state.photo;
  square.checked = Boolean(state.photo) && state.squareWanted;
  $('export-square-row').title = state.photo ? '' : 'Made from the photo: choose one to include it';
  const go = $('export-go');
  go.disabled = Boolean(blocked) || state.busy;
  const n = state.starts.length;
  go.textContent = `Export ${n} clip${n === 1 ? '' : 's'}`;
  go.title = blocked;
  if (blocked && !state.busy) { $('progress-text').textContent = blocked; show($('export-progress'), true); show($('progress-fill').parentElement, false); }
  else if (!state.busy) show($('export-progress'), false);
}

/* ── running a job ───────────────────────────────────────────────── */

function setBusy(busy) {
  state.busy = busy;
  $('preview-btn').disabled = busy;
  $('export-go').disabled = busy;
}

/** Ask the server to make something, and follow it until it's done. */
async function runJob(kind, body, onProgress, onDone) {
  setBusy(true);
  let job;
  try {
    job = await (await postJSON(`/api/divein/${kind}`, body)).json();
  } catch (error) {
    setBusy(false);
    onDone(null, error.message);
    return;
  }
  clearInterval(state.polling);
  state.polling = setInterval(async () => {
    let s;
    try { s = await (await fetch(`/api/divein/jobs/${job.id}`)).json(); } catch { return; }
    onProgress(s.progress || 0, s.message || 'Working…');
    if (s.status === 'done' || s.status === 'error') {
      clearInterval(state.polling);
      setBusy(false);
      onDone(s, s.status === 'error' ? (s.error || 'That failed.') : '');
    }
  }, 400);
}

/** Render the clip under the playhead, small and short, with every effect. */
function previewClip() {
  const clip = clipAtTime(state.playhead) >= 0 ? clipAtTime(state.playhead) : state.selected;
  if (!state.audio) { setError('Choose the mix first.'); return; }
  if (!state.starts.length) { setError('Add a clip first: double-click the timeline, or press “+ Clip”.'); return; }
  if (footageMissing() && state.sources[clip] !== 'photo') {
    setError('The footage isn’t loaded (is the drive plugged in?). Press Find first.');
    return;
  }
  if (clipSource(clip) === 'photo' && !state.photo) {
    setError(`Clip ${clip + 1} uses the photo: choose one, or move it into the filmed part.`);
    return;
  }
  pause();
  const button = $('preview-btn');
  const label = button.textContent;
  runJob('preview', design({ preview_index: clip }),
    (f) => { button.textContent = `Rendering ${Math.round(f * 100)}%`; },
    (job, error) => {
      button.textContent = label;
      if (error) { setError(error); return; }
      const sync = (job.sync || [])[0];
      if (sync && !sync.in_sync) notify(syncNote(sync, `Clip ${clip + 1}`), 'warn');
      if (job.files && job.files[0]) playExact(job.files[0].url);
    });
}

function startExport() {
  if (state.busy) return;
  show($('files'), false);
  show($('export-progress'), true);
  show($('progress-fill').parentElement, true);
  $('progress-fill').style.width = '0%';
  $('progress-text').textContent = 'Starting…';
  runJob('export', design({ square: Boolean(state.photo) && state.squareWanted }),
    (f, message) => {
      $('progress-fill').style.width = `${Math.round(f * 100)}%`;
      $('progress-text').textContent = message;
    },
    (job, error) => {
      show($('export-progress'), Boolean(error));
      if (error) { $('progress-text').textContent = error; setError(error); }
      if (job && job.files && job.files.length) showFiles(job);
    });
}

/* ── the results ─────────────────────────────────────────────────── */

function syncNote(s, name) {
  if (s.in_sync) return `${name}: measured in sync.`;
  if (s.checked) {
    return `${name}: ${Math.abs(s.off_by).toFixed(2)}s out of sync (the sound is `
      + `${s.off_by > 0 ? 'late' : 'early'}). Try moving the clip slightly.`;
  }
  return `${name}: the camera’s sound was too unclear to check the sync. Watch it before posting.`;
}

function showFiles(job) {
  const list = $('file-list');
  list.innerHTML = '';
  const syncs = new Map((job.sync || []).map((s) => [s.clip, s]));
  for (const f of job.files) {
    const li = document.createElement('li');
    const name = document.createElement('span');
    name.className = 'fname';
    name.textContent = f.name;
    name.title = f.name;
    const top = document.createElement('div');
    top.className = 'top';
    const size = document.createElement('span');
    size.className = 'size';
    size.textContent = `${(f.size / 1048576).toFixed(1)} MB`;
    top.append(size);
    if (f.name.endsWith('.mp4')) {
      const play = document.createElement('button');
      play.type = 'button';
      play.textContent = 'Watch';
      play.addEventListener('click', () => playExact(f.url));
      top.append(play);
    }
    const link = document.createElement('a');
    link.href = f.url;
    link.download = f.name;
    link.textContent = 'Download';
    top.append(link);
    li.append(name, top);
    const number = Number((f.name.match(/ - (\d+)\.mp4$/) || [])[1] || (f.name.endsWith('.mp4') ? 1 : 0));
    const s = syncs.get(number);
    if (s) {
      const note = document.createElement('p');
      note.className = `sync${s.in_sync ? '' : ' bad'}`;
      note.textContent = s.in_sync ? 'Footage measured in sync' : syncNote(s, 'This clip').replace('This clip: ', '');
      li.append(note);
    }
    list.append(li);
  }
  const zip = $('zip-link');
  if (job.zip_url) { zip.href = job.zip_url; zip.download = 'Dive In.zip'; }
  show(zip, Boolean(job.zip_url));
  show($('files'), true);
}

function wireExport() {
  $('export-btn').addEventListener('click', () => {
    if ($('export-panel').hidden) openExport(); else closeExport();
  });
  $('export-close').addEventListener('click', closeExport);
  $('export-go').addEventListener('click', startExport);
  $('export-square').addEventListener('change', (e) => { state.squareWanted = e.target.checked; });
  $('preview-btn').addEventListener('click', previewClip);
  $('exact-close').addEventListener('click', closeExact);
}
