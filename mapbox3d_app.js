const DATA = __PAYLOAD__;
mapboxgl.accessToken = DATA.mapboxToken;
const W = DATA.water;
const $ = (id) => document.getElementById(id);
function b64f32(s) { return new Float32Array(Uint8Array.from(atob(s), c => c.charCodeAt(0)).buffer); }
const DEPTH = W ? b64f32(W.depthB64) : null;
const VX = (W && W.vxB64) ? b64f32(W.vxB64) : null;
const VY = (W && W.vyB64) ? b64f32(W.vyB64) : null;
const GROUND = (W && W.groundB64) ? b64f32(W.groundB64) : null;
const cellCount = W ? W.nrows * W.ncols : 0;
const nFrames = W ? W.nFrames : 0;
const HAS_ANIM = !!(W && nFrames > 1 && !W.colorStops);
const UP = W ? Math.max(1, Math.min(4, Math.floor(1600 / Math.max(W.ncols, W.nrows)))) : 1;
const dBox = DATA.bbox;
const centerLatRad = (dBox[1] + dBox[3]) / 2 * Math.PI / 180;
const cellX = W ? (W.cellsizeM || ((dBox[2] - dBox[0]) / W.ncols * 111320 * Math.cos(centerLatRad))) : 0;
const cellY = W ? (W.cellsizeM || ((dBox[3] - dBox[1]) / W.nrows * 110540)) : 0;
const cellArea = cellX * cellY;
if (W) document.body.classList.add('has-timeline');

const ui = { styleKey: 'terrain', buildingsOn: true, terrainOn: true, labelsOn: false, flowOn: !!VX, rainOn: true,
             mode: 'depth', exag: DATA.zExag, opacity: 0.92, dry: W ? W.dryThresh : 0.03, particles: 1800 };

// ---- risk colour logic (same Huizinga / DEFRA functions as before) ----
function huizingaDamageFraction(depth) {
  const d = DATA.huizingaBuiltUp.depths, f = DATA.huizingaBuiltUp.damage;
  if (depth <= d[0]) return f[0];
  if (depth >= d[d.length - 1]) return f[f.length - 1];
  for (let i = 1; i < d.length; i++) {
    if (depth <= d[i]) { const t = (depth - d[i - 1]) / (d[i] - d[i - 1]); return f[i - 1] + t * (f[i] - f[i - 1]); }
  }
  return f[f.length - 1];
}
function defraHazardFraction(hr) {
  const targets = [0, 0.30, 0.55, 0.75, 0.95];
  let lo = 0, loT = 0;
  for (let i = 0; i < DATA.riskBands.length; i++) {
    const hi = DATA.riskBands[i][1];
    const hiFinite = isFinite(hi) ? hi : lo * 2 + 1;
    if (hr <= hiFinite || !isFinite(hi)) {
      const t = hiFinite > lo ? (hr - lo) / (hiFinite - lo) : 0;
      return Math.min(1, Math.max(0, loT + Math.min(1, t) * (targets[i + 1] - loT)));
    }
    lo = hi; loT = targets[i + 1];
  }
  return targets[targets.length - 1];
}
const RISK_COLOR_STOPS = [[0.00, [0xf2, 0xd9, 0x4e]], [0.35, [0xf0, 0x88, 0x3e]], [0.9, [0xc8, 0x1e, 0x1e]]];
function riskColorFromFraction(frac) {
  frac = Math.min(1, Math.max(0, frac));
  for (let i = 1; i < RISK_COLOR_STOPS.length; i++) {
    const [f0, c0] = RISK_COLOR_STOPS[i - 1], [f1, c1] = RISK_COLOR_STOPS[i];
    if (frac <= f1 || i === RISK_COLOR_STOPS.length - 1) {
      const tt = Math.min(1, Math.max(0, f1 > f0 ? (frac - f0) / (f1 - f0) : 0));
      return '#' + [0, 1, 2].map(k => Math.round(c0[k] + (c1[k] - c0[k]) * tt).toString(16).padStart(2, '0')).join('');
    }
  }
  return '#f2d94e';
}
const BUILDING_DRY = '#e9e2d3', ROAD_DRY = 'rgba(0,0,0,0)';

// ---- rain rate (real uploaded series, else the solver's own triangular hyetograph) ----
function currentRainRateMmHr(simMinNow) {
  if (!W) return 0;
  const hr = simMinNow / 60.0, tms = W.rainTimesHr, rts = W.rainRatesMmHr;
  if (tms && rts && tms.length > 1) {
    if (hr <= tms[0]) return rts[0];
    if (hr >= tms[tms.length - 1]) return 0.0;
    let i = 0; while (i < tms.length - 1 && tms[i + 1] < hr) i++;
    const f = (tms[i + 1] - tms[i]) > 1e-9 ? (hr - tms[i]) / (tms[i + 1] - tms[i]) : 0;
    return rts[i] + f * (rts[i + 1] - rts[i]);
  }
  const peak = W.peakMmHr || 0, durHr = W.durationHr || 0;
  if (peak <= 0 || durHr <= 0 || hr >= durHr) return 0.0;
  const tPeak = durHr / 2.0;
  return hr <= tPeak ? peak * (hr / tPeak) : peak * (1 - (hr - tPeak) / (durHr - tPeak));
}
const totalSimMin = W ? Math.max(1, (nFrames - 1) * W.saveIntMin) : 0;
const LOOP_SECONDS = 9.0;
const nSeg = Math.max(1, nFrames - 1);

// ---- per-frame run statistics (flooded area, deepest water, volume) ----
let ST_AREA = null, ST_DEEP = null, ST_VOL = null, MAXD_ARR = null, VREF = 0.5;
if (HAS_ANIM || (W && !W.colorStops)) {
  ST_AREA = new Float32Array(nFrames); ST_DEEP = new Float32Array(nFrames); ST_VOL = new Float32Array(nFrames);
  MAXD_ARR = new Float32Array(cellCount);
  for (let f = 0; f < nFrames; f++) {
    const base = f * cellCount; let wet = 0, deep = 0, vol = 0;
    for (let i = 0; i < cellCount; i++) {
      const d = DEPTH[base + i];
      if (d > MAXD_ARR[i]) MAXD_ARR[i] = d;
      if (d > ui.dry) { wet++; vol += d; if (d > deep) deep = d; }
    }
    ST_AREA[f] = wet * cellArea / 1e4; ST_DEEP[f] = deep; ST_VOL[f] = vol * cellArea;
  }
  if (VX && VY) {
    const sp = [], step = Math.max(1, Math.floor(cellCount * nFrames / 200000));
    for (let k = 0; k < cellCount * nFrames; k += step) if (DEPTH[k] > ui.dry) sp.push(Math.hypot(VX[k], VY[k]));
    if (sp.length) { sp.sort((a, b) => a - b); VREF = Math.max(0.05, sp[Math.floor(sp.length * 0.98)]); }
  }
}
function lerpArr(arr, seg, mix) { const n = Math.min(seg + 1, arr.length - 1); return arr[seg] + (arr[n] - arr[seg]) * mix; }

