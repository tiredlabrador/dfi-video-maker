/*
 * How the footage looks in the viewer: the Dive In look (black and white,
 * contrast, grain, dark edges, brightness) shown live, and the D-Log colour
 * fix applied by the graphics chip. The export draws all of it exactly.
 */
'use strict';

/* ── the look, live ──────────────────────────────────────────────── */

const LOOK = ['bw', 'contrast', 'grain', 'vignette', 'ghost'];

function footageLook() {
  return { ...Object.fromEntries(LOOK.map((k) => [k, isOn(`look-${k}`)])),
           brightness: Number($('look-brightness').value) || 0 };
}

/** The brightness slider as a gain: +50 is 1.5x, as the export applies it. */
function brightnessGain() {
  return 1 + (Number($('look-brightness').value) || 0) / 100;
}

/** A grey noise tile for the live grain (the export draws its own, exactly). */
function grainTile() {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const ctx = c.getContext('2d'), img = ctx.createImageData(256, 256);
  for (let i = 0; i < img.data.length; i += 4) {
    // Roughly normal, centred on mid grey, spread like the export's (sd 40).
    const n = (Math.random() + Math.random() + Math.random() - 1.5) * 80 + 128;
    img.data[i] = img.data[i + 1] = img.data[i + 2] = n;
    img.data[i + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  return `url(${c.toDataURL()})`;
}

let grainTimer = null;

/**
 * Show the look on the live footage: black and white and contrast as CSS
 * filters (the same sums), dark edges and moving grain as layers on top.
 */
function applyLook() {
  const look = footageLook();
  const filter = [look.bw ? 'grayscale(1)' : '', look.contrast ? 'contrast(1.35) brightness(0.9)' : '']
    .filter(Boolean).join(' ');
  // Brightness goes first, on the footage as filmed. On the colour-corrected
  // view it's applied inside the correction, before DJI's colour file.
  const gain = brightnessGain();
  $('footage').style.filter = [gain !== 1 ? `brightness(${gain})` : '', filter].filter(Boolean).join(' ');
  $('graded').style.filter = filter;
  $('look-brightness-label').textContent = `${look.brightness > 0 ? '+' : ''}${look.brightness}`;
  grade.drawn = '';
  show($('footage-vignette'), look.vignette);
  // Centred on the same part of the design as the export's, whichever shape.
  const [, H] = canvasSize();
  const centre = (setting('vignette.centre_y', 0.375) * 1350 + (H - 1350) / 2) / H;
  $('footage-vignette').style.background = 'radial-gradient(ellipse farthest-corner at 50% '
    + `${(centre * 100).toFixed(1)}%, transparent 45%, rgba(0,0,0,.65) 100%)`;
  const grain = $('footage-grain');
  show(grain, look.grain);
  if (look.grain && !grain.style.backgroundImage) grain.style.backgroundImage = grainTile();
  clearInterval(grainTimer);
  if (look.grain && $('stage').classList.contains('is-footage')) {
    grainTimer = setInterval(() => {                 // a new grain every couple of frames
      grain.style.backgroundPosition = `${Math.random() * 256}px ${Math.random() * 256}px`;
    }, 80);
  }
}

/* ── the D-Log colour fix (DJI's colour file, applied by the GPU) ── */

const grade = { gl: null, video: null, size: 0, crop: null, gain: null, ready: false,
                problem: '', loop: null, drawn: '', verified: false, blank: 0 };

/** Read DJI's .cube file: its size, and its colours (red changing fastest). */
function parseCube(text) {
  let size = 0;
  const values = [];
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    if (t.startsWith('LUT_3D_SIZE')) { size = parseInt(t.split(/\s+/)[1], 10); continue; }
    if (/^[A-Z_]/.test(t)) continue;                     // TITLE, DOMAIN_MIN and the like
    const parts = t.split(/\s+/).map(Number);
    if (parts.length === 3 && parts.every(Number.isFinite)) values.push(...parts);
  }
  if (!size || values.length !== size * size * size * 3) throw new Error('That colour file isn’t a 3D LUT.');
  return { size, data: new Float32Array(values) };
}

function compile(gl, type, source) {
  const s = gl.createShader(type);
  gl.shaderSource(s, source);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
  return s;
}

/** Fetch the colour file once and set up the drawing. False if it can't be done. */
async function loadGrade() {
  if (grade.ready || grade.problem) return grade.ready;
  try {
    const response = await fetch('/api/divein/lut');
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error
      || 'The colour file couldn’t be loaded.');
    const lut = parseCube(await response.text());
    const gl = $('graded').getContext('webgl2', { premultipliedAlpha: false });
    if (!gl) throw new Error('This browser can’t correct colours live. The export still will.');
    const program = gl.createProgram();
    gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, `#version 300 es
      in vec2 corner; out vec2 uv;
      void main() { uv = corner * 0.5 + 0.5; gl_Position = vec4(corner, 0.0, 1.0); }`));
    gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, `#version 300 es
      precision highp float; precision highp sampler3D;
      in vec2 uv; out vec4 colour;
      uniform sampler2D picture; uniform sampler3D lut; uniform vec4 crop; uniform float size;
      uniform float gain;
      void main() {
        vec3 c = clamp(texture(picture, crop.xy + uv * crop.zw).rgb * gain, 0.0, 1.0);
        colour = vec4(texture(lut, c * ((size - 1.0) / size) + 0.5 / size).rgb, 1.0);
      }`));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    gl.useProgram(program);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const corner = gl.getAttribLocation(program, 'corner');
    gl.enableVertexAttribArray(corner);
    gl.vertexAttribPointer(corner, 2, gl.FLOAT, false, 0, 0);

    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_3D, gl.createTexture());
    gl.texImage3D(gl.TEXTURE_3D, 0, gl.RGB16F, lut.size, lut.size, lut.size, 0,
      gl.RGB, gl.FLOAT, lut.data);
    for (const [k, v] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR],
      [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE],
      [gl.TEXTURE_WRAP_R, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_3D, k, v);
    gl.uniform1i(gl.getUniformLocation(program, 'lut'), 1);
    gl.uniform1f(gl.getUniformLocation(program, 'size'), lut.size);

    gl.activeTexture(gl.TEXTURE0);
    grade.video = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, grade.video);
    for (const [k, v] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR],
      [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) {
      gl.texParameteri(gl.TEXTURE_2D, k, v);
    }
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    gl.uniform1i(gl.getUniformLocation(program, 'picture'), 0);
    grade.crop = gl.getUniformLocation(program, 'crop');
    grade.gain = gl.getUniformLocation(program, 'gain');
    grade.gl = gl;
    grade.ready = true;
  } catch (error) {
    grade.problem = error.message || 'The colours couldn’t be corrected.';
  }
  return grade.ready;
}

