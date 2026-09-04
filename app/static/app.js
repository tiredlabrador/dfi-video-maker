/*
 * DFI Video Maker — the page.
 *
 * Talks to the small Python server running on this machine. Everything stays
 * local: the "upload" is a copy between two folders on your own disk.
 */
'use strict';

const $ = (id) => document.getElementById(id);

const state = {
  audioFile: null,
  artFile: null,
  audioDuration: 0,
  polling: null,
  batch: [],          // one entry per track, in posting order
};

/* ── Helpers ─────────────────────────────────────────────────────── */

function show(element, visible) {
  element.hidden = !visible;
}

function parseTimecode(text) {
  const parts = String(text || '').trim().split(':').map(Number);
  if (parts.some(Number.isNaN)) return null;
  return parts.reduce((total, part) => total * 60 + part, 0);
}

async function postForm(path, formData) {
  const response = await fetch(path, { method: 'POST', body: formData });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Request failed (${response.status}).`);
  }
  return payload;
}

function setError(message) {
  const box = $('error');
  box.textContent = message || '';
  show(box, Boolean(message));
}

/* ── Health check on load ────────────────────────────────────────── */

async function checkHealth() {
  try {
    const health = await (await fetch('/api/health')).json();
    if (!health.ffmpeg || !health.ffprobe) {
      $('health').textContent = 'ffmpeg is missing — see INSTALL.md';
      $('health').classList.add('bad');
      return;
    }
    // Missing brand assets do not stop a render, but they change what comes
    // out, and that is far cheaper to notice now than after posting.
    const missing = [];
    if (!health.brand_font) missing.push('the Squid Boy font');
    if (!health.overlay) missing.push('the logo overlay');
    if (!health.fallback_art) missing.push('the fallback artwork');
    if (missing.length) {
      $('health').textContent = `Missing ${missing.join(', ')}`;
      $('health').classList.add('bad');
      $('health').title = 'Videos will still render, but they will not look right.';
    } else {
      $('health').textContent = 'Ready';
    }
  } catch {
    $('health').textContent = 'Not connected';
    $('health').classList.add('bad');
  }
}

/* ── Choosing an audio file ──────────────────────────────────────── */

async function useAudioFile(file) {
  if (!file) return;
  state.audioFile = file;
  $('audio-label').textContent = file.name;
  $('audio-drop').classList.add('is-set');
  setError('');

  const form = new FormData();
  form.append('audio', file);
  if (state.artFile) form.append('artwork', state.artFile);

  $('artwork-source').textContent = 'Reading…';
  show($('audio-detail'), true);

  let info;
  try {
    info = await postForm('/api/inspect', form);
  } catch (error) {
    setError(error.message);
    return;
  }

  if (!info.readable) {
    setError(info.error || 'That file could not be read as audio.');
  }

  // Only fill boxes the user has not typed into, so we never overwrite them.
  if (info.track && !$('track').value) $('track').value = info.track;
  if (info.artist && !$('artist').value) $('artist').value = info.artist;

  state.audioDuration = info.duration || 0;
  $('audio-duration').textContent = info.duration_label || '—';
  $('artwork-source').textContent = {
    embedded: "The track's own cover art",
    override: 'The image you chose',
    fallback: 'The DFI label design',
    none: 'None — this will fail',
  }[info.artwork_source] || '—';

  if (info.artwork_preview) {
    $('artwork-preview').src = info.artwork_preview;
    $('artwork-note').textContent =
      'This is the artwork the video will use. Check it before rendering.';
    show($('stage-empty'), false);
    show($('stage-art'), true);
    show($('player'), false);
  }

  validateClipStart();
}

/* ── Clip start sanity ───────────────────────────────────────────── */

function validateClipStart() {
  const warning = $('clip-warning');
  const seconds = parseTimecode($('clip-start').value);
  if (seconds === null) {
    warning.textContent = "That doesn't look like mm:ss.";
    show(warning, true);
    return false;
  }
  if (state.audioDuration && seconds + 25 > state.audioDuration) {
    warning.textContent =
      `The track is only ${$('audio-duration').textContent} long, so the clip will run short.`;
    show(warning, true);
    return true;                        // a warning, not a refusal
  }
  show(warning, false);
  return true;
}

/* ── Rendering ───────────────────────────────────────────────────── */

async function render(preview) {
  if (!state.audioFile) {
    setError('Choose an audio file first.');
    return;
  }
  validateClipStart();
  setError('');

  const form = new FormData();
  form.append('audio', state.audioFile);
  if (state.artFile) form.append('artwork', state.artFile);
  form.append('track', $('track').value.trim());
  form.append('artist', $('artist').value.trim());
  form.append('clip_start', $('clip-start').value.trim() || '0:00');
  if (preview) form.append('preview', '1');

  setBusy(true);
  show($('batch-results'), false);
  show($('facts'), false);
  updateProgress(0, preview ? 'Preparing a quick preview…' : 'Starting the render…');

  let job;
  try {
    job = await postForm('/api/render', form);
  } catch (error) {
    setError(error.message);
    setBusy(false);
    return;
  }
  pollJob(job.id, preview);
}

function pollJob(jobId, preview) {
  clearInterval(state.polling);
  state.polling = setInterval(async () => {
    let job;
    try {
      job = await (await fetch(`/api/jobs/${jobId}`)).json();
    } catch {
      return;                          // a blip; the next tick will catch up
    }
    updateProgress(job.progress, job.message);

    if (job.status === 'done') {
      clearInterval(state.polling);
      setBusy(false);
      showResult(job, preview);
    } else if (job.status === 'error') {
      clearInterval(state.polling);
      setBusy(false);
      show($('progress'), false);
      setError(job.error || 'The render failed.');
    }
  }, 400);
}

function updateProgress(fraction, message) {
  show($('progress'), true);
  $('progress-fill').style.width = `${Math.round((fraction || 0) * 100)}%`;
  $('progress-text').textContent = message || 'Working…';
}

function setBusy(busy) {
  for (const id of ['render-btn', 'preview-btn',
                    'batch-render-btn', 'batch-preview-btn']) {
    const button = $(id);
    if (button) button.disabled = busy;
  }
}

function showResult(job, preview) {
  show($('progress'), false);
  show($('stage-empty'), false);
  show($('stage-art'), false);

  const player = $('player');
  player.src = `${job.download_url}?t=${Date.now()}`;
  show(player, true);
  player.load();

  const probe = job.probe || {};
  const facts = {
    Size: `${probe.width}×${probe.height}`,
    Length: `${(probe.duration || 0).toFixed(3)} s`,
    Video: `${probe.video_codec} (${probe.pix_fmt})`,
    Audio: `${probe.audio_codec}, ${probe.channels} ch @ ${probe.sample_rate} Hz`,
    'File size': `${((probe.size_bytes || 0) / 1048576).toFixed(1)} MB`,
  };

  // The spec the finished video is meant to meet. Shown rather than assumed:
  // "it rendered" and "it is correct" are different claims.
  const checks = preview ? {} : {
    Size: probe.width === 1080 && probe.height === 1350,
    Length: Math.abs((probe.duration || 0) - 25) <= 0.05,
    Video: probe.video_codec === 'h264' && probe.pix_fmt === 'yuv420p',
    Audio: probe.audio_codec === 'aac' && probe.channels === 2,
  };

  const list = $('facts-list');
  list.innerHTML = '';
  for (const [label, value] of Object.entries(facts)) {
    const term = document.createElement('dt');
    term.textContent = label;
    const detail = document.createElement('dd');
    detail.textContent = value;
    if (label in checks) detail.className = checks[label] ? 'pass' : 'fail';
    list.append(term, detail);
  }

  const download = $('download');
  download.href = job.download_url;
  download.setAttribute('download', job.filename || 'video.mp4');
  download.textContent = preview
    ? 'Download the preview' : 'Download the MP4';
  show($('facts'), true);
}

/* ── Wiring ──────────────────────────────────────────────────────── */

function wireDropZone(zone, onFile, onFiles) {
  ['dragenter', 'dragover'].forEach((event) =>
    zone.addEventListener(event, (e) => {
      e.preventDefault();
      zone.classList.add('is-over');
    }));
  ['dragleave', 'drop'].forEach((event) =>
    zone.addEventListener(event, () => zone.classList.remove('is-over')));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    const files = [...(e.dataTransfer?.files || [])];
    if (!files.length) return;
    if (onFiles) onFiles(files);
    else if (onFile) onFile(files[0]);
  });
}

document.addEventListener('DOMContentLoaded', () => {
  checkHealth();

  $('audio-input').addEventListener('change', (e) => useAudioFile(e.target.files[0]));
  wireDropZone($('audio-drop'), useAudioFile);

  $('art-input').addEventListener('change', (e) => {
    state.artFile = e.target.files[0] || null;
    $('art-label').textContent = state.artFile ? state.artFile.name
                                               : 'Use a different image instead';
    $('art-drop').classList.toggle('is-set', Boolean(state.artFile));
    show($('clear-art'), Boolean(state.artFile));
    if (state.audioFile) useAudioFile(state.audioFile);
  });
  wireDropZone($('art-drop'), (file) => {
    state.artFile = file;
    $('art-label').textContent = file.name;
    $('art-drop').classList.add('is-set');
    show($('clear-art'), true);
    if (state.audioFile) useAudioFile(state.audioFile);
  });

  $('clear-art').addEventListener('click', () => {
    state.artFile = null;
    $('art-input').value = '';
    $('art-label').textContent = 'Use a different image instead';
    $('art-drop').classList.remove('is-set');
    show($('clear-art'), false);
    if (state.audioFile) useAudioFile(state.audioFile);
  });

  $('clip-start').addEventListener('input', validateClipStart);
  $('preview-btn').addEventListener('click', () => render(true));
  $('render-btn').addEventListener('click', () => render(false));
});


/* ══ A whole batch ═══════════════════════════════════════════════════
 *
 * The list order IS the posting order, and the numbers shown are the numbers
 * the files get. That is why moving a track re-labels everything immediately:
 * the screen should never disagree with what will come out.
 */

function switchTab(which) {
  const single = which === 'single';
  $('tab-single').classList.toggle('is-active', single);
  $('tab-batch').classList.toggle('is-active', !single);
  $('tab-single').setAttribute('aria-selected', String(single));
  $('tab-batch').setAttribute('aria-selected', String(!single));
  show($('pane-single'), single);
  show($('pane-batch'), !single);
}

async function addBatchFiles(files) {
  setError('');
  for (const file of files) {
    const entry = {
      file, name: file.name, token: null,
      track: '', artist: '', clipStart: '0:00', reading: true,
    };
    state.batch.push(entry);
    renderBatchRows();

    // Read tags in the background so the rows fill themselves in.
    try {
      const form = new FormData();
      form.append('audio', file);
      const info = await postForm('/api/inspect', form);
      entry.token = info.upload_token;
      entry.track = info.track || stripExtension(file.name);
      entry.artist = info.artist || '';
      entry.duration = info.duration || 0;
      if (!info.readable) entry.problem = info.error;
    } catch (error) {
      entry.problem = error.message;
    }
    entry.reading = false;
    renderBatchRows();
  }
}

function stripExtension(name) {
  return name.replace(/\.[^.]+$/, '');
}

function renderBatchRows() {
  const list = $('batch-rows');
  list.innerHTML = '';
  show($('batch-empty'), state.batch.length === 0);

  state.batch.forEach((entry, index) => {
    const row = document.createElement('li');
    row.className = 'row';

    const number = document.createElement('div');
    number.className = 'row-number';
    number.textContent = String(index + 1).padStart(2, '0');

    const fields = document.createElement('div');
    fields.className = 'row-fields';
    const track = document.createElement('input');
    track.type = 'text';
    track.placeholder = entry.reading ? 'Reading…' : 'Track title';
    track.value = entry.track;
    track.addEventListener('input', () => { entry.track = track.value; });
    const artist = document.createElement('input');
    artist.type = 'text';
    artist.className = 'row-artist';
    artist.placeholder = 'Artist';
    artist.value = entry.artist;
    artist.addEventListener('input', () => { entry.artist = artist.value; });
    fields.append(track, artist);

    const start = document.createElement('input');
    start.type = 'text';
    start.className = 'row-start';
    start.value = entry.clipStart;
    start.title = 'Clip start (mm:ss)';
    start.addEventListener('input', () => { entry.clipStart = start.value; });

    const tools = document.createElement('div');
    tools.className = 'row-tools';
    tools.append(
      toolButton('▲', 'Move up', index === 0, () => moveBatch(index, -1)),
      toolButton('▼', 'Move down', index === state.batch.length - 1,
                 () => moveBatch(index, 1)),
      toolButton('✕', 'Remove', false, () => {
        state.batch.splice(index, 1);
        renderBatchRows();
      }, 'row-remove'),
    );

    row.append(number, fields, start, tools);
    if (entry.problem) {
      row.title = entry.problem;
      row.style.borderColor = 'rgba(255,107,94,.5)';
    }
    list.append(row);
  });
}

function toolButton(label, title, disabled, onClick, extraClass) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = label;
  button.title = title;
  button.disabled = disabled;
  if (extraClass) button.className = extraClass;
  button.addEventListener('click', onClick);
  return button;
}

function moveBatch(index, direction) {
  const target = index + direction;
  if (target < 0 || target >= state.batch.length) return;
  [state.batch[index], state.batch[target]] =
    [state.batch[target], state.batch[index]];
  renderBatchRows();
}

async function renderBatchNow(preview) {
  const ready = state.batch.filter((entry) => entry.token);
  if (!ready.length) {
    setError('Add some audio files first.');
    return;
  }
  if (ready.length !== state.batch.length) {
    setError('Some files are still being read. Give it a second.');
    return;
  }

  setError('');
  setBusy(true);
  show($('facts'), false);
  updateProgress(0, 'Starting the batch…');

  let job;
  try {
    const response = await fetch('/api/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        preview,
        items: state.batch.map((entry) => ({
          upload_token: entry.token,
          track: entry.track.trim(),
          artist: entry.artist.trim(),
          clip_start: entry.clipStart.trim() || '0:00',
        })),
      }),
    });
    job = await response.json();
    if (!response.ok) throw new Error(job.error || 'The batch could not start.');
  } catch (error) {
    setError(error.message);
    setBusy(false);
    return;
  }
  pollBatch(job.id);
}

function pollBatch(jobId) {
  clearInterval(state.polling);
  state.polling = setInterval(async () => {
    let job;
    try {
      job = await (await fetch(`/api/jobs/${jobId}`)).json();
    } catch { return; }

    updateProgress(job.progress, job.message);
    showBatchResults(job);

    if (job.status === 'done' || job.status === 'error') {
      clearInterval(state.polling);
      setBusy(false);
      if (job.status === 'error') setError(job.error || 'The batch failed.');
      if (job.zip_url) {
        const zip = $('zip-download');
        zip.href = job.zip_url;
        show(zip, true);
      }
    }
  }, 500);
}

function showBatchResults(job) {
  const items = job.items || [];
  if (!items.length) return;
  show($('batch-results'), true);

  const list = $('batch-result-list');
  list.innerHTML = '';
  for (const item of items) {
    const row = document.createElement('li');
    row.className = item.status === 'done' ? 'is-done'
                  : item.status === 'error' ? 'is-error' : '';

    const name = document.createElement('span');
    name.className = 'result-name';
    name.textContent = item.filename;

    const stateLabel = document.createElement('span');
    stateLabel.className = 'result-state';
    stateLabel.textContent = item.status === 'done' ? 'Ready'
                           : item.status === 'error' ? 'Failed' : 'Waiting';
    if (item.error) {
      stateLabel.title = item.error;
      row.title = item.error;
    }

    row.append(name, stateLabel);

    if (item.download_url) {
      const play = document.createElement('button');
      play.type = 'button';
      play.className = 'result-play';
      play.textContent = 'Watch';
      play.addEventListener('click', () => {
        show($('stage-empty'), false);
        show($('stage-art'), false);
        const player = $('player');
        player.src = item.download_url;
        show(player, true);
        player.load();
        player.play().catch(() => {});
      });
      row.append(play);
    }
    list.append(row);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  $('tab-single').addEventListener('click', () => switchTab('single'));
  $('tab-batch').addEventListener('click', () => switchTab('batch'));

  $('batch-input').addEventListener('change', (e) => {
    addBatchFiles([...e.target.files]);
    e.target.value = '';
  });
  wireDropZone($('batch-drop'), null, (files) => addBatchFiles(files));

  $('batch-render-btn').addEventListener('click', () => renderBatchNow(false));
  $('batch-preview-btn').addEventListener('click', () => renderBatchNow(true));
});