// ---- GPU water surface (WebGL2): lit, flowing, depth-absorbing water ----
let glWater = null, floodCanvas = null, floodCtx = null, floodImgData = null;
function createGLWater() {
  if (!W || W.colorStops || !DEPTH) return null;
  const cv = document.createElement('canvas');
  cv.width = W.ncols * UP; cv.height = W.nrows * UP;
  const gl = cv.getContext('webgl2', {premultipliedAlpha: true, preserveDrawingBuffer: true, antialias: false});
  if (!gl) return null;
  const VS = `#version 300 es
in vec2 aPos; out vec2 vUv;
void main(){ vUv = vec2(aPos.x*0.5+0.5, 0.5-aPos.y*0.5); gl_Position = vec4(aPos,0.,1.); }`;
  const FS = `#version 300 es
precision highp float;
in vec2 vUv; out vec4 o;
uniform sampler2D uDA, uDB, uVA, uVB;
uniform float uMix, uTime, uMaxD, uDry, uVmax, uMode;
uniform vec2 uGrid;
float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7))) * 43758.5453); }
float vnoise(vec2 p){ vec2 i=floor(p), f=fract(p); f=f*f*(3.-2.*f);
  return mix(mix(hash(i),hash(i+vec2(1,0)),f.x), mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x), f.y); }
float fbm(vec2 p){ float s=0., a=.5; for(int k=0;k<3;k++){ s+=a*vnoise(p); p=p*2.03+vec2(1.7,9.2); a*=.5; } return s; }
float depthAt(vec2 uv){ float a=texture(uDA,uv).r, b=texture(uDB,uv).r; return mix(a*a,b*b,uMix)*uMaxD; }
vec2 velAt(vec2 uv){ vec2 a=texture(uVA,uv).rg*2.-1., b=texture(uVB,uv).rg*2.-1.; return mix(a,b,uMix)*uVmax; }
float ripples(vec2 q, vec2 dir, float ph){ return fbm(q*0.9 - dir*ph*2.2 + vec2(uTime*0.07, -uTime*0.05)); }
float field(vec2 q, vec2 dir){
  float p0=fract(uTime*0.22), p1=fract(uTime*0.22+0.5);
  float w=abs(p0-0.5)*2.;
  return mix(ripples(q,dir,p0), ripples(q+vec2(23.1,7.7),dir,p1), w);
}
void main(){
  float d = depthAt(vUv);
  float edge = smoothstep(uDry*0.35, uDry*2.0, d);
  if (edge <= 0.002) { o = vec4(0.); return; }
  vec2 v = velAt(vUv);
  float spd = length(v);
  if (uMode > 1.5) {
    float s = clamp(spd / uVmax, 0., 1.);
    vec3 c = s < 0.5 ? mix(vec3(0.20,0.45,0.95), vec3(0.95,0.78,0.30), s*2.) : mix(vec3(0.95,0.78,0.30), vec3(1.0,0.35,0.15), (s-0.5)*2.);
    float a = edge * 0.92; o = vec4(c*a, a); return;
  }
  vec2 dir = v / max(uVmax, 1e-3);
  vec2 q = vUv * uGrid;
  float e = 0.35;
  float h  = field(q, dir);
  float hx = field(q + vec2(e,0.), dir);
  float hy = field(q + vec2(0.,e), dir);
  float amp = 0.45 + 1.4 * clamp(spd / 1.5, 0., 1.);
  vec3 n = normalize(vec3(-(hx-h)/e*amp, -(hy-h)/e*amp, 1.0));
  vec3 L = normalize(vec3(-0.45, -0.35, 0.82));
  float diff = max(dot(n, L), 0.);
  float spec = pow(max(dot(reflect(-L, n), vec3(0.,0.,1.)), 0.), 70.);
  float absorb = 1. - exp(-d / 1.0);
  vec3 shallow = vec3(0.45, 0.74, 1.0), deep = vec3(0.04, 0.10, 0.55);
  vec3 col = mix(shallow, deep, absorb);
  col *= 0.78 + 0.34 * diff;
  col += spec * vec3(1.0, 0.97, 0.9) * 0.9;
  float shore = 1. - smoothstep(uDry*1.5, uDry*7., d);
  float foamN = fbm(q*2.6 - dir*uTime*0.9 + uTime*0.15);
  float foam = clamp(shore*smoothstep(0.4,0.75,foamN), 0., 1.);
  col = mix(col, vec3(0.95, 0.98, 1.0), foam * 0.6);
  float alpha = edge * mix(0.74, 0.96, absorb);
  o = vec4(col * alpha, alpha);
}`;
  function sh(type, src) {
    const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) { console.warn('water shader:', gl.getShaderInfoLog(s)); return null; }
    return s;
  }
  const vs = sh(gl.VERTEX_SHADER, VS), fs = sh(gl.FRAGMENT_SHADER, FS);
  if (!vs || !fs) return null;
  const prog = gl.createProgram(); gl.attachShader(prog, vs); gl.attachShader(prog, fs); gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) { console.warn('water link:', gl.getProgramInfoLog(prog)); return null; }
  gl.useProgram(prog);
  const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, 1,1]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(prog, 'aPos');
  gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  const U = n => gl.getUniformLocation(prog, n);
  const nc = W.ncols, nr = W.nrows, maxD = Math.max(W.maxDepth, 0.05);
  function makeTex(unit, fmt, internal) {
    const t = gl.createTexture(); gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, internal, nc, nr, 0, fmt, gl.UNSIGNED_BYTE, null);
    return t;
  }
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
  const tex = [makeTex(0, gl.RED, gl.R8), makeTex(1, gl.RED, gl.R8), makeTex(2, gl.RG, gl.RG8), makeTex(3, gl.RG, gl.RG8)];
  gl.uniform1i(U('uDA'), 0); gl.uniform1i(U('uDB'), 1); gl.uniform1i(U('uVA'), 2); gl.uniform1i(U('uVB'), 3);
  gl.uniform1f(U('uMaxD'), maxD); gl.uniform1f(U('uVmax'), VREF); gl.uniform2f(U('uGrid'), nc, nr);
  const dBytes = new Uint8Array(cellCount), vBytes = new Uint8Array(cellCount * 2);
  let modeMax = false;
  function upload(unitD, unitV, frame) {
    const base = modeMax ? 0 : frame * cellCount, src = modeMax ? MAXD_ARR : DEPTH;
    for (let i = 0; i < cellCount; i++) dBytes[i] = Math.round(Math.sqrt(Math.min(1, Math.max(0, src[base + i]) / maxD)) * 255);
    gl.activeTexture(gl.TEXTURE0 + unitD); gl.bindTexture(gl.TEXTURE_2D, tex[unitD]);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, nc, nr, gl.RED, gl.UNSIGNED_BYTE, dBytes);
    for (let i = 0; i < cellCount; i++) {
      const vx = (VX && !modeMax) ? VX[base + i] : 0, vy = (VY && !modeMax) ? VY[base + i] : 0;
      vBytes[2*i]   = Math.round(Math.max(0, Math.min(1, 0.5 + 0.5 * vx / VREF)) * 255);
      vBytes[2*i+1] = Math.round(Math.max(0, Math.min(1, 0.5 + 0.5 * vy / VREF)) * 255);
    }
    gl.activeTexture(gl.TEXTURE0 + unitV); gl.bindTexture(gl.TEXTURE_2D, tex[unitV]);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, nc, nr, gl.RG, gl.UNSIGNED_BYTE, vBytes);
  }
  let segA = -1, segB = -1;
  gl.viewport(0, 0, cv.width, cv.height); gl.clearColor(0, 0, 0, 0);
  return {
    canvas: cv,
    setMode(m) { modeMax = (m === 'max'); segA = segB = -1; gl.uniform1f(U('uMode'), m === 'speed' ? 2 : 0); },
    draw(seg, mixFrac) {
      const segNext = Math.min(seg + 1, nFrames - 1);
      if (seg !== segA) { upload(0, 2, seg); segA = seg; }
      if (segNext !== segB) { upload(1, 3, segNext); segB = segNext; }
      gl.uniform1f(U('uDry'), ui.dry); gl.uniform1f(U('uMix'), modeMax ? 0 : mixFrac);
      gl.uniform1f(U('uTime'), performance.now() / 1000);
      gl.clear(gl.COLOR_BUFFER_BIT); gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    },
  };
}
if (W) {
  try { glWater = createGLWater(); } catch (e) { console.warn('GPU water unavailable, using CPU overlay:', e); glWater = null; }
  if (glWater) { floodCanvas = glWater.canvas; }
  else {
    floodCanvas = document.createElement('canvas'); floodCanvas.width = W.ncols; floodCanvas.height = W.nrows;
    floodCtx = floodCanvas.getContext('2d'); floodImgData = floodCtx.createImageData(W.ncols, W.nrows);
  }
}