/** Draw the current video frame, colour-corrected and framed as the export will. */
function drawGraded() {
  const el = $('footage'), canvas = $('graded'), gl = grade.gl;
  if (!gl || el.readyState < 2 || !el.videoWidth) return;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.round(canvas.clientWidth * ratio), h = Math.round(canvas.clientHeight * ratio);
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  gl.viewport(0, 0, w, h);
  gl.bindTexture(gl.TEXTURE_2D, grade.video);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, el);
  // The same part of the picture as the plain video (and the export).
  const box = footageLayout(framingOf(state.framedClip ?? -1));
  if (!box) return;
  const [sx, sy, sw, sh] = box.source;
  // The picture is uploaded upside down (FLIP_Y), so "top" is the far end.
  gl.uniform4f(grade.crop, sx, 1 - sy - sh, sw, sh);
  gl.uniform1f(grade.gain, brightnessGain());       // before the colour file, as exported
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  drawBackdrop(canvas);                             // now, while the corrected frame is there
  checkGradeWorks(gl, w, h);
}

/**
 * Some browsers (Safari with some HEVC video) hand WebGL blank frames. If the
 * corrected picture keeps coming out completely black while the video has a
 * picture, give up on correcting the preview and show the plain footage.
 */
function checkGradeWorks(gl, w, h) {
  if (grade.verified || grade.problem) return;
  const pixel = new Uint8Array(4);
  let lit = false;
  for (const fx of [0.25, 0.5, 0.75]) {
    for (const fy of [0.25, 0.5, 0.75]) {
      gl.readPixels(Math.floor(w * fx), Math.floor(h * fy), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
      if (pixel[0] + pixel[1] + pixel[2] > 0) lit = true;
    }
  }
  if (lit) { grade.verified = true; return; }
  grade.blank += 1;
  if (grade.blank >= 3) {                 // drawn only on new frames, so 3 is plenty
    grade.problem = 'This browser can’t show the D-Log colour fix live. The export still applies it.';
    grade.loop = null;
    show($('graded'), false);
    notify(grade.problem, 'warn');
  }
}

/** Redraw only when there's a new frame to show (or the size or framing changed). */
function gradeLoop(loop) {
  if (grade.loop !== loop) return;                   // a newer loop has taken over
  const el = $('footage'), canvas = $('graded');
  const key = `${el.currentTime}|${canvas.clientWidth}|${canvas.clientHeight}|${el.readyState}`
    + `|${JSON.stringify(framingOf(state.framedClip ?? -1))}|${brightnessGain()}`;
  if (key !== grade.drawn) { drawGraded(); grade.drawn = key; }
  requestAnimationFrame(() => gradeLoop(loop));
}

/** Correct the colours when the footage on show was filmed flat and it's wanted. */
let gradeRequest = 0;
async function updateGrade(at) {
  const request = ++gradeRequest;
  const wanted = Boolean(at && at.log && $('lut-on').checked);
  if (wanted && !(await loadGrade()) && grade.problem) notify(grade.problem, 'warn');
  if (request !== gradeRequest) return;              // a newer call has decided
  const on = wanted && grade.ready;
  show($('graded'), on);
  if (on && !grade.loop) {
    grade.loop = {};
    grade.drawn = '';
    const loop = grade.loop;
    requestAnimationFrame(() => gradeLoop(loop));
  }
  if (!on) grade.loop = null;
}

function wireLook() {
  for (const k of LOOK) {
    $(`look-${k}`).addEventListener('click', (e) => {
      const b = e.currentTarget;
      b.setAttribute('aria-pressed', String(b.getAttribute('aria-pressed') !== 'true'));
      save();
      applyLook();
    });
  }
  $('look-brightness').addEventListener('input', () => { save(); applyLook(); });
  $('look-brightness-label').addEventListener('click', () => {
    $('look-brightness').value = '0'; save(); applyLook();
  });
  $('lut-on').addEventListener('change', () => updateViewer());
}
