/* The Job 14 world on the home page: dry ground, a cut stump, a drop of water, a tree.
 *
 * Everything is generated here, there are no model or texture files. The page passes scroll
 * progress through the pinned story chapter (setProgress); that picks a point on the KEYS timeline.
 * The loop only runs while the chapter is on screen (setActive).
 *
 * Build (three is bundled, tree-shaken, so nothing is fetched from a CDN):
 *   npm i three@0.187.0 --prefix <scratch dir>
 *   NODE_PATH=<scratch dir>/node_modules node_modules/.bin/esbuild app/static/site/src/world.js \
 *     --bundle --minify --format=esm --target=es2020 --outfile=app/static/site/world.min.js
 */
import {
  BackSide, BufferAttribute, BufferGeometry, DoubleSide, InstancedBufferAttribute, InstancedMesh,
  Matrix4, Mesh, PerspectiveCamera, PlaneGeometry, Points, Quaternion, Scene, ShaderMaterial,
  SphereGeometry, Vector3, WebGLRenderer,
} from "three";

/* ─── Timeline: one key per chapter, reached when that chapter is centred on screen ─── */
const BASE = { spread: 0, water: 0, life: 0, grow: 0, lanterns: 0, drop: 0, scent: 0, seeds: 0, birds: 0 };
const KEYS = [
  { at: 0,    pos: [6.6, 2.0, 11.5], look: [0, 1.5, 0] },
  { at: 0.2,  pos: [4.6, 1.5, 8],    look: [0, 1.6, 0], drop: 0.3 },
  { at: 0.42, pos: [2.6, 1.6, 6.8],  look: [0, 1.5, 0], drop: 1, spread: 1.5, water: 0.1, life: 0.15, scent: 0.3 },
  { at: 0.58, pos: [-4.6, 1.6, 8],   look: [0, 1.9, 0], drop: 1, spread: 16, water: 0.55, life: 0.6, grow: 0.08, scent: 0.8 },
  { at: 0.76, pos: [9, 2.2, 16],     look: [0, 5, 0],   drop: 1, spread: 80, water: 0.9, life: 0.9, grow: 0.5, scent: 0.8, birds: 0.3 },
  { at: 1,    pos: [-15, 3.2, 27],   look: [-1, 8.6, 0], drop: 1, spread: 360, water: 1, life: 1, grow: 1, lanterns: 0, scent: 0.8, birds: 1 },
];
const STATE_KEYS = Object.keys(BASE);

/* ─── Small maths ─── */
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const mix = (a, b, t) => a + (b - a) * t;
const sstep = (a, b, v) => { const t = clamp((v - a) / (b - a)); return t * t * (3 - 2 * t); };
const hex = (h) => { const n = parseInt(h.slice(1), 16); return new Vector3((n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255); };

function rng(seed) { return () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; }; }
function hash2(x, y) { const s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453; return s - Math.floor(s); }
function vnoise(x, y) {
  const ix = Math.floor(x), iy = Math.floor(y), fx = x - ix, fy = y - iy;
  const u = fx * fx * (3 - 2 * fx), v = fy * fy * (3 - 2 * fy);
  return mix(mix(hash2(ix, iy), hash2(ix + 1, iy), u), mix(hash2(ix, iy + 1), hash2(ix + 1, iy + 1), u), v);
}
function fbm(x, y) { return vnoise(x, y) * 0.55 + vnoise(x * 2.1, y * 2.1) * 0.3 + vnoise(x * 4.3, y * 4.3) * 0.15; }

const riverX = (z) => -13 + 5 * Math.sin(z * 0.075) + 2.4 * Math.sin(z * 0.19 + 1);

function height(x, z) {
  const r = Math.hypot(x, z);
  const far = sstep(9, 70, r);
  let h = 0.28 * (1 - sstep(0, 6, r)) + (fbm(x * 0.13, z * 0.13) - 0.5) * 0.9;
  h += far * far * (fbm(x * 0.022 + 4, z * 0.022) * 34 - 6);
  const d = Math.abs(x - riverX(z));
  const bank = 1 - sstep(1.2, 6.5, d);
  return mix(h, -1.25, bank);
}

/* ─── Shared GLSL. No colour management: values are written straight as sRGB ─── */
const COMMON = /* glsl */`
uniform vec3 uFog; uniform float uFogNear, uFogFar; uniform float uTime, uLife;
vec3 lit(vec3 base, vec3 n){
  float d = dot(n, normalize(vec3(.46,.8,.38))) * .5 + .5;
  return base * mix(vec3(.58,.64,.64), vec3(1.14,1.12,1.02), d*d);
}
vec3 fogged(vec3 c, float dist){ return mix(c, uFog, smoothstep(uFogNear, uFogFar, dist)); }
float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7))) * 43758.5453); }
float noise(vec2 p){
  vec2 i = floor(p), f = fract(p); f = f*f*(3.-2.*f);
  return mix(mix(hash(i), hash(i+vec2(1,0)), f.x), mix(hash(i+vec2(0,1)), hash(i+vec2(1,1)), f.x), f.y);
}`;