// ---- flow lines (advected particles, brighter where faster) ----
let flowCanvas = null, flowCtx = null, particles = [];
let wetCellIdx = [];
if (DEPTH && cellCount && VX) {
  const everWet = new Uint8Array(cellCount);
  for (let f = 0; f < nFrames; f++) { const base = f * cellCount; for (let i = 0; i < cellCount; i++) if (!everWet[i] && DEPTH[base + i] > ui.dry) everWet[i] = 1; }
  for (let i = 0; i < cellCount; i++) if (everWet[i]) wetCellIdx.push(i);
}
function spawnParticlePosition() {
  if (wetCellIdx.length > 0) { const i = wetCellIdx[(Math.random() * wetCellIdx.length) | 0]; return {x: (i % W.ncols) + Math.random(), y: ((i / W.ncols) | 0) + Math.random()}; }
  return {x: Math.random() * W.ncols, y: Math.random() * W.nrows};
}
function rebuildParticles() {
  particles = new Array(ui.particles).fill(0).map(() => { const p = spawnParticlePosition(); return {x: p.x, y: p.y, age: Math.random() * 60, dry: 0, hasPrev: false}; });
}
if (VX && VY && W) {
  flowCanvas = document.createElement('canvas'); flowCanvas.width = W.ncols * UP; flowCanvas.height = W.nrows * UP;
  flowCtx = flowCanvas.getContext('2d'); rebuildParticles();
}
function updateAndDrawFlow(seg, mixFrac, simDtSeconds) {
  if (!flowCanvas || !ui.flowOn || ui.mode === 'max') return;
  const dt = Math.min(600, simDtSeconds), segNext = Math.min(seg + 1, nFrames - 1), baseA = seg * cellCount, baseB = segNext * cellCount;
  flowCtx.globalCompositeOperation = 'destination-out'; flowCtx.fillStyle = 'rgba(0,0,0,0.10)';
  flowCtx.fillRect(0, 0, flowCanvas.width, flowCanvas.height);
  flowCtx.globalCompositeOperation = 'source-over'; flowCtx.lineWidth = Math.max(1.2, UP * 0.55); flowCtx.lineCap = 'round';
  for (const p of particles) {
    const row = Math.min(W.nrows - 1, Math.max(0, Math.floor(p.y))), col = Math.min(W.ncols - 1, Math.max(0, Math.floor(p.x)));
    const idx = row * W.ncols + col;
    const depth = Math.max(0, DEPTH[baseA + idx] + (DEPTH[baseB + idx] - DEPTH[baseA + idx]) * mixFrac);
    p.age += 1;
    if (depth <= ui.dry) {
      p.dry += 1;
      if (p.dry > 40 || p.age > 500) { const q = spawnParticlePosition(); p.x = q.x; p.y = q.y; p.age = 0; p.dry = 0; p.hasPrev = false; }
      continue;
    }
    p.dry = 0;
    const vx = VX[baseA + idx] + (VX[baseB + idx] - VX[baseA + idx]) * mixFrac, vy = VY[baseA + idx] + (VY[baseB + idx] - VY[baseA + idx]) * mixFrac;
    let dx = (vx * dt) / cellX, dy = (vy * dt) / cellY;
    const stepMag = Math.hypot(dx, dy);
    if (stepMag > 1.5) { const s = 1.5 / stepMag; dx *= s; dy *= s; }
    const px = p.x, py = p.y; p.x += dx; p.y += dy;
    if (p.x < 0 || p.x >= W.ncols || p.y < 0 || p.y >= W.nrows) { const q = spawnParticlePosition(); p.x = q.x; p.y = q.y; p.age = 0; p.dry = 0; p.hasPrev = false; continue; }
    if (p.hasPrev) {
      const s = Math.min(1, Math.hypot(vx, vy) / VREF);
      flowCtx.strokeStyle = 'rgba(255,255,255,' + (0.30 + 0.65 * s).toFixed(2) + ')';
      flowCtx.beginPath(); flowCtx.moveTo(px * UP, py * UP); flowCtx.lineTo(p.x * UP, p.y * UP); flowCtx.stroke();
    }
    p.hasPrev = true;
  }
}

