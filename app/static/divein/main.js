/* Dive In — wiring everything up when the page opens. */
'use strict';

async function loadDefaults() {
  const status = $('health');
  try {
    const data = await (await fetch('/api/divein/defaults')).json();
    state.defaults = data.defaults;
    $('defaults').textContent = JSON.stringify(data.defaults, null, 2);
    const missing = Object.entries(data.fonts).filter(([, ok]) => !ok).map(([n]) => n);
    status.textContent = missing.length ? `Missing ${missing.join(', ')}` : 'Ready';
    status.className = `status ${missing.length ? 'bad' : 'ok'}`;
    prepareDraftSources();
  } catch {
    status.textContent = 'Not connected';
    status.className = 'status bad';
  }
}

function showMotionOptions() {
  show($('motion-choice'), $('rings-on').checked);
}

document.addEventListener('DOMContentLoaded', () => {
  restore();
  showMotionOptions();
  loadDefaults();
  wirePhotoDrag();
  wireFraming();
  wireLook();
  wireTimeline();
  wireTransport();
  wireMedia();
  wireExport();

  wireSegment('colour-choice', (v) => { state.colour = v; renderChips(); drawTimeline(); designChanged(0); });
  wireSegment('motion-choice', (v) => { state.motion = v; designChanged(0); });
  wireSegment('format-choice', setFormat);
  for (const id of ['artist', 'episode']) $(id).addEventListener('input', () => designChanged(250));
  $('starts').addEventListener('change', () => {
    if (!startsFromText()) setError('One of the start times can’t be read. Use mm:ss or h:mm:ss, separated by commas.');
  });
  $('clip-seconds').addEventListener('input', () => {
    save(); renderChips(); drawTimeline(); updateViewer(); saveSession();
  });
  $('twitch').addEventListener('change', () => designChanged(0));
  $('rings-on').addEventListener('change', () => { showMotionOptions(); designChanged(0); });
  $('apply-overrides').addEventListener('click', () => {
    try {
      state.overrides = JSON.parse($('overrides').value || '{}');
      prepareDraftSources();
      designChanged(0);
      notify('Tuning applied.', 'ok');
    } catch (error) {
      setError(`That isn't valid JSON: ${error.message}`);
    }
  });
  $('clear-overrides').addEventListener('click', () => {
    $('overrides').value = '{}'; state.overrides = {}; prepareDraftSources(); designChanged(0);
  });

  applyLook();
  renderChips();
  drawTimeline();
  updateViewer();
  restoreSession();
});