export function start(canvas, { lite = false, still = false } = {}) {
  const renderer = new WebGLRenderer({ canvas, antialias: !lite, alpha: false, powerPreference: "high-performance" });
  renderer.toneMapping = 0;
  renderer.outputColorSpace = "srgb-linear";
  let dpr = Math.min(window.devicePixelRatio || 1, lite ? 1.25 : 1.75);
  renderer.setPixelRatio(dpr);

  const scene = new Scene();
  const camera = new PerspectiveCamera(38, 1, 0.1, 460);

  const shared = {
    uTime: { value: 0 }, uLife: { value: 0 }, uFog: { value: new Vector3() },
    uFogNear: { value: 24 }, uFogFar: { value: 170 },
    uSpread: { value: 0 }, uGrow: { value: 0 },
    uDrops: { value: Array.from({ length: 6 }, () => new Vector3(0, 0, 99)) },
  };

  /* ─── Sky ─── */
  const sky = new Mesh(new SphereGeometry(420, 24, 16), new ShaderMaterial({
    side: BackSide, depthWrite: false, uniforms: shared,
    vertexShader: `varying vec3 vD; void main(){ vD = normalize(position); vec4 p = projectionMatrix * modelViewMatrix * vec4(position,1.); gl_Position = p.xyww; }`,
    fragmentShader: COMMON + /* glsl */`
      varying vec3 vD;
      void main(){
        vec3 d = normalize(vD);
        float h = pow(max(d.y, 0.), .55);
        vec3 horizon = mix(vec3(.985,.885,.74), vec3(.90,.965,.99), uLife);
        vec3 zenith = mix(vec3(.92,.74,.58), vec3(.22,.58,.93), uLife);
        vec3 c = mix(horizon, zenith, h);
        vec3 sun = normalize(vec3(.55, mix(.1,.36,uLife), -.72));
        float s = max(dot(d, sun), 0.);
        c += vec3(1.,.82,.55) * (pow(s, 26.) * .3 + pow(s, 420.) * .9);
        if (d.y > 0.) {
          vec2 q = d.xz / (d.y + .16) * 1.6 + vec2(uTime * .012, 0.);
          float n = noise(q) * .55 + noise(q*2.1) * .3 + noise(q*4.3) * .15;
          c = mix(c, vec3(1.), smoothstep(.5, .72, n) * smoothstep(0., .2, d.y) * .94);
        }
        gl_FragColor = vec4(c, 1.);
      }`,
  }));
  sky.frustumCulled = false;
  scene.add(sky);

  /* ─── Ground: a polar grid, dense under the stump and coarse at the horizon ─── */
  const rings = lite ? 70 : 120, segs = lite ? 80 : 150;
  const gPos = [], gNor = [], gIdx = [];
  for (let i = 0; i <= rings; i++) {
    const r = 0.05 + Math.pow(i / rings, 2.3) * 330;
    for (let j = 0; j < segs; j++) {
      const a = (j / segs) * Math.PI * 2 + (i % 2) * Math.PI / segs;
      const x = Math.cos(a) * r, z = Math.sin(a) * r, e = 0.04 + r * 0.01;
      gPos.push(x, height(x, z), z);
      const n = new Vector3(height(x - e, z) - height(x + e, z), 2 * e, height(x, z - e) - height(x, z + e)).normalize();
      gNor.push(n.x, n.y, n.z);
    }
  }
  for (let i = 0; i < rings; i++) for (let j = 0; j < segs; j++) {
    const a = i * segs + j, b = i * segs + (j + 1) % segs, c = a + segs, d = b + segs;
    gIdx.push(a, b, c, b, d, c);
  }
  const groundGeo = new BufferGeometry();
  groundGeo.setAttribute("position", new BufferAttribute(new Float32Array(gPos), 3));
  groundGeo.setAttribute("normal", new BufferAttribute(new Float32Array(gNor), 3));
  groundGeo.setIndex(gIdx);
  const ground = new Mesh(groundGeo, new ShaderMaterial({
    uniforms: shared,
    vertexShader: `varying vec3 vP, vN; void main(){ vP = position; vN = normal; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.); }`,
    fragmentShader: COMMON + /* glsl */`
      uniform float uSpread, uGrow; uniform vec3 uDrops[6];
      varying vec3 vP, vN;
      float cracks(vec2 p){
        vec2 i = floor(p), f = fract(p); float a = 8., b = 8.;
        for (int y=-1;y<=1;y++) for (int x=-1;x<=1;x++){
          vec2 g = vec2(float(x),float(y));
          vec2 o = vec2(hash(i+g), hash(i+g+7.3));
          float d = length(g + o - f);
          if (d < a) { b = a; a = d; } else if (d < b) b = d;
        }
        return smoothstep(.0, .07, b - a);
      }
      void main(){
        float r = length(vP.xz);
        float dist = distance(cameraPosition, vP);
        float n = noise(vP.xz * .6) * .6 + noise(vP.xz * 1.9) * .4;
        float edge = uSpread * (1. + (noise(vP.xz * .22) - .5) * .5);
        float inside = 1. - smoothstep(edge - 1.4, edge, r);
        vec3 dry = mix(vec3(.87,.75,.57), vec3(.80,.65,.46), n);
        if (inside < 1. && dist < 60.) dry = mix(dry * .76, dry, cracks(vP.xz * 2.7));
        vec3 green = mix(vec3(.33,.70,.22), vec3(.15,.54,.19), n);
        green = mix(green, vec3(.52,.78,.27), smoothstep(.45, .9, noise(vP.xz * .09)) * .7);
        float wetEdge = smoothstep(edge - 2.5, edge, r) * (1. - smoothstep(edge, edge + 2.4, r));
        vec3 c = mix(dry, green, inside);
        c = mix(c, c * vec3(.74,.72,.66), wetEdge);
        c = mix(c, vec3(.86,.80,.62), (1. - smoothstep(-.75, -.34, vP.y)) * inside);
        c = lit(c, normalize(vN));
        float shade = (1. - smoothstep(1.5, 12., length(vP.xz - vec2(3., -2.)))) * (.5 + .5 * noise(vP.xz * 1.3)) * smoothstep(.45, 1., uGrow);
        c *= 1. - shade * .62;
        c *= 1. - .5 * (1. - smoothstep(.85, 2.1, r));

        for (int i=0;i<6;i++){
          vec3 dp = uDrops[i];
          if (dp.z < 3.) {
            float d = distance(vP.xz, dp.xy), R = dp.z * 3.4;
            float ring = 1. - smoothstep(.02, .12, abs(d - R));
            c = mix(c, vec3(.97,.99,1.), ring * (1. - dp.z / 3.) * .85);
          }
        }
        gl_FragColor = vec4(fogged(c, dist), 1.);
      }`,
  }));
  scene.add(ground);

  /* ─── Water: one flat level that rises into the carved river bed ─── */
  const waterGeo = new PlaneGeometry(700, 700, 1, 1);
  waterGeo.rotateX(-Math.PI / 2);
  const water = new Mesh(waterGeo, new ShaderMaterial({
    uniforms: shared,
    vertexShader: `varying vec3 vP; void main(){ vec4 w = modelMatrix * vec4(position,1.); vP = w.xyz; gl_Position = projectionMatrix * viewMatrix * w; }`,
    fragmentShader: COMMON + /* glsl */`
      varying vec3 vP;
      void main(){
        vec2 p = vP.xz * .55;
        float a = noise(p + vec2(uTime*.22, uTime*.1));
        float b = noise(p*2.2 - vec2(uTime*.16, -uTime*.12));
        vec3 c = mix(vec3(.08,.56,.84), vec3(.34,.82,.94), a);
        c += vec3(1.) * smoothstep(.6, .98, b * a * 1.75) * .3;
        gl_FragColor = vec4(fogged(c, distance(cameraPosition, vP)), 1.);
      }`,
  }));
  water.position.y = -2.4;
  scene.add(water);

  /* ─── Tree: every vertex stores when it appears (aT) and the axis point it grows out of ─── */
  const R = rng(1407);
  const tPos = [], tCen = [], tT = [], tNor = [], tKind = [], tIdx = [];
  const tips = [], leafSeeds = [];
  let vCount = 0;

  function tube(path, radii, times, sides, kind) {
    const start = vCount;
    const up = new Vector3(), side = new Vector3(), bin = new Vector3(), dir = new Vector3();
    for (let i = 0; i < path.length; i++) {
      dir.copy(path[Math.min(i + 1, path.length - 1)]).sub(path[Math.max(i - 1, 0)]).normalize();
      up.set(0.31, 0.9, 0.17);
      side.crossVectors(dir, up).normalize();
      bin.crossVectors(dir, side).normalize();
      for (let j = 0; j < sides; j++) {
        const a = (j / sides) * Math.PI * 2, c = Math.cos(a), s = Math.sin(a);
        const nx = side.x * c + bin.x * s, ny = side.y * c + bin.y * s, nz = side.z * c + bin.z * s;
        tPos.push(path[i].x + nx * radii[i], path[i].y + ny * radii[i], path[i].z + nz * radii[i]);
        tCen.push(path[i].x, path[i].y, path[i].z);
        tNor.push(nx, ny, nz); tT.push(times[i]); tKind.push(kind);
      }
    }
    for (let i = 0; i < path.length - 1; i++) for (let j = 0; j < sides; j++) {
      const a = start + i * sides + j, b = start + i * sides + (j + 1) % sides, c = a + sides, d = b + sides;
      tIdx.push(a, c, b, b, c, d);
    }
    vCount += path.length * sides;
  }

  const maxDepth = lite ? 3 : 4;
  function branch(base, dir, len, rad, depth, t0) {
    const steps = depth === 0 ? 11 : 6;
    const span = [0.38, 0.27, 0.2, 0.16, 0.12][depth];
    const path = [], radii = [], times = [];
    const p = base.clone(), d = dir.clone().normalize();
    for (let i = 0; i <= steps; i++) {
      const u = i / steps;
      path.push(p.clone());
      radii.push(rad * mix(1, 0.34, u) + 0.008);
      times.push(t0 + span * u);
      d.x += (R() - 0.5) * 0.24; d.z += (R() - 0.5) * 0.24; d.y += 0.03 + (depth === 0 ? 0.06 : 0);
      d.normalize();
      p.addScaledVector(d, len / steps);
    }
    tube(path, radii, times, depth < 2 ? 8 : depth < 3 ? 5 : 3, 1);
    const tEnd = t0 + span;
    if (depth >= 2) for (let i = 2; i <= steps; i++) leafSeeds.push({ p: path[i], t: times[i] + 0.04 });
    if (depth === maxDepth) { tips.push({ p: path[steps], t: tEnd }); return; }
    const kids = depth === 0 ? 5 : 3;
    for (let k = 0; k < kids; k++) {
      const u = depth === 0 ? 0.42 + (k / kids) * 0.56 : 0.45 + R() * 0.55;
      const idx = Math.min(steps, Math.floor(u * steps));
      const a = R() * Math.PI * 2 + k * 2.4;
      const out = new Vector3(Math.cos(a), depth === 0 ? 0.42 + R() * 0.5 : 0.35 + R() * 0.5, Math.sin(a));
      out.add(d.clone().multiplyScalar(depth === 0 ? 0.25 : 0.75));
      branch(path[idx], out, len * (depth === 0 ? 0.74 : 0.72), radii[idx] * 0.7, depth + 1, times[idx]);
    }
  }
  branch(new Vector3(0, 0.98, 0), new Vector3(0.04, 1, 0.02), 6.8, 0.56, 0, 0);

  /* The stump and its flared roots are in the same geometry, always visible (aT below zero). */
  const stumpStart = vCount;
  const rs = 18, ys = [-0.5, 0.0, 0.3, 0.7, 1.0];
  ys.forEach((y, i) => {
    for (let j = 0; j < rs; j++) {
      const a = (j / rs) * Math.PI * 2;
      const rad = (1.02 - 0.2 * (i / 4)) * (1 + (0.1 * Math.sin(a * 5 + 1) + 0.05 * Math.sin(a * 11)) * (1 - i / 5)) + (i < 2 ? 0.26 * Math.max(0, Math.sin(a * 3 + 0.6)) : 0);
      tPos.push(Math.cos(a) * rad, y, Math.sin(a) * rad);
      tCen.push(Math.cos(a) * rad, y, Math.sin(a) * rad);
      tNor.push(Math.cos(a), 0.15, Math.sin(a)); tT.push(-1); tKind.push(2);
    }
  });
  for (let i = 0; i < ys.length - 1; i++) for (let j = 0; j < rs; j++) {
    const a = stumpStart + i * rs + j, b = stumpStart + i * rs + (j + 1) % rs, c = a + rs, d = b + rs;
    tIdx.push(a, c, b, b, c, d);
  }
  vCount += ys.length * rs;
  const capStart = vCount, rimStart = stumpStart + (ys.length - 1) * rs;
  tPos.push(0, 1.0, 0); tCen.push(0, 1.0, 0); tNor.push(0, 1, 0); tT.push(-1); tKind.push(3);
  for (let j = 0; j < rs; j++) {
    const k = rimStart + j;
    tPos.push(tPos[k * 3], tPos[k * 3 + 1], tPos[k * 3 + 2]);
    tCen.push(tPos[k * 3], tPos[k * 3 + 1], tPos[k * 3 + 2]);
    tNor.push(0, 1, 0); tT.push(-1); tKind.push(3);
  }
  for (let j = 0; j < rs; j++) tIdx.push(capStart, capStart + 1 + (j + 1) % rs, capStart + 1 + j);
  vCount += rs + 1;

  const treeGeo = new BufferGeometry();
  treeGeo.setAttribute("position", new BufferAttribute(new Float32Array(tPos), 3));
  treeGeo.setAttribute("aCenter", new BufferAttribute(new Float32Array(tCen), 3));
  treeGeo.setAttribute("normal", new BufferAttribute(new Float32Array(tNor), 3));
  treeGeo.setAttribute("aT", new BufferAttribute(new Float32Array(tT), 1));
  treeGeo.setAttribute("aKind", new BufferAttribute(new Float32Array(tKind), 1));
  treeGeo.setIndex(tIdx);
  const tree = new Mesh(treeGeo, new ShaderMaterial({
    uniforms: shared,
    vertexShader: /* glsl */`
      uniform float uGrow;
      attribute vec3 aCenter; attribute float aT, aKind;
      varying vec3 vP, vN; varying float vK;
      void main(){
        float g = aT < 0. ? 1. : smoothstep(aT, aT + .05, uGrow) * (.3 + .7 * smoothstep(0., .9, uGrow));
        vec3 p = mix(aCenter, position, g);
        vP = p; vN = normal; vK = aKind;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.);
      }`,
    fragmentShader: COMMON + /* glsl */`
      varying vec3 vP, vN; varying float vK;
      void main(){
        vec3 c;
        if (vK > 2.5) {
          float r = length(vP.xz) + noise(vP.xz * 3.) * .07;
          c = mix(vec3(.82,.68,.47), vec3(.67,.50,.31), smoothstep(.3,.7, sin(r * 42.) * .5 + .5));
          c = mix(c, vec3(.48,.34,.22), smoothstep(.72, .88, length(vP.xz)));
        } else {
          float a = atan(vP.z, vP.x);
          float bark = noise(vec2(a * 7. + vP.x, vP.y * 1.6)) * .6 + noise(vec2(a * 19., vP.y * 4.)) * .4;
          vec3 dead = mix(vec3(.46,.40,.34), vec3(.32,.27,.23), bark);
          vec3 live = mix(vec3(.50,.37,.25), vec3(.34,.24,.16), bark);
          c = vK > 1.5 ? mix(dead, live, uLife) : live;
          if (vK < 1.5) c = mix(vec3(.55,.68,.36), c, smoothstep(.0, 1.6, length(vP.xz) * 3. + (vP.y - 1.) * .2 - .4 + uLife * .8));
        }
        c = lit(c, normalize(vK > 2.5 ? vec3(0.,1.,0.) : vN));
        gl_FragColor = vec4(fogged(c, distance(cameraPosition, vP)), 1.);
      }`,
  }));
  tree.frustumCulled = false;
  scene.add(tree);

  /* ─── Leaves ─── */
  const leafGeo = new BufferGeometry();
  leafGeo.setAttribute("position", new BufferAttribute(new Float32Array([0, 0, 0, 0.3, 0.02, 0.42, 0, 0.06, 1, -0.3, 0.02, 0.42]), 3));
  leafGeo.setIndex([0, 1, 2, 0, 2, 3]);
  const perSeed = lite ? 16 : 30;
  const firstLeaves = 6;
  const leafCount = Math.min(leafSeeds.length * perSeed, lite ? 8000 : 34000) + firstLeaves;
  const leafT = new Float32Array(leafCount), leafTone = new Float32Array(leafCount);
  const leafMat = new ShaderMaterial({
    side: DoubleSide, uniforms: shared,
    vertexShader: /* glsl */`
      uniform float uGrow, uTime;
      attribute float aT, aTone;
      varying float vTone; varying vec3 vN, vW;
      void main(){
        float g = smoothstep(aT, aT + .09, uGrow);
        vec3 p = position * g;
        vec4 w = modelMatrix * instanceMatrix * vec4(p, 1.);
        float t = uTime * 1.1 + w.x * .5 + w.z * .4;
        w.xyz += vec3(sin(t), sin(t * 1.3) * .6, cos(t * .9)) * .05 * position.z;
        vW = w.xyz; vTone = aTone;
        vN = normalize(mat3(instanceMatrix) * vec3(0., 1., .1));
        gl_Position = projectionMatrix * viewMatrix * w;
      }`,
    fragmentShader: COMMON + /* glsl */`
      varying float vTone; varying vec3 vN, vW;
      void main(){
        vec3 c = mix(vec3(.13,.58,.17), vec3(.56,.88,.20), vTone);
        if (vTone > .93) c = vec3(.70,.95,.28);
        c *= mix(.62, 1.22, smoothstep(5., 17., vW.y));
        c = lit(c, normalize(vN) * sign(vN.y + .001));
        gl_FragColor = vec4(fogged(c, distance(cameraPosition, vW)), 1.);
      }`,
  });
  const leaves = new InstancedMesh(leafGeo, leafMat, leafCount);
  leaves.frustumCulled = false;
  const m4 = new Matrix4(), q = new Quaternion(), v = new Vector3(), s = new Vector3(), e = new Vector3();
  const place = (i, x, y, z, sc, axis, ang, t) => {
    m4.compose(v.set(x, y, z), q.setFromAxisAngle(e.copy(axis).normalize(), ang), s.set(sc, sc, sc));
    leaves.setMatrixAt(i, m4); leafT[i] = t; leafTone[i] = R() * 0.9;
  };
  // The first pair of leaves: the moment the stump is visibly alive again.
  for (let i = 0; i < firstLeaves; i++) {
    const a = (i / firstLeaves) * Math.PI * 2;
    place(i, 0, 1.25 + i * 0.09, 0, 0.42 - i * 0.03, new Vector3(Math.cos(a), 0.1, Math.sin(a)), 0.9, 0.012 + i * 0.006);
  }
  for (let i = firstLeaves; i < leafCount; i++) {
    const sd = leafSeeds[Math.floor(R() * leafSeeds.length)];
    place(i, sd.p.x + (R() - 0.5) * 1.3, sd.p.y + (R() - 0.5) * 1, sd.p.z + (R() - 0.5) * 1.3, 0.2 + R() * 0.17,
      new Vector3(R() - 0.5, R() - 0.5, R() - 0.5), R() * 6.28, sd.t);
    if (R() < 0.05) leafTone[i] = 0.96;
  }
  leafGeo.setAttribute("aT", new InstancedBufferAttribute(leafT, 1));
  leafGeo.setAttribute("aTone", new InstancedBufferAttribute(leafTone, 1));
  scene.add(leaves);

  /* ─── Grass: blades lift as the green front passes them ─── */
  const bladeGeo = new BufferGeometry();
  bladeGeo.setAttribute("position", new BufferAttribute(new Float32Array([-0.06, 0, 0, 0.06, 0, 0, 0.0, 1, 0.1]), 3));
  bladeGeo.setIndex([0, 1, 2]);
  const bladeCount = lite ? 9000 : 42000;
  const bladeOff = new Float32Array(bladeCount * 3), bladeSeed = new Float32Array(bladeCount);
  for (let i = 0; i < bladeCount; i++) {
    const a = R() * 6.283, r = 1.1 + Math.pow(R(), 1.7) * 44;
    const x = Math.cos(a) * r, z = Math.sin(a) * r;
    bladeOff[i * 3] = x; bladeOff[i * 3 + 1] = height(x, z); bladeOff[i * 3 + 2] = z; bladeSeed[i] = R();
  }
  bladeGeo.setAttribute("aOff", new InstancedBufferAttribute(bladeOff, 3));
  bladeGeo.setAttribute("aSeed", new InstancedBufferAttribute(bladeSeed, 1));
  const grass = new InstancedMesh(bladeGeo, new ShaderMaterial({
    side: DoubleSide, uniforms: shared,
    vertexShader: /* glsl */`
      uniform float uSpread, uTime;
      attribute vec3 aOff; attribute float aSeed;
      varying vec3 vW; varying float vH, vS;
      void main(){
        float r = length(aOff.xz);
        float up = (1. - smoothstep(uSpread - 3.2, uSpread - .4, r)) * step(.1, uSpread) * step(-.3, aOff.y);
        float a = aSeed * 40.;
        vec3 p = vec3(position.x * cos(a) - position.z * sin(a), position.y, position.x * sin(a) + position.z * cos(a));
        p *= vec3(.8, .24 + aSeed * .32, .8) * up;
        float t = uTime * 1.5 + aOff.x * .35 + aOff.z * .3;
        p.x += sin(t) * .1 * position.y; p.z += cos(t * .8) * .07 * position.y;
        vec3 w = aOff + p;
        vW = w; vH = position.y; vS = aSeed;
        gl_Position = projectionMatrix * viewMatrix * vec4(w, 1.);
      }`,
    fragmentShader: COMMON + /* glsl */`
      varying vec3 vW; varying float vH, vS;
      void main(){
        vec3 c = mix(vec3(.08,.42,.12), vec3(.50,.90,.22), vH * (.45 + vS * .55));
        if (vS > .972 && vH > .75) c = vS > .986 ? vec3(1.,.97,.9) : vec3(1.,.83,.15);
        gl_FragColor = vec4(fogged(c, distance(cameraPosition, vW)), 1.);
      }`,
  }), bladeCount);
  grass.frustumCulled = false;
  scene.add(grass);

  /* ─── The drop ─── */
  const dropMat = new ShaderMaterial({
    uniforms: shared,
    vertexShader: `varying vec3 vN, vV; void main(){ vec4 m = modelViewMatrix * vec4(position,1.); vN = normalize(normalMatrix * normal); vV = normalize(-m.xyz); gl_Position = projectionMatrix * m; }`,
    fragmentShader: /* glsl */`
      varying vec3 vN, vV;
      void main(){
        float f = pow(1. - max(dot(normalize(vN), vV), 0.), 2.2);
        vec3 c = mix(vec3(.60,.84,.92), vec3(.13,.40,.52), f);
        float spec = pow(max(dot(normalize(vN), normalize(vec3(.4,.7,.6))), 0.), 40.);
        gl_FragColor = vec4(c + spec, 1.);
      }`,
  });
  const dropGeo = new SphereGeometry(0.17, 18, 14);
  { // A teardrop, not a ball.
    const p = dropGeo.attributes.position;
    for (let i = 0; i < p.count; i++) {
      const y = p.getY(i) / 0.17, k = y > 0 ? 1 - y * 0.82 : 1;
      p.setXYZ(i, p.getX(i) * k, p.getY(i) * (y > 0 ? 2.5 : 1), p.getZ(i) * k);
    }
    dropGeo.computeVertexNormals();
  }
  const heroDrop = new Mesh(dropGeo, dropMat);
  scene.add(heroDrop);
  const tapDrops = Array.from({ length: 6 }, () => {
    const d = new Mesh(dropGeo, dropMat); d.visible = false; d.userData = { fall: 0, x: 0, z: 0, y0: 0, g: 0 }; scene.add(d); return d;
  });

  /* ─── Twelve lights, one per unit, each on its own outer branch ─── */
  const picks = tips.filter((t) => t.p.y > 7.5).sort((a, b) => Math.atan2(a.p.z, a.p.x) - Math.atan2(b.p.z, b.p.x));
  const lamps = Array.from({ length: 12 }, (_, i) => picks[Math.floor((i / 12) * picks.length)].p);
  const lampGeo = new BufferGeometry();
  lampGeo.setAttribute("position", new BufferAttribute(new Float32Array(lamps.flatMap((p) => [p.x, p.y, p.z])), 3));
  lampGeo.setAttribute("aI", new BufferAttribute(new Float32Array(lamps.map((_, i) => i)), 1));
  const lampMat = new ShaderMaterial({
    transparent: true, depthWrite: false, depthTest: false,
    uniforms: { ...shared, uShow: { value: 0 }, uActive: { value: -1 }, uPx: { value: 1 } },
    vertexShader: /* glsl */`
      uniform float uShow, uActive, uPx, uTime;
      attribute float aI; varying float vA;
      void main(){
        float act = 1. - step(.5, abs(uActive - aI));
        vA = uShow * (uActive < 0. ? 1. : mix(.45, 1., act));
        vec4 m = modelViewMatrix * vec4(position, 1.);
        gl_PointSize = uPx * mix(30., 76., act) * (1. + sin(uTime * 1.6 + aI) * .1) * smoothstep(0., .4, uShow) * (24. / max(-m.z, 6.));
        gl_Position = projectionMatrix * m;
      }`,
    fragmentShader: /* glsl */`
      varying float vA;
      void main(){
        float d = length(gl_PointCoord - .5) * 2.;
        float core = 1. - smoothstep(.2, .3, d);
        float halo = pow(1. - clamp(d, 0., 1.), 2.) * .55;
        vec3 c = mix(vec3(.91,.70,.20), vec3(1.,.95,.72), core);
        gl_FragColor = vec4(c, (core + halo) * vA);
      }`,
  });
  const lampPoints = new Points(lampGeo, lampMat);
  lampPoints.frustumCulled = false;
  scene.add(lampPoints);

  /* ─── Scent: gold motes rising; seeds: the same motes carried off on the wind ─── */
  const moteN = lite ? 180 : 520;
  const moteP = new Float32Array(moteN * 3), moteS = new Float32Array(moteN);
  for (let i = 0; i < moteN; i++) {
    const a = R() * 6.283, r = 0.6 + R() * 11;
    moteP[i * 3] = Math.cos(a) * r; moteP[i * 3 + 1] = R(); moteP[i * 3 + 2] = Math.sin(a) * r; moteS[i] = R();
  }
  const moteGeo = new BufferGeometry();
  moteGeo.setAttribute("position", new BufferAttribute(moteP, 3));
  moteGeo.setAttribute("aS", new BufferAttribute(moteS, 1));
  const moteMat = new ShaderMaterial({
    transparent: true, depthWrite: false,
    uniforms: { uTime: shared.uTime, uShow: { value: 0 }, uSeeds: { value: 0 }, uPx: { value: 1 } },
    vertexShader: /* glsl */`
      uniform float uTime, uShow, uSeeds, uPx;
      attribute float aS; varying float vA, vS;
      void main(){
        float t = fract(position.y + uTime * (.018 + aS * .03));
        vec3 p = vec3(position.x, .2 + t * 15., position.z);
        p.x += sin(t * 9. + aS * 40.) * .7; p.z += cos(t * 7. + aS * 30.) * .7;
        p += vec3(-24., 5., -9.) * t * uSeeds * (.6 + aS);
        vA = uShow * sin(t * 3.1416);
        vS = uSeeds;
        vec4 m = modelViewMatrix * vec4(p, 1.);
        gl_PointSize = uPx * (2.5 + aS * 4. + uSeeds * 3.) * (26. / max(-m.z, 2.));
        gl_Position = projectionMatrix * m;
      }`,
    fragmentShader: /* glsl */`
      varying float vA, vS;
      void main(){
        float d = length(gl_PointCoord - .5) * 2.;
        vec3 c = mix(vec3(.84,.62,.16), vec3(1.,.97,.86), vS);
        gl_FragColor = vec4(c, (1. - smoothstep(.45, 1., d)) * vA * .9);
      }`,
  });
  const motes = new Points(moteGeo, moteMat);
  motes.frustumCulled = false;
  scene.add(motes);

  /* ─── Birds, once there is a tree worth circling ─── */
  const birdN = 9, bP = [], bI = [], bW = [];
  for (let i = 0; i < birdN; i++) {
    bP.push(-1, 0, 0, 0, 0, 0.26, 0, 0, -0.1, 1, 0, 0, 0, 0, 0.26, 0, 0, -0.1);
    for (let k = 0; k < 6; k++) { bI.push(i); bW.push(k === 0 || k === 3 ? 1 : 0); }
  }
  const birdGeo = new BufferGeometry();
  birdGeo.setAttribute("position", new BufferAttribute(new Float32Array(bP), 3));
  birdGeo.setAttribute("aI", new BufferAttribute(new Float32Array(bI), 1));
  birdGeo.setAttribute("aW", new BufferAttribute(new Float32Array(bW), 1));
  const birdMat = new ShaderMaterial({
    side: DoubleSide, transparent: true,
    uniforms: { uTime: shared.uTime, uShow: { value: 0 } },
    vertexShader: /* glsl */`
      uniform float uTime, uShow; attribute float aI, aW; varying float vA;
      void main(){
        float a = uTime * .16 + aI * .55, r = 21. + sin(aI * 2.) * 5.;
        vec3 c = vec3(cos(a) * r, 20. + sin(aI * 1.7) * 3., sin(a) * r);
        vec3 p = position * .55;
        p.y += aW * sin(uTime * 7. + aI * 2.) * .36;
        float h = -a; p = vec3(p.x * cos(h) + p.z * sin(h), p.y, -p.x * sin(h) + p.z * cos(h));
        vA = uShow;
        gl_Position = projectionMatrix * viewMatrix * vec4(c + p, 1.);
      }`,
    fragmentShader: `varying float vA; void main(){ gl_FragColor = vec4(.27,.27,.25, vA); }`,
  });
  const birds = new Mesh(birdGeo, birdMat);
  birds.frustumCulled = false;
  scene.add(birds);

  /* ─── Scroll → timeline ─── */
  const steps = KEYS.map((key) => ({ ...BASE, ...key }));
  let progress = 0;

  const cur = { ...BASE, px: 0, py: 0, pz: 0, lx: 0, ly: 0, lz: 0 };
  const target = { ...cur };
  function readScroll() {
    let i = 0;
    while (i < steps.length - 2 && progress > steps[i + 1].at) i++;
    const a = steps[i], b = steps[i + 1];
    const raw = clamp((progress - a.at) / (b.at - a.at));
    const t = raw * raw * (3 - 2 * raw);
    for (const key of STATE_KEYS) target[key] = mix(a[key], b[key], key === "drop" ? raw : t);
    target.px = mix(a.pos[0], b.pos[0], t); target.py = mix(a.pos[1], b.pos[1], t); target.pz = mix(a.pos[2], b.pos[2], t);
    target.lx = mix(a.look[0], b.look[0], t); target.ly = mix(a.look[1], b.look[1], t); target.lz = mix(a.look[2], b.look[2], t);
  }

  let W = 1, H = 1, portrait = false;
  function resize() {
    const nw = canvas.clientWidth || window.innerWidth, nh = canvas.clientHeight || window.innerHeight;
    W = nw; H = nh; portrait = W / H < 0.85;
    renderer.setSize(W, H, false);
    camera.aspect = W / H;
    camera.fov = portrait ? 50 : 38;
    camera.updateProjectionMatrix();
    lampMat.uniforms.uPx.value = moteMat.uniforms.uPx.value = dpr * Math.min(1, H / 800 + 0.15);
  }

  /* ─── Life on top of the scroll ─── */
  let mx = 0, my = 0, smx = 0, smy = 0;
  const onMove = (ev) => { mx = ev.clientX / W - 0.5; my = ev.clientY / H - 0.5; };
  window.addEventListener("pointermove", onMove, { passive: true });

  let dropIx = 0;
  const ripples = [];
  const addRipple = (x, z) => { ripples.push({ x, z, age: 0 }); if (ripples.length > 6) ripples.shift(); };

  const ray = new Vector3();
  function groundAt(cx, cy) {
    ray.set((cx / W) * 2 - 1, -(cy / H) * 2 + 1, 0.5).unproject(camera).sub(camera.position).normalize();
    if (ray.y >= -0.02) return null;
    const t = -camera.position.y / ray.y;
    if (t > 90) return null;
    return { x: camera.position.x + ray.x * t, z: camera.position.z + ray.z * t };
  }
  function rain(cx, cy) {
    const at = groundAt(cx, cy);
    if (!at) return false;
    const d = tapDrops[dropIx++ % tapDrops.length];
    const y0 = Math.max(9, camera.position.y * 0.8);
    Object.assign(d.userData, { fall: 1, x: at.x, z: at.z, y0, g: height(at.x, at.z), t: 0 });
    d.position.set(at.x, y0, at.z); d.visible = true;
    return true;
  }

  /* ─── Frame ─── */
  let last = performance.now(), running = true, raf = 0, slow = 0, fast = 0, firstFrame = true;
  const look = new Vector3();
  function frame(now) {
    raf = 0;
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    if (!still) shared.uTime.value += dt;
    readScroll();
    const k = still ? 1 : 1 - Math.exp(-dt * 3.1);
    for (const key of Object.keys(cur)) cur[key] = mix(cur[key], target[key], k);
    smx = mix(smx, mx, 1 - Math.exp(-dt * 2)); smy = mix(smy, my, 1 - Math.exp(-dt * 2));

    shared.uSpread.value = cur.spread; shared.uGrow.value = cur.grow;
    shared.uLife.value = cur.life;
    lampMat.uniforms.uShow.value = cur.lanterns;
    moteMat.uniforms.uShow.value = cur.scent; moteMat.uniforms.uSeeds.value = cur.seeds;
    birdMat.uniforms.uShow.value = cur.birds; birds.visible = cur.birds > 0.02;
    water.position.y = mix(-2.4, -0.42, cur.water);
    shared.uFog.value.set(mix(0.985, 0.82, cur.life), mix(0.885, 0.93, cur.life), mix(0.74, 0.98, cur.life));

    // The scripted drop, tied to the scrollbar; when it lands it stays landed.
    heroDrop.visible = cur.drop > 0.02 && cur.drop < 0.985;
    const ft = cur.drop * cur.drop;
    heroDrop.position.set(0, mix(15, 1.05, ft), 0);
    const hit = cur.drop >= 0.985;
    if (hit && !frame.hit) addRipple(0, 0);
    frame.hit = hit;

    for (const d of tapDrops) {
      const u = d.userData;
      if (!u.fall) continue;
      u.t += dt;
      d.position.y = u.y0 - 22 * u.t * u.t;
      if (d.position.y <= u.g) { u.fall = 0; d.visible = false; addRipple(u.x, u.z); }
    }
    for (const r of ripples) r.age += dt;
    shared.uDrops.value.forEach((vd, i) => { const r = ripples[i]; vd.set(r ? r.x : 0, r ? r.z : 0, r ? r.age : 99); });
    while (ripples.length && ripples[0].age > 3) ripples.shift();

    const dist = portrait ? 1.5 : 1;
    look.set(cur.lx, cur.ly, cur.lz);
    camera.position.set(
      look.x + (cur.px - look.x) * dist + smx * 0.9,
      look.y + (cur.py - look.y) * dist - smy * 0.45,
      look.z + (cur.pz - look.z) * dist,
    );
    camera.position.y = Math.max(camera.position.y, height(camera.position.x, camera.position.z) + 0.55);
    camera.lookAt(look);
    camera.updateMatrixWorld();

    renderer.render(scene, camera);
    if (firstFrame) { firstFrame = false; canvas.classList.add("is-on"); }

    // Hold frame rate on modest phones by giving up resolution, never the scene.
    if (!still) {
      if (dt > 0.04) { slow++; fast = 0; } else { fast++; if (fast > 120) slow = 0; }
      if (slow > 40 && dpr > 0.7) { dpr = Math.max(0.7, dpr * 0.8); slow = 0; renderer.setPixelRatio(dpr); resize(); }
      if (running) raf = requestAnimationFrame(frame);
    }
  }
  const kick = () => { if (!raf) raf = requestAnimationFrame(frame); };

  const onResize = () => { resize(); if (still) kick(); };
  let active = true;
  const onVis = () => { running = active && !document.hidden; last = performance.now(); if (running) kick(); };
  window.addEventListener("resize", onResize, { passive: true });
  document.addEventListener("visibilitychange", onVis);

  resize();
  readScroll();
  Object.assign(cur, target);
  kick();

  return {
    setProgress(p) { progress = clamp(p); if (still) kick(); },
    setActive(on) { active = on; onVis(); },
  };
}