// ---- CPU fallback overlay (custom colour ramps: damage / NbS / diff, or no WebGL2) ----
function colorFromStops(t, stops) {
  for (let i = 1; i < stops.length; i++) {
    const [f0, c0] = stops[i - 1], [f1, c1] = stops[i];
    if (t <= f1 || i === stops.length - 1) {
      const tt = f1 > f0 ? Math.min(1, Math.max(0, (t - f0) / (f1 - f0))) : 0;
      return [Math.round(c0[0]+(c1[0]-c0[0])*tt), Math.round(c0[1]+(c1[1]-c0[1])*tt), Math.round(c0[2]+(c1[2]-c0[2])*tt)];
    }
  }
  return stops[stops.length - 1][1];
}
function setFloodFrameCPU(seg, mixFrac) {
  const maxDepth = Math.max(W.maxDepth, 0.05), segNext = Math.min(seg + 1, nFrames - 1);
  const baseA = seg * cellCount, baseB = segNext * cellCount, data = floodImgData.data, ncols = W.ncols, nrows = W.nrows;
  let wetCount = 0;
  const depthFrame = setFloodFrameCPU._buf || (setFloodFrameCPU._buf = new Float32Array(cellCount));
  for (let i = 0; i < cellCount; i++) {
    const d = Math.max(0, DEPTH[baseA + i] + (DEPTH[baseB + i] - DEPTH[baseA + i]) * mixFrac);
    depthFrame[i] = d; if (d > ui.dry) wetCount++;
  }
  const sparse = wetCount > 0 && wetCount / cellCount < 0.03;
  const near = sparse ? new Uint8Array(cellCount) : null;
  if (sparse) for (let i = 0; i < cellCount; i++) {
    if (depthFrame[i] <= ui.dry) continue;
    near[i] = 2; const row = (i / ncols) | 0, col = i % ncols;
    for (let dr = -1; dr <= 1; dr++) for (let dc = -1; dc <= 1; dc++) {
      if (!dr && !dc) continue; const r2 = row + dr, c2 = col + dc;
      if (r2 < 0 || r2 >= nrows || c2 < 0 || c2 >= ncols) continue;
      const j = r2 * ncols + c2; if (near[j] === 0) near[j] = 1;
    }
  }
  for (let i = 0; i < cellCount; i++) {
    const idx = i * 4, d = depthFrame[i], dil = sparse && near[i] === 1;
    if (d <= ui.dry && !dil) { data[idx+3] = 0; continue; }
    const t = Math.min(1, Math.max(d, dil ? ui.dry * 1.5 : d) / maxDepth), aS = dil ? 0.55 : 1.0;
    if (W.colorStops) {
      const c = colorFromStops(t, W.colorStops); data[idx] = c[0]; data[idx+1] = c[1]; data[idx+2] = c[2]; data[idx+3] = Math.round((160 + 90 * t) * aS);
    } else {
      data[idx] = Math.round(120 - 100 * t); data[idx+1] = Math.round(190 - 140 * t); data[idx+2] = 255; data[idx+3] = Math.round((150 + 100 * t) * aS);
    }
  }
  floodCtx.putImageData(floodImgData, 0, 0);
}
function setFloodFrame(seg, mixFrac) { if (glWater) glWater.draw(seg, mixFrac); else if (floodCtx) setFloodFrameCPU(seg, mixFrac); }

// ---- map ----
const STYLE_URLS = {dark: 'mapbox://styles/mapbox/dark-v11', light: 'mapbox://styles/mapbox/light-v11',
  satellite: 'mapbox://styles/mapbox/satellite-streets-v12', streets: 'mapbox://styles/mapbox/streets-v12',
  terrain: 'mapbox://styles/mapbox/outdoors-v12', night: 'mapbox://styles/mapbox/navigation-night-v1'};
const DARK_STYLES = ['dark', 'night', 'satellite'];
const map = new mapboxgl.Map({container: 'map', style: STYLE_URLS.terrain, center: DATA.center, zoom: 14, pitch: 60, bearing: -17,
  antialias: true, preserveDrawingBuffer: true, maxPitch: 75});
map.addControl(new mapboxgl.NavigationControl({visualizePitch: true}), 'top-right');
function resetView() { const b = DATA.bbox; map.fitBounds([[b[0], b[1]], [b[2], b[3]]], {padding: {top: 80, bottom: 220, left: 340, right: 80}, pitch: 60, bearing: -17, duration: 0}); }
function applyLabels() {
  const st = map.getStyle(); if (!st) return;
  for (const l of st.layers) if (l.type === 'symbol') { try { map.setLayoutProperty(l.id, 'visibility', ui.labelsOn ? 'visible' : 'none'); } catch (e) {} }
}
function applyWaterPaint() {
  if (!map.getLayer('flood-layer')) return;
  map.setPaintProperty('flood-layer', 'raster-opacity', ui.opacity * (ui.mode === 'paths' ? 0.45 : 1));
}
function applyFlowVisibility() {
  if (map.getLayer('flow-layer')) map.setLayoutProperty('flow-layer', 'visibility', (ui.flowOn || ui.mode === 'paths') && ui.mode !== 'max' ? 'visible' : 'none');
}
function addSceneSources() {
  if (map.getSource('mapbox-dem')) return;  // already (re)added for this style
  map.addSource('mapbox-dem', {type: 'raster-dem', url: 'mapbox://mapbox.mapbox-terrain-dem-v1', tileSize: 512, maxzoom: 14});
  if (ui.terrainOn) map.setTerrain({source: 'mapbox-dem', exaggeration: ui.exag});
  map.addLayer({id: 'sky', type: 'sky', paint: {'sky-type': 'atmosphere', 'sky-atmosphere-sun-intensity': 8}});
  if (DATA.aoiGeom) {
    const ring = DATA.aoiGeom.type === 'Polygon' ? DATA.aoiGeom.coordinates[0] : DATA.aoiGeom.coordinates[0][0];
    const lons = ring.map(p => p[0]), lats = ring.map(p => p[1]);
    const padLon = Math.max((Math.max(...lons) - Math.min(...lons)) * 6, 0.5), padLat = Math.max((Math.max(...lats) - Math.min(...lats)) * 6, 0.5);
    const outer = [[Math.min(...lons)-padLon, Math.min(...lats)-padLat], [Math.max(...lons)+padLon, Math.min(...lats)-padLat],
      [Math.max(...lons)+padLon, Math.max(...lats)+padLat], [Math.min(...lons)-padLon, Math.max(...lats)+padLat], [Math.min(...lons)-padLon, Math.min(...lats)-padLat]];
    map.addSource('aoi-mask', {type: 'geojson', data: {type: 'Feature', geometry: {type: 'Polygon', coordinates: [outer, ring.slice().reverse()]}}});
    map.addLayer({id: 'aoi-mask-layer', type: 'fill', source: 'aoi-mask', paint: {'fill-color': '#04080a', 'fill-opacity': DARK_STYLES.includes(ui.styleKey) ? 0.5 : 0.22}});
  }
  if (W) {
    const b = DATA.bbox, coords = [[b[0], b[3]], [b[2], b[3]], [b[2], b[1]], [b[0], b[1]]];
    map.addSource('flood-canvas', {type: 'canvas', canvas: floodCanvas, coordinates: coords, animate: true});
    map.addLayer({id: 'flood-layer', type: 'raster', source: 'flood-canvas', paint: {'raster-opacity': ui.opacity, 'raster-fade-duration': 0}});
    if (flowCanvas) {
      map.addSource('flow-canvas', {type: 'canvas', canvas: flowCanvas, coordinates: coords, animate: true});
      map.addLayer({id: 'flow-layer', type: 'raster', source: 'flow-canvas', paint: {'raster-opacity': 0.95, 'raster-fade-duration': 0}});
    }
  }
  if (DATA.buildings.features.length) {
    map.addSource('buildings', {type: 'geojson', data: DATA.buildings});
    map.addLayer({id: 'buildings-layer', type: 'fill-extrusion', source: 'buildings', layout: {visibility: ui.buildingsOn ? 'visible' : 'none'}, paint: {
      'fill-extrusion-color': ['coalesce', ['feature-state', 'color'], BUILDING_DRY],
      'fill-extrusion-height': ['*', ['get', 'height_m'], 2], 'fill-extrusion-base': 0,
      'fill-extrusion-opacity': 0.96, 'fill-extrusion-vertical-gradient': true, 'fill-extrusion-ambient-occlusion-intensity': 0.55,
      'fill-extrusion-ambient-occlusion-radius': 6}});
  }
  if (DATA.roads.features.length) {
    map.addSource('roads', {type: 'geojson', data: DATA.roads});
    map.addLayer({id: 'roads-layer', type: 'line', source: 'roads', layout: {'line-join': 'round', 'line-cap': 'round'},
      paint: {'line-color': ['coalesce', ['feature-state', 'color'], ROAD_DRY], 'line-width': 3, 'line-opacity': 0.95}});
  }
  applyLabels(); applyWaterPaint(); applyFlowVisibility();
  bColorCache.fill(''); rColorCache.fill('');
}
const bColorCache = new Array(DATA.buildings.features.length).fill('');
const rColorCache = new Array(DATA.roads.features.length).fill('');

