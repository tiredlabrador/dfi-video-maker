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
  $('render-btn').disabled = busy;
  $('preview-btn').disabled = busy;
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

function wireDropZone(zone, onFile) {
  ['dragenter', 'dragover'].forEach((event) =>
    zone.addEventListener(event, (e) => {
      e.preventDefault();
      zone.classList.add('is-over');
    }));
  ['dragleave', 'drop'].forEach((event) =>
    zone.addEventListener(event, () => zone.classList.remove('is-over')));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    const file = e.dataTransfer?.files?.[0];
    if (file) onFile(file);
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