let styleSwapInProgress = false;
function setMapStyle(key) {
  if (key === ui.styleKey || styleSwapInProgress) return;
  styleSwapInProgress = true; ui.styleKey = key;
  document.querySelectorAll('.tile').forEach(t => t.classList.toggle('active', t.dataset.style === key));
  // A style swap drops every custom source/layer. isStyleLoaded() can still be true for the OLD style while the new
  // one is being fetched, so wait for the new style's own load event first (a style not fetched before takes longer
  // than any fixed delay), then for the map to be ready, and never leave the swap flag stuck if re-adding throws.
  let newStyleLoaded = false;
  map.once('style.load', () => { newStyleLoaded = true; });
  map.setStyle(STYLE_URLS[key]);
  let tries = 0;
  const tryAdd = () => {
    if (!(newStyleLoaded && map.isStyleLoaded()) && tries++ < 300) { setTimeout(tryAdd, 100); return; }
    try { addSceneSources(); } catch (e) { console.warn('re-adding scene after style swap:', e); if (tries < 300) { tries++; setTimeout(tryAdd, 150); return; } }
    styleSwapInProgress = false;
  };
  setTimeout(tryAdd, 80);
}
document.querySelectorAll('.tile').forEach(t => t.addEventListener('click', () => setMapStyle(t.dataset.style)));

// ---- panel wiring ----
$('resetBtn').addEventListener('click', resetView);
$('collapseBtn').addEventListener('click', () => {
  const c = $('panel').classList.toggle('collapsed'); document.body.classList.toggle('panel-collapsed', c);
  $('collapseBtn').innerHTML = c ? '&#8964;' : '&#8963;';
});
$('fsBtn').addEventListener('click', () => {
  const el = document.documentElement;
  if (!document.fullscreenElement) (el.requestFullscreen || el.webkitRequestFullscreen || function () {}).call(el);
  else (document.exitFullscreen || document.webkitExitFullscreen || function () {}).call(document);
});
$('pillBuildings').addEventListener('click', (e) => {
  ui.buildingsOn = !ui.buildingsOn; e.currentTarget.classList.toggle('on', ui.buildingsOn);
  if (map.getLayer('buildings-layer')) map.setLayoutProperty('buildings-layer', 'visibility', ui.buildingsOn ? 'visible' : 'none');
});
function setTerrain() {
  if (ui.terrainOn) map.setTerrain({source: 'mapbox-dem', exaggeration: ui.exag}); else map.setTerrain(null);
  $('exagVal').textContent = ui.exag.toFixed(1); $('exagOut').textContent = ui.exag.toFixed(1) + '×';
}
$('pillTerrain').addEventListener('click', (e) => { ui.terrainOn = !ui.terrainOn; e.currentTarget.classList.toggle('on', ui.terrainOn); if (map.getSource('mapbox-dem')) setTerrain(); });
$('exagSlider').addEventListener('input', (e) => { ui.exag = parseFloat(e.target.value); if (map.getSource('mapbox-dem')) setTerrain(); else $('exagOut').textContent = ui.exag.toFixed(1) + '×'; });
$('exagOut').textContent = ui.exag.toFixed(1) + '×';
$('pillLabels').addEventListener('click', (e) => { ui.labelsOn = !ui.labelsOn; e.currentTarget.classList.toggle('on', ui.labelsOn); applyLabels(); });
function fmtDepth(d) { return d < 1 ? (d * 100).toFixed(0) + ' cm' : d.toFixed(2) + ' m'; }
function fmtMin(min) { const r = Math.round(min), h = Math.floor(r / 60), m = r % 60; return h + ':' + String(m).padStart(2, '0'); }
const startTimeMs = (W && W.startTimeIso) ? new Date(W.startTimeIso).getTime() : null;

if (!W || W.colorStops) { $('modeSection').style.display = 'none'; document.querySelectorAll('[data-water]').forEach(e => e.style.display = 'none'); $('pillFlow').style.display = 'none'; }
if (W && !VX) { $('cardSpeed').style.display = 'none'; $('cardPaths').style.display = 'none'; $('pillFlow').style.display = 'none'; }
$('panelSub').textContent = W ? ((cellCount / 1000).toFixed(0) + 'k cells' + (cellX ? ' · ' + cellX.toFixed(0) + ' m grid' : '') + (W.durationHr ? ' · ' + W.durationHr + ' h storm' : '')) : 'Terrain view';
$('timelineBar').style.display = 'block';
if (!HAS_ANIM) {
  ['chartWrap', '.tl-bot', '#playPauseBtn', '.big', '.mut', '[data-flood]', '#rainBtn'].forEach(sel => {
    document.querySelectorAll(sel.startsWith('.') || sel.startsWith('[') || sel.startsWith('#') ? sel : '#' + sel).forEach(e => e.style.display = 'none'); });
  $('timeBig').parentElement.style.display = 'none';
}
if (!HAS_ANIM) $('exportVideoBtn3d').style.display = 'none';
function setTlOffset() { document.documentElement.style.setProperty('--tl-h', ($('timelineBar').offsetHeight + 22) + 'px'); }
const ctlStyle = document.createElement('style'); ctlStyle.textContent = '.mapboxgl-ctrl-bottom-right,.mapboxgl-ctrl-bottom-left{bottom:var(--tl-h,0px)}'; document.head.appendChild(ctlStyle);
setTlOffset(); window.addEventListener('resize', setTlOffset);

if (W && !W.colorStops) {
  const maxD = Math.max(W.maxDepth, 0.05);
  function renderLegend() {
    const bar = $('legendBar'), lbl = $('legendLbl');
    if (ui.mode === 'speed') {
      bar.style.background = 'linear-gradient(90deg,#3373f2,#f2c74d 50%,#ff5926)';
      lbl.innerHTML = ['0', (VREF / 2).toFixed(1), VREF.toFixed(1) + '+ m/s'].map(s => '<span>' + s + '</span>').join('');
      $('legendNote').textContent = 'Water speed from the solver’s own velocity output';
    } else {
      bar.style.background = 'linear-gradient(90deg,#73bdff,#4c9dff 35%,#1f48c9 70%,#0a1a78)';
      const f = [0, 0.1, 0.3, 0.6, 1].map(x => x === 0 ? ui.dry : maxD * x);
      lbl.innerHTML = f.map((v, i) => '<span>' + (i === 4 ? fmtDepth(v) : fmtDepth(v)) + '</span>').join('');
      $('legendNote').textContent = ui.mode === 'max' ? 'Deepest water reached at each point during the event' : 'Flow lines, brighter where faster';
    }
  }
  function setMode(m) {
    ui.mode = m; document.querySelectorAll('.card').forEach(c => c.classList.toggle('active', c.dataset.mode === m));
    if (glWater) glWater.setMode(m === 'paths' ? 'depth' : m);
    applyWaterPaint(); applyFlowVisibility(); renderLegend();
  }
  document.querySelectorAll('.card').forEach(c => c.addEventListener('click', () => setMode(c.dataset.mode)));
  renderLegend();
  $('pillFlow').addEventListener('click', (e) => { ui.flowOn = !ui.flowOn; e.currentTarget.classList.toggle('on', ui.flowOn); applyFlowVisibility(); });
  $('pillFlow').classList.toggle('on', ui.flowOn);
  $('cutSlider').addEventListener('input', (e) => { ui.dry = parseInt(e.target.value) / 100; $('cutOut').textContent = e.target.value + ' cm'; });
  $('cutSlider').value = Math.round(ui.dry * 100); $('cutOut').textContent = Math.round(ui.dry * 100) + ' cm';
  $('cutSlider').max = 30; if (parseInt($('cutSlider').value) < 1) $('cutSlider').value = 1;
  $('opSlider').addEventListener('input', (e) => { ui.opacity = parseInt(e.target.value) / 100; $('opOut').textContent = e.target.value + '%'; applyWaterPaint(); });
  $('opOut').textContent = '92%';
  $('partSlider').addEventListener('input', (e) => { ui.particles = parseInt(e.target.value); $('partOut').textContent = e.target.value; if (flowCanvas) rebuildParticles(); });
  $('partOut').textContent = '1800';
  if (!VX) document.querySelectorAll('.row')[3].style.display = 'none';
  $('fineSummary').textContent = Math.round(ui.dry * 100) + ' cm cutoff · 92% opacity';
}

// ---- export ----
$('exportToggle').addEventListener('click', (e) => { e.stopPropagation(); $('exportMenu').classList.toggle('open'); });
document.addEventListener('click', () => $('exportMenu').classList.remove('open'));
$('exportBtn').addEventListener('click', () => {
  resetView();
  setTimeout(() => { const a = document.createElement('a'); a.href = map.getCanvas().toDataURL('image/png'); a.download = 'flood_3d.png'; a.click(); }, 400);
});

// ---- animation state ----
let elapsedSeconds = 0, playing = true, speed = 1.0, lastNow = null, dragging = false;
let pin = null, pinSeries = null, pinPeak = 0, pinMarker = null;

function updateBuildingColors(seg, mixFrac) {
  if (!map.getSource('buildings')) return;
  const feats = DATA.buildings.features;
  for (let k = 0; k < feats.length; k++) {
    const f = feats[k], dp = f.properties.depths;
    let d = 0; if (dp) { d = lerpArr(dp, Math.min(seg, dp.length - 1), mixFrac); if (d < 0) d = 0; }
    const col = d > 0.03 ? riskColorFromFraction(Math.max(0.05, huizingaDamageFraction(d))) : BUILDING_DRY;
    if (bColorCache[k] !== col) { bColorCache[k] = col; map.setFeatureState({source: 'buildings', id: f.id}, {color: col}); }
  }
}
function updateRoadColors(seg, mixFrac) {
  if (!map.getSource('roads')) return;
  const feats = DATA.roads.features;
  for (let k = 0; k < feats.length; k++) {
    const f = feats[k], dp = f.properties.depths, sp = f.properties.speeds;
    let d = 0, s = 0;
    if (dp) { d = Math.max(0, lerpArr(dp, Math.min(seg, dp.length - 1), mixFrac)); }
    if (sp) { s = Math.max(0, lerpArr(sp, Math.min(seg, sp.length - 1), mixFrac)); }
    const col = d > 0.03 ? riskColorFromFraction(Math.max(0.05, defraHazardFraction(d * (s + 1.5)))) : ROAD_DRY;
    if (rColorCache[k] !== col) { rColorCache[k] = col; map.setFeatureState({source: 'roads', id: f.id}, {color: col}); }
  }
}

// ---- timeline card: stats + chart ----
const chart = $('chart'), chartTip = $('chartTip');
const NBINS = HAS_ANIM ? Math.min(60, Math.max(8, (nFrames - 1) * 2)) : 0;
let rainBins = null, rainBinMax = 1, areaMax = 1;
if (HAS_ANIM) {
  rainBins = []; for (let i = 0; i < NBINS; i++) rainBins.push(currentRainRateMmHr((i + 0.5) / NBINS * totalSimMin));
  rainBinMax = Math.max(1e-6, ...rainBins); for (let i = 0; i < nFrames; i++) areaMax = Math.max(areaMax, ST_AREA[i]);
}
function niceMax(v) { const p = Math.pow(10, Math.floor(Math.log10(Math.max(v, 1e-9)))), m = v / p; return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p; }
function drawChart(t) {
  if (!HAS_ANIM) return;
  const dpr = window.devicePixelRatio || 1, cw = chart.clientWidth, ch = chart.clientHeight;
  if (chart.width !== Math.round(cw * dpr)) { chart.width = Math.round(cw * dpr); chart.height = Math.round(ch * dpr); }
  const c = chart.getContext('2d'); c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, cw, ch);
  const x0 = 4, x1 = cw - 6, X = (u) => x0 + u * (x1 - x0);
  const hy0 = 14, hy1 = 42, ay0 = 60, ay1 = 92;
  c.font = '10px "Public Sans",system-ui,sans-serif'; c.fillStyle = 'rgba(190,205,214,0.85)'; c.textBaseline = 'alphabetic';
  const rm = niceMax(rainBinMax), am = niceMax(areaMax);
  c.fillText('Rainfall  ' + (rm >= 10 ? rm.toFixed(0) : rm.toFixed(1)) + ' mm/h', x0, 10);
  c.fillText('Flooded area  ' + (am >= 10 ? am.toFixed(0) : am.toFixed(1)) + ' ha', x0, 56);
  c.strokeStyle = 'rgba(255,255,255,0.08)'; c.lineWidth = 1;
  [hy0, hy1, ay0, ay1].forEach(y => { c.beginPath(); c.moveTo(x0, y + 0.5); c.lineTo(x1, y + 0.5); c.stroke(); });
  const bw = (x1 - x0) / NBINS;
  c.fillStyle = 'rgba(76,157,255,0.75)';
  for (let i = 0; i < NBINS; i++) { const h = (rainBins[i] / rm) * (hy1 - hy0); if (h > 0.2) c.fillRect(x0 + i * bw + 0.5, hy0 + 0, bw - 1, h); }
  c.beginPath();
  for (let f = 0; f < nFrames; f++) { const px = X(f / (nFrames - 1)), py = ay1 - (ST_AREA[f] / am) * (ay1 - ay0); f ? c.lineTo(px, py) : c.moveTo(px, py); }
  c.strokeStyle = '#4c9dff'; c.lineWidth = 1.8; c.stroke();
  c.lineTo(X(1), ay1); c.lineTo(X(0), ay1); c.closePath(); c.fillStyle = 'rgba(76,157,255,0.16)'; c.fill();
  c.fillStyle = 'rgba(190,205,214,0.8)';
  const nt = 6; for (let i = 0; i <= nt; i++) { const m = totalSimMin * i / nt, lab = fmtMin(m), tw = c.measureText(lab).width; c.fillText(lab, Math.min(Math.max(X(i / nt) - tw / 2, 0), cw - tw), 107); }
  const px = X(t); c.strokeStyle = 'rgba(255,255,255,0.9)'; c.lineWidth = 1.5; c.beginPath(); c.moveTo(px, hy0 - 4); c.lineTo(px, ay1 + 2); c.stroke();
  const segF = t * nSeg, seg = Math.min(nSeg - 1, Math.floor(segF)), mx = segF - seg;
  const ay = ay1 - (lerpArr(ST_AREA, seg, mx) / am) * (ay1 - ay0);
  c.fillStyle = '#fff'; c.beginPath(); c.arc(px, ay, 3.5, 0, 7); c.fill(); c.strokeStyle = '#4c9dff'; c.lineWidth = 2; c.stroke();
}
function chartFrac(ev) { const r = chart.getBoundingClientRect(); return Math.min(1, Math.max(0, (ev.clientX - r.left - 4) / (r.width - 10))); }
let chartDrag = false;
chart.addEventListener('pointerdown', (ev) => { if (!HAS_ANIM) return; chartDrag = true; dragging = true; chart.setPointerCapture(ev.pointerId); elapsedSeconds = chartFrac(ev) * LOOP_SECONDS; });
chart.addEventListener('pointermove', (ev) => {
  if (!HAS_ANIM) return; const u = chartFrac(ev);
  if (chartDrag) elapsedSeconds = u * LOOP_SECONDS;
  const segF = u * nSeg, seg = Math.min(nSeg - 1, Math.floor(segF)), mx = segF - seg, simMin = u * totalSimMin;
  const rate = currentRainRateMmHr(simMin), area = lerpArr(ST_AREA, seg, mx), vol = lerpArr(ST_VOL, seg, mx);
  chartTip.innerHTML = '<b>' + (startTimeMs !== null ? new Date(startTimeMs + simMin * 60000).toLocaleString(undefined, {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}) : fmtMin(simMin)) + '</b><br>' +
    rate.toFixed(1) + ' mm/h rainfall<br>' + area.toFixed(area < 10 ? 1 : 0) + ' ha flooded<br>' + Math.round(vol).toLocaleString() + ' m³ on the surface';
  chartTip.style.display = 'block';
  const tw = chartTip.offsetWidth, r = chart.getBoundingClientRect();
  chartTip.style.left = Math.min(Math.max((ev.clientX - r.left) - tw / 2, 0), r.width - tw) + 'px'; chartTip.style.top = (-chartTip.offsetHeight - 6) + 'px';
});
chart.addEventListener('pointerleave', () => { chartTip.style.display = 'none'; });
const endChartDrag = () => { if (chartDrag) { chartDrag = false; dragging = false; lastNow = null; } };
chart.addEventListener('pointerup', endChartDrag); chart.addEventListener('pointercancel', endChartDrag);

// ---- click-to-pin probe ----
function pinAt(lng, lat) {
  const b = DATA.bbox, col = Math.floor((lng - b[0]) / (b[2] - b[0]) * W.ncols), row = Math.floor((b[3] - lat) / (b[3] - b[1]) * W.nrows);
  if (col < 0 || row < 0 || col >= W.ncols || row >= W.nrows) return;
  pin = {col, row, idx: row * W.ncols + col};
  pinSeries = new Float32Array(nFrames); pinPeak = 0;
  for (let f = 0; f < nFrames; f++) { pinSeries[f] = Math.max(0, DEPTH[f * cellCount + pin.idx]); pinPeak = Math.max(pinPeak, pinSeries[f]); }
  if (!pinMarker) { const el = document.createElement('div'); el.className = 'pin'; el.textContent = 'A'; pinMarker = new mapboxgl.Marker({element: el}); }
  pinMarker.setLngLat([lng, lat]).addTo(map); $('probe').style.display = 'block';
  const g = GROUND ? GROUND[pin.idx] : NaN; $('probeGround').textContent = isFinite(g) ? g.toFixed(1) + ' m' : '-';
  $('probePeak').textContent = fmtDepth(pinPeak);
}
$('probeClose').addEventListener('click', () => { pin = null; $('probe').style.display = 'none'; if (pinMarker) pinMarker.remove(); });
function updateProbe(t, seg, mx, simMin) {
  if (!pin) return;
  const d = lerpArr(pinSeries, seg, mx), n = Math.min(seg + 1, nFrames - 1);
  $('probeDepth').textContent = d > ui.dry ? fmtDepth(d) : 'Dry';
  $('probeWhen').textContent = (d > ui.dry ? 'deep at ' : 'at ') + (startTimeMs !== null ? new Date(startTimeMs + simMin * 60000).toLocaleTimeString(undefined, {hour: 'numeric', minute: '2-digit'}) : fmtMin(simMin));
  if (VX) {
    const a = seg * cellCount + pin.idx, b2 = n * cellCount + pin.idx;
    const vx = VX[a] + (VX[b2] - VX[a]) * mx, vy = VY[a] + (VY[b2] - VY[a]) * mx, sp = Math.hypot(vx, vy);
    const ang = Math.atan2(-vy, vx) * 180 / Math.PI;
    $('probeVel').innerHTML = d > ui.dry ? '<span style="display:inline-block;transform:rotate(' + (-ang).toFixed(0) + 'deg)">&rarr;</span> ' + sp.toFixed(1) + ' m/s' : '-';
  } else $('probeVel').textContent = 'n/a';
  const pc = $('probeChart'), dpr = window.devicePixelRatio || 1, cw = pc.clientWidth, chh = pc.clientHeight;
  if (pc.width !== Math.round(cw * dpr)) { pc.width = Math.round(cw * dpr); pc.height = Math.round(chh * dpr); }
  const c = pc.getContext('2d'); c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, cw, chh);
  const top = Math.max(pinPeak, 0.05);
  c.beginPath(); for (let f = 0; f < nFrames; f++) { const x = 2 + f / Math.max(1, nFrames - 1) * (cw - 4), y = chh - 4 - (pinSeries[f] / top) * (chh - 10); f ? c.lineTo(x, y) : c.moveTo(x, y); }
  c.strokeStyle = '#4c9dff'; c.lineWidth = 1.6; c.stroke(); c.lineTo(cw - 2, chh - 4); c.lineTo(2, chh - 4); c.closePath(); c.fillStyle = 'rgba(76,157,255,0.2)'; c.fill();
  const x = 2 + t * (cw - 4), y = chh - 4 - (d / top) * (chh - 10);
  c.strokeStyle = 'rgba(255,255,255,0.45)'; c.setLineDash([2, 3]); c.beginPath(); c.moveTo(x, 0); c.lineTo(x, chh); c.stroke(); c.setLineDash([]);
  c.fillStyle = '#fff'; c.beginPath(); c.arc(x, y, 3, 0, 7); c.fill();
}
let mapDownAt = null;
map.on('mousedown', (e) => { mapDownAt = e.point; });
map.on('click', (e) => { if (!W || W.colorStops) return; if (mapDownAt && Math.hypot(e.point.x - mapDownAt.x, e.point.y - mapDownAt.y) > 4) return; pinAt(e.lngLat.lng, e.lngLat.lat); });

// ---- rain overlay ----
const rainCanvas = $('rainCanvas'), rainCtx = rainCanvas.getContext('2d');
const durationMin = W ? (W.durationHr || 0) * 60 : 0;
let rainDrops = [];
function resizeRainCanvas() { rainCanvas.width = window.innerWidth; rainCanvas.height = window.innerHeight; }
function initRain() {
  resizeRainCanvas(); const n = Math.round((rainCanvas.width * rainCanvas.height) / 1200);
  rainDrops = Array.from({length: n}, () => ({x: Math.random() * rainCanvas.width, y: Math.random() * rainCanvas.height, len: 10 + Math.random() * 14, speedBase: 9 + Math.random() * 7, drift: -1.5 + Math.random() * 0.5}));
}
window.addEventListener('resize', resizeRainCanvas);
if (durationMin > 0 || (W && W.rainTimesHr)) initRain();
const RAIN_REF_MM_HR = 60.0;
function drawRain(rate) {
  const k = Math.max(0, Math.min(1, rate / RAIN_REF_MM_HR));
  rainCtx.clearRect(0, 0, rainCanvas.width, rainCanvas.height);
  if (k <= 0 || !rainDrops.length) return;
  const active = Math.max(1, Math.round(rainDrops.length * (0.08 + 0.92 * k))), sm = 0.5 + 1.3 * k;
  rainCtx.strokeStyle = 'rgba(200,225,240,' + (0.35 + 0.4 * k).toFixed(2) + ')'; rainCtx.lineWidth = 1.2 + 0.9 * k; rainCtx.lineCap = 'round';
  for (let i = 0; i < active; i++) {
    const d = rainDrops[i]; rainCtx.beginPath(); rainCtx.moveTo(d.x, d.y); rainCtx.lineTo(d.x + d.drift * 2, d.y + d.len); rainCtx.stroke();
    d.x += d.drift; d.y += d.speedBase * sm;
    if (d.y > rainCanvas.height) { d.y = -d.len; d.x = Math.random() * rainCanvas.width; }
    if (d.x < -20) d.x = rainCanvas.width + 20; if (d.x > rainCanvas.width + 20) d.x = -20;
  }
}
$('rainBtn').addEventListener('click', (e) => { ui.rainOn = !ui.rainOn; e.currentTarget.classList.toggle('on', ui.rainOn); });

// ---- playback controls ----
$('playPauseBtn').addEventListener('click', () => { playing = !playing; $('playPauseBtn').innerHTML = playing ? '&#10074;&#10074;' : '&#9654;'; if (playing) lastNow = null; });
$('speedSelect').addEventListener('change', () => { speed = parseFloat($('speedSelect').value); });
const ts = $('timeSlider');
ts.addEventListener('pointerdown', () => { dragging = true; });
ts.addEventListener('input', () => { elapsedSeconds = (ts.value / 1000) * LOOP_SECONDS; });
const endDrag = () => { dragging = false; lastNow = null; };
ts.addEventListener('pointerup', endDrag); ts.addEventListener('change', endDrag);

// ---- export video ----
const exportVideoBtn = $('exportVideoBtn3d');
exportVideoBtn.addEventListener('click', () => {
  if (exportVideoBtn.disabled) return;
  exportVideoBtn.disabled = true; const origLabel = exportVideoBtn.textContent; exportVideoBtn.textContent = 'Recording...';
  resetView(); dragging = false; playing = true; elapsedSeconds = 0; lastNow = null;
  setTimeout(() => {
    const stream = map.getCanvas().captureStream(30), chunks = []; let recorder;
    try { recorder = new MediaRecorder(stream, {mimeType: 'video/webm'}); }
    catch (e) { exportVideoBtn.textContent = origLabel; exportVideoBtn.disabled = false; alert('Video recording is not supported in this browser: ' + e.message); return; }
    recorder.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data); };
    recorder.onstop = async () => {
      exportVideoBtn.textContent = 'Encoding...';
      const form = new FormData(); form.append('video', new Blob(chunks, {type: 'video/webm'}), 'recording.webm');
      try {
        const resp = await fetch('/export-video', {method: 'POST', body: form});
        if (!resp.ok) { const err = await resp.json().catch(() => ({error: 'export failed'})); throw new Error(err.error || ('HTTP ' + resp.status)); }
        const url = URL.createObjectURL(await resp.blob()); const a = document.createElement('a'); a.href = url; a.download = 'flood_animation.mp4'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 5000);
      } catch (e) { alert('Video export failed: ' + e.message); }
      finally { exportVideoBtn.textContent = origLabel; exportVideoBtn.disabled = false; }
    };
    recorder.start(); setTimeout(() => recorder.stop(), LOOP_SECONDS * 1000 + 300);
  }, 300);
});

function tick(now) {
  requestAnimationFrame(tick);   // schedule first: one bad frame must never stop the animation
  try { tickBody(now); } catch (e) { if (!tick._warned) { tick._warned = true; console.warn('frame error (continuing):', e); } }
}
function tickBody(now) {
  if (lastNow === null) lastNow = now;
  const dt = (now - lastNow) / 1000; lastNow = now;
  if (playing && !dragging && HAS_ANIM) elapsedSeconds = (elapsedSeconds + dt * speed) % LOOP_SECONDS;
  const t = HAS_ANIM ? elapsedSeconds / LOOP_SECONDS : 0;
  const segF = t * nSeg, seg = Math.min(nSeg - 1, Math.floor(segF)), mixFrac = segF - seg, simMin = t * totalSimMin;
  setFloodFrame(seg, mixFrac);
  if (HAS_ANIM) {
    if (DATA.buildings.features.length) updateBuildingColors(seg, mixFrac);
    if (DATA.roads.features.length) updateRoadColors(seg, mixFrac);
    if (playing && !dragging) updateAndDrawFlow(seg, mixFrac, dt * speed * (totalSimMin * 60 / LOOP_SECONDS));
    if (!dragging) ts.value = Math.round(t * 1000);
    $('timeBig').textContent = fmtMin(simMin);
    $('timeSub').textContent = startTimeMs !== null ? new Date(startTimeMs + simMin * 60000).toLocaleString(undefined, {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}) : 'since start';
    $('timeLabel').textContent = fmtMin(simMin) + ' / ' + fmtMin(totalSimMin);
    const area = lerpArr(ST_AREA, seg, mixFrac);
    $('stArea').textContent = (area < 10 ? area.toFixed(1) : Math.round(area)) + ' ha';
    $('stDeep').textContent = lerpArr(ST_DEEP, seg, mixFrac).toFixed(2) + ' m';
    $('stVol').textContent = Math.round(lerpArr(ST_VOL, seg, mixFrac)).toLocaleString() + ' m³';
    drawChart(t); updateProbe(t, seg, mixFrac, simMin);
    if (ui.rainOn && simMin < Math.max(durationMin, W.rainTimesHr ? W.rainTimesHr[W.rainTimesHr.length - 1] * 60 : 0)) drawRain(currentRainRateMmHr(simMin));
    else rainCtx.clearRect(0, 0, rainCanvas.width, rainCanvas.height);
  } else if (W && pin) updateProbe(0, 0, 0, 0);
}
map.on('load', () => {
  addSceneSources(); resetView();
  if (W) { if (glWater) glWater.setMode('depth'); requestAnimationFrame(tick); }
});
