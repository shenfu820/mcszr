/**
 * 板材检测项目 · 数字人 3D 形象前端
 * ---------------------------------------------------------------
 * 职责：只做“看得见”的部分 —— VRM 渲染 / 呼吸眨眼口型 / 字幕 / 状态灯。
 * 决策全部在后端（server/），这里通过 SSE 或 window.DH.* 接收指令。
 *
 * 对外接口（供宿主页面/后端注入调用）：
 *   DH.say({text, audio, visemes, hold, state})  播一句台词
 *   DH.setVoice(0|1)                              开/关声音
 *   DH.alarm(on)                                  报警态
 *   DH.setState(idle|busy|listen|alert)
 *   DH.hide() / DH.show()
 */
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { loadFBX } from './fbx.js';
import { createPortrait } from './portrait.js';
import { loadMeshAvatar } from './mesh.js';

// ───────────────────────────────── 基础常量
// 默认用 2.5D 照片人像（assets/portrait.png + face.json）；
// 换 3D 用 ?model=../models/rocketbox/m08/m08_facial.fbx 或 ?model=../models/xxx.vrm
const DEFAULT_MODEL = 'portrait';
const MODEL_URL = new URLSearchParams(location.search).get('model') || DEFAULT_MODEL;
const SSE_URL = new URLSearchParams(location.search).get('events') || '/api/events';

const $ = (id) => document.getElementById(id);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const lerp = (a, b, t) => a + (b - a) * t;

// ───────────────────────────────── 渲染器
const canvas = $('gl');
const renderer = new THREE.WebGLRenderer({
  canvas, antialias: true, alpha: true, premultipliedAlpha: false,
  powerPreference: 'high-performance',
});
renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);
renderer.setClearColor(0x000000, 0);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;

const scene = new THREE.Scene();

const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 60);
camera.position.set(0, 1.42, 2.15);
camera.lookAt(0, 1.34, 0);

// ───────────────────────────────── 灯光（三点 + 轮廓）
const hemi = new THREE.HemisphereLight(0xdfefff, 0x2a3340, 1.15);
scene.add(hemi);

const key = new THREE.DirectionalLight(0xffffff, 2.0);
key.position.set(0.9, 2.6, 2.2);
scene.add(key);

const fill = new THREE.DirectionalLight(0x9fd0ff, 0.85);
fill.position.set(-1.8, 1.5, 1.2);
scene.add(fill);

const rim = new THREE.DirectionalLight(0x53f0c0, 1.25);
rim.position.set(-0.6, 1.9, -2.4);
scene.add(rim);

const rim2 = new THREE.DirectionalLight(0x4aa8ff, 0.9);
rim2.position.set(1.9, 1.6, -1.9);
scene.add(rim2);

// ───────────────────────────────── 状态
const state = {
  model: null,         // 当前模型（VRM 对象或 FBXModel）
  adapter: null,       // 统一接口
  clock: new THREE.Clock(),
  targetLook: new THREE.Vector2(0, 0),
  look: new THREE.Vector2(0, 0),
  blinkTimer: 0,
  blinkValue: 0,
  blinkPhase: 0,       // 0=待机 1=闭 2=开
  idleHead: 0,
  mood: 'relaxed',
  talking: false,
  voiceOn: true,
  lip: 0,              // 当前口型强度 0..1
  lipTarget: 0,
  lipSeed: Math.random() * 100,
  breath: 0,
  busy: false,
};

// ───────────────────────────────── 口型（音频包络驱动）
let audioCtx = null, analyser = null, audioData = null;
let currentAudio = null, audioSrcNode = null;
let captionTimer = null, captionFull = '', captionIdx = 0;

function ensureAudioCtx() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.55;
    audioData = new Uint8Array(analyser.fftSize);
    analyser.connect(audioCtx.destination);
  }
  if (audioCtx.state === 'suspended') audioCtx.resume().catch(() => {});
  return audioCtx;
}

/** 读当前音频能量 0..1 */
function readEnergy() {
  if (!analyser || !currentAudio || currentAudio.paused) return 0;
  analyser.getByteTimeDomainData(audioData);
  let sum = 0;
  for (let i = 0; i < audioData.length; i++) {
    const v = (audioData[i] - 128) / 128;
    sum += v * v;
  }
  const rms = Math.sqrt(sum / audioData.length);
  // 木业车间语音能量一般 0.02~0.25，做一次非线性放大
  return clamp(Math.pow(rms * 4.4, 0.72), 0, 1);
}

// ───────────────────────────────── 模型适配层
// VRM 有标准人形骨骼+表情预设；Rocketbox 的 FBX 用 Bip01 骨骼 + Oculus viseme。
// 这里把两者抹平成同一套接口，上层动画/口型/表情代码只认 adapter。
const FBX_TEX = {
  bodyColor: '../models/rocketbox/m08/m014_body_color.png',
  bodyNormal: '../models/rocketbox/m08/m014_body_normal.png',
  headColor: '../models/rocketbox/m08/m014_head_color.png',
  headNormal: '../models/rocketbox/m08/m014_head_normal.png',
  opacityColor: '../models/rocketbox/m08/m014_opacity_color.png',
};

function makeAdapter(model) {
  if (model && model.kind === 'portrait') {
    // 2.5D：接口与另两种一致，直接透传
    return model;
  }
  if (model && model.kind === 'mesh') {
    // 单图生 3D + Blender 规整出来的 GLB 头像：形变在 mesh.js 的顶点着色器里
    return model;
  }
  if (model && model.kind === 'fbx') {
    return {
      kind: 'fbx',
      raw: model,
      head: model.head,
      chest: model.spine,
      setLip(v) { model.applyVisemes(v); },
      setBlink(v) { model.applyBlink(v); },
      setSmile(v) { model.applySmile(v); },
      setBrow(v) { model.applyBrow(v); },
      update(dt) { model.update(dt); },
    };
  }
  // VRM
  const vrm = model;
  return {
    kind: 'vrm',
    raw: vrm,
    head: vrm.humanoid && vrm.humanoid.getNormalizedBoneNode('head'),
    chest: vrm.humanoid && vrm.humanoid.getNormalizedBoneNode('chest'),
    setLip(v) {
      setExpr(vrm, EXPR.aa, v.aa || 0);
      setExpr(vrm, EXPR.ih, v.ih || 0);
      setExpr(vrm, EXPR.ou, v.ou || 0);
    },
    setBlink(v) { setExpr(vrm, EXPR.blink, v); },
    setSmile(v) { setExpr(vrm, EXPR.happy, v); },
    setBrow() {},
    update(dt) { vrm.update(dt); },
  };
}

// ───────────────────────────────── 模型载入
const bootBar = $('bootBar');

function onLoaded(model, box) {
  state.model = model;
  state.adapter = makeAdapter(model);
  scene.add(model.scene);

  if (model.kind === 'portrait') {
    // 2.5D：照片要像素直通，关掉色调映射与色彩空间转换，否则偏暗发红
    renderer.toneMapping = THREE.NoToneMapping;
    renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
    // 相机拉近到只露出上半身（裁掉底部衬衫，突出脸）
    camera.position.set(0, 0.06, 1.55);
    camera.lookAt(0, 0.06, 0);
  } else {
    // 3D：按模型高度自动框“半身偏全身”
    const size = box.getSize(new THREE.Vector3());
    const h = size.y || 1.75;
    const headY = box.max.y - h * 0.06;
    camera.position.set(0, headY - h * 0.20, Math.max(1.7, h * 1.55));
    camera.lookAt(0, headY - h * 0.33, 0);
  }

  $('boot').classList.add('hide');
  setState('idle');
  pollEvents();
  idleLoopSpeech();
  console.log('[DH] 模型已加载', {
    kind: model.kind,
    head: state.adapter.head ? state.adapter.head.name : null,
  });
}

function loadModel(url) {
  if (url === 'portrait' || /\.(png|jpe?g|webp)$/i.test(url)) {
    const base = url === 'portrait' ? './assets/' : url.replace(/[^/]+$/, '');
    const imgUrl = url === 'portrait' ? './assets/portrait.png' : url;
    fetch(base + 'face.json')
      .then((r) => r.json())
      .then((fj) => {
        const aspect = (fj.width || 717) / (fj.height || 1139);
        const p = createPortrait({ url: imgUrl, face: fj.norm, aspect });
        const box = new THREE.Box3().setFromObject(p.scene);
        onLoaded(p, box);
      })
      .catch((e) => fail('2.5D 人像加载失败：' + (e && e.message ? e.message : e)));
    return;
  }
  if (/\.fbx(\?|$)/i.test(url)) {
    loadFBX(url, { textures: FBX_TEX }).then((m) => {
      const box = new THREE.Box3().setFromObject(m.scene);
      onLoaded(m, box);
      window.DH && (window.DH.model = m);
    }).catch((e) => fail('FBX 加载失败：' + (e && e.message ? e.message : e)));
    return;
  }
  if (/\.glb(\?|$)/i.test(url)) {
    // 单图生 3D 资产（models/dh01/*.glb）
    loadMeshAvatar(url).then((m) => {
      const box = new THREE.Box3().setFromObject(m.scene);
      onLoaded({ ...m, scene: m.scene }, box);
      window.DH && (window.DH.model = m);
    }).catch((e) => fail('GLB 头像加载失败：' + (e && e.message ? e.message : e)));
    return;
  }
  // VRM
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));
  loader.load(
    url,
    (gltf) => {
      const vrm = gltf.userData.vrm;
      if (!vrm) { fail('模型里没有 VRM 数据（userData.vrm 为空）'); return; }
      try { VRMUtils.removeUnnecessaryVertices(gltf.scene); } catch (e) {}
      try { VRMUtils.combineSkeletons(gltf.scene); } catch (e) {}
      try { VRMUtils.rotateVRM0(vrm); } catch (e) {}
      vrm.scene.traverse((o) => {
        o.frustumCulled = false;
        if (o.isMesh) { o.castShadow = false; o.receiveShadow = false; }
      });
      applyRestPose(vrm);
      const box = new THREE.Box3().setFromObject(vrm.scene);
      onLoaded(vrm, box);
    },
    (e) => { if (e.total) bootBar.style.width = Math.round((e.loaded / e.total) * 100) + '%'; },
    (err) => fail('模型加载失败：' + (err && err.message ? err.message : err)),
  );
}

loadModel(MODEL_URL);

function fail(msg) {
  const b = $('boot');
  b.innerHTML = '<div style="text-align:center;color:#ff9a9a;max-width:70vw;line-height:1.7">' + msg +
    '<br><span style="color:#7f93a6;font-size:11px">检查 models/ 下是否有 .vrm 文件</span></div>';
  console.error('[DH]', msg);
}

function exprNames(vrm) {
  try {
    const m = vrm.expressionManager;
    return m ? Object.keys(m.expressionMap || {}) : [];
  } catch { return []; }
}

/** 兼容 VRM0(大写) / VRM1(小写) 的表情名 */
function setExpr(vrm, names, value) {
  const m = vrm.expressionManager;
  if (!m) return false;
  for (const n of names) {
    if (m.getExpression(n) || (m.expressionMap && m.expressionMap[n])) {
      m.setValue(n, value);
      return true;
    }
  }
  return false;
}

const EXPR = {
  aa: ['aa', 'A'], ih: ['ih', 'I'], ou: ['ou', 'U'], ee: ['ee', 'E'], oh: ['oh', 'O'],
  blink: ['blink', 'Blink'],
  happy: ['happy', 'Joy'], relaxed: ['relaxed', 'Fun'], surprised: ['surprised', 'Surprised'],
  angry: ['angry', 'Angry'],
};

// ───────────────────────────────── 站姿（T-pose → 自然垂臂）
function applyRestPose(vrm) {
  const B = (n) => vrm.humanoid && vrm.humanoid.getNormalizedBoneNode(n);
  const set = (n, x, y, z) => {
    const b = B(n);
    if (!b) return;
    b.rotation.set(x, y, z);
  };
  // 上臂：左臂绕 Z 负转、右臂绕 Z 正转，从水平降到垂臂（约 68°）
  set('leftUpperArm', 0.10, 0.0, -1.18);
  set('rightUpperArm', 0.10, 0.0, 1.18);
  // 小臂：略微内收并前屈，避免笔直
  set('leftLowerArm', 0.0, -0.18, -0.30);
  set('rightLowerArm', 0.0, 0.18, 0.30);
  // 手掌自然下垂
  set('leftHand', 0.0, 0.0, -0.10);
  set('rightHand', 0.0, 0.0, 0.10);
  // 肩部放松
  set('leftShoulder', 0.0, 0.0, -0.06);
  set('rightShoulder', 0.0, 0.0, 0.06);
}

// ───────────────────────────────── 渲染循环
function resize() {
  const w = canvas.clientWidth || window.innerWidth;
  const h = canvas.clientHeight || window.innerHeight;
  if (canvas.width !== Math.floor(w * renderer.getPixelRatio()) ||
      canvas.height !== Math.floor(h * renderer.getPixelRatio())) {
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
}
window.addEventListener('resize', resize);

function tick() {
  requestAnimationFrame(tick);
  resize();
  const dt = Math.min(state.clock.getDelta(), 0.05);
  const t = state.clock.elapsedTime;
  const A = state.adapter;

  if (A) {
    // —— 呼吸
    state.breath += dt;
    const br = Math.sin(state.breath * 1.15) * 0.5 + 0.5;
    if (A.chest) A.chest.rotation.x = -0.012 + br * 0.020;
    const spine = A.kind === 'vrm' && A.raw.humanoid
      ? A.raw.humanoid.getNormalizedBoneNode('spine') : null;
    if (spine) spine.rotation.x = 0.008 - br * 0.008;

    // —— 头部微动（待机）
    if (A.head) {
      state.idleHead += dt;
      const sway = state.talking ? 0.6 : 1.0;
      // FBX(Bip01) 与 VRM 的轴向不同，幅度分开给
      const k = A.kind === 'fbx' ? 0.55 : 1.0;
      A.head.rotation.y = Math.sin(state.idleHead * 0.42) * 0.055 * sway * k;
      A.head.rotation.z = Math.sin(state.idleHead * 0.31 + 1.2) * 0.026 * sway * k;
      A.head.rotation.x = Math.sin(state.idleHead * 0.27 + 0.6) * 0.020 * k;
    }

    // —— 视线跟随（仅 VRM 有 lookAt 实现）
    const vrm = A.kind === 'vrm' ? A.raw : null;
    if (vrm && vrm.lookAt) {
      state.look.x = lerp(state.look.x, state.targetLook.x, 1 - Math.pow(0.001, dt));
      state.look.y = lerp(state.look.y, state.targetLook.y, 1 - Math.pow(0.001, dt));
      vrm.lookAt.target = null;
      if (vrm.lookAt.applier) {
        vrm.lookAt.applier.applyYawPitch(state.look.x * 0.55, state.look.y * 0.40);
      }
    }

    // —— 眨眼
    updateBlink(dt, A);

    // —— 口型
    updateLip(dt, A, t);

    // —— 表情基调
    // 「阳光开朗」：默认带一点笑，说话时也保持，报警时才收起来
    if (state.mood === 'happy') A.setSmile(0.55);
    else if (state.mood === 'surprised') { A.setSmile(0.15); A.setBrow(0.5); }
    else A.setSmile(0.32);

    A.update(dt);
  }

  renderer.render(scene, camera);
}

function updateBlink(dt, A) {
  state.blinkTimer -= dt;
  if (state.blinkPhase === 0 && state.blinkTimer <= 0) {
    state.blinkPhase = 1;
    state.blinkValue = 0;
    state.blinkTimer = 0.075;
  } else if (state.blinkPhase === 1) {
    state.blinkValue = clamp(state.blinkValue + dt / 0.075, 0, 1);
    if (state.blinkValue >= 1) { state.blinkPhase = 2; state.blinkTimer = 0.055; }
  } else if (state.blinkPhase === 2) {
    state.blinkValue = clamp(state.blinkValue - dt / 0.055, 0, 1);
    if (state.blinkValue <= 0) {
      state.blinkPhase = 0;
      state.blinkTimer = Math.random() < 0.18 ? 0.16 : 2.4 + Math.random() * 3.8;
    }
  }
  A.setBlink(state.blinkValue);
}

function updateLip(dt, A, t) {
  const energy = state.voiceOn && state.talking ? readEnergy() : 0;
  let target = energy;
  if (state.talking && !energy && state.voiceOn && currentAudio && currentAudio.paused) target = 0;
  if (state.talking && !state.voiceOn) {
    // 静音模式：用低频噪声模拟说话节奏，保证嘴在动
    target = clamp(0.55 + 0.30 * Math.sin(t * 11.3) * Math.sin(t * 3.7) + 0.10 * Math.sin(t * 23.1), 0, 1);
  }
  state.lipTarget = target;
  state.lip = lerp(state.lip, state.lipTarget, 1 - Math.pow(0.0009, dt));

  const L = state.lip;
  if (L < 0.035) {
    A.setLip({ aa: 0, ih: 0, ou: 0 });
    return;
  }
  // 用能量抖动分配元音，避免嘴型单调
  const j = Math.sin(t * 7.7 + state.lipSeed) * 0.5 + 0.5;
  const j2 = Math.sin(t * 4.3 + state.lipSeed * 1.7) * 0.5 + 0.5;
  A.setLip({
    aa: clamp(L * 1.45 * (0.72 + 0.5 * j), 0, 1),
    ih: clamp(L * 0.5 * j2, 0, 0.7),
    ou: clamp(L * 0.4 * (1 - j2), 0, 0.6),
  });
}

// ───────────────────────────────── 鼠标/触摸视线跟随
window.addEventListener('pointermove', (e) => {
  const nx = (e.clientX / window.innerWidth) * 2 - 1;
  const ny = (e.clientY / window.innerHeight) * 2 - 1;
  state.targetLook.set(clamp(nx, -1, 1), clamp(-ny, -1, 1));
});
window.addEventListener('pointerleave', () => state.targetLook.set(0, 0));

// ───────────────────────────────── 字幕（打字机）
function showCaption(text, who) {
  const box = $('caption');
  const el = $('capText');
  if (!text) { box.classList.remove('on'); return; }
  clearInterval(captionTimer);
  box.classList.add('on');
  const whoEl = box.querySelector('.who');
  if (whoEl && who) whoEl.textContent = who;
  captionFull = text;
  captionIdx = 0;
  el.textContent = '';
  const caret = document.createElement('i');
  caret.className = 'caret';
  el.appendChild(caret);
  // 语速 ~ 每字 55ms，长句稍快
  const speed = text.length > 60 ? 38 : 55;
  captionTimer = setInterval(() => {
    captionIdx++;
    if (captionIdx >= captionFull.length) {
      el.textContent = captionFull;
      clearInterval(captionTimer);
      if (caret.parentNode) caret.parentNode.removeChild(caret);
      return;
    }
    el.textContent = captionFull.slice(0, captionIdx);
    el.appendChild(caret);
  }, speed);
}

function hideCaption(delay = 1400) {
  setTimeout(() => {
    if (!state.talking) $('caption').classList.remove('on');
  }, delay);
}

// ───────────────────────────────── 语音播放
function playVoice(url) {
  if (!state.voiceOn) return Promise.resolve();
  return new Promise((resolve) => {
    try {
      if (audioSrcNode) { try { audioSrcNode.disconnect(); } catch {} audioSrcNode = null; }
      if (currentAudio) { try { currentAudio.pause(); } catch {} }
      const a = new Audio();
      a.src = url;
      a.preload = 'auto';
      currentAudio = a;
      ensureAudioCtx();
      audioSrcNode = audioCtx.createMediaElementSource(a);
      audioSrcNode.connect(analyser);
      a.addEventListener('ended', () => resolve(), { once: true });
      a.addEventListener('error', () => { console.warn('[DH] 语音播放失败', url); resolve(); }, { once: true });
      a.play().catch((e) => { console.warn('[DH] 播放被拒', e); resolve(); });
      // 兜底：音频时长异常时也能推进
      setTimeout(resolve, 20000);
    } catch (e) {
      console.warn('[DH] playVoice 异常', e);
      resolve();
    }
  });
}

// ───────────────────────────────── 对外 API
let speakChain = Promise.resolve();

function say(opts) {
  const o = typeof opts === 'string' ? { text: opts } : (opts || {});
  const text = o.text || '';
  const audio = o.audio || '';
  const hold = o.hold || 0;
  const who = o.who || '5号选手';
  if (o.state) setState(o.state);
  if (o.mood) state.mood = o.mood;

  speakChain = speakChain.then(async () => {
    state.talking = true;
    if (state.busy) setDot('#39b6ff');
    showCaption(text, who);
    const t0 = Date.now();
    if (audio) await playVoice(audio);
    // 语音结束到字幕收尾的过渡
    const minHold = text.length * (text.length > 60 ? 38 : 55);
    const wait = Math.max(minHold - (Date.now() - t0), hold * 1000);
    if (wait > 0) await new Promise((r) => setTimeout(r, wait));
    state.talking = false;
    state.lip = 0;
    if (!audio) await new Promise((r) => setTimeout(r, 220));
    hideCaption(900);
  }).catch((e) => console.warn('[DH] say 异常', e));

  return speakChain;
}

function setState(s) {
  const map = {
    idle: ['待机', '#7a8ba0'], busy: ['工作中', '#39b6ff'], listen: ['聆听中', '#3ddc97'],
    alert: ['异常', '#ff6b6b'], speak: ['播报中', '#3ddc97'],
  };
  const [label, color] = map[s] || map.idle;
  $('state').textContent = label;
  setDot(color);
  document.body.classList.toggle('state-alert', s === 'alert');
  state.busy = (s === 'busy');
  if (s === 'alert') {
    $('alert').classList.add('on');
    state.mood = 'surprised';
  } else if (s !== 'busy') {
    $('alert').classList.remove('on');
    state.mood = 'relaxed';
  }
  if (s === 'listen') state.mood = 'happy';
}

function setDot(color) {
  const d = $('dot');
  d.style.background = color;
  d.style.color = color;
}

function setVoice(on) {
  state.voiceOn = !!on;
  if (!state.voiceOn && currentAudio) { try { currentAudio.pause(); } catch {} }
  return state.voiceOn;
}

function alarm(on, why) {
  if (on) {
    setState('alert');
    if (why) $('alert').textContent = why;
    say({ text: '警报，模型性能异常。', audio: '/voice/alert_abnormal.mp3',
          state: 'alert', mood: 'surprised' });
  } else {
    setState('idle');
    $('alert').classList.remove('on');
    document.body.classList.remove('state-alert');
    state.mood = 'relaxed';
  }
}

function hide() { document.getElementById('stage').style.opacity = '0'; }
function show() { document.getElementById('stage').style.opacity = '1'; }

// ───────────────────────────────── 事件通道（SSE → 指令）
function pollEvents() {
  try {
    const es = new EventSource(SSE_URL);
    es.addEventListener('dh', (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      handleMessage(m);
    });
    es.onerror = () => { /* EventSource 自带重连 */ };
    console.log('[DH] 已连接事件流', SSE_URL);
  } catch (e) {
    console.warn('[DH] SSE 连接失败，转为本地模式', e);
  }
}

function handleMessage(m) {
  switch (m.type) {
    case 'say': say(m); break;
    case 'state': setState(m.state); break;
    case 'alarm': alarm(!!m.on, m.why); break;
    case 'health': updateHealthHud(m.health); break;
    case 'voice': setVoice(!!m.on); break;
    case 'hide': hide(); break;
    case 'show': show(); break;
    case 'mood': state.mood = m.mood; break;
    default: console.log('[DH] 未知消息', m);
  }
}

/** 把后端巡检到的模型健康度画到状态条上。
 *  注意：这里**只更新状态条与光晕**，不弹报警横幅 ——
 *  横幅留给台词（alert_abnormal）和控制台按钮触发，
 *  否则真实数据一动就红屏，正片演示会被打断。状态条仍如实显示异常。 */
function updateHealthHud(h) {
  if (!h) return;
  const c = h.confidence || {};
  const degraded = !!h.degraded;
  const dot = $('dot');
  dot.style.background = degraded ? '#ff9b3d' : '#3ddc97';
  dot.style.color = dot.style.background;
  $('state').textContent = degraded
    ? `模型异常 · 置信度 ${c.recent ?? '-'}`
    : `正常 · 置信度 ${c.recent ?? '-'}`;
}

/** 浏览器要求首次交互后才允许出声。若启动参数已放开 autoplay（AudioContext 直接 running），
 *  就自动去掉这层提示，省得挡住人像；否则保留，点一下即永久解锁。 */
function installAudioUnlock() {
  const tryAutoUnlock = () => {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      if (ctx.state === 'running') {
        ensureAudioCtx();
        ctx.close();
        return true;
      }
      ctx.close();
    } catch (e) { /* 忽略 */ }
    return false;
  };

  if (tryAutoUnlock()) return;   // 已允许自动播放，不再打扰

  const el = document.createElement('div');
  el.id = 'unlock';
  el.textContent = '点击任意位置启用语音';
  el.style.cssText = 'position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);' +
    'padding:12px 22px;border-radius:12px;background:rgba(12,22,32,.88);' +
    'border:1px solid rgba(90,160,200,.4);color:#bfe6ff;font-size:14px;letter-spacing:1px;' +
    'cursor:pointer;z-index:60;backdrop-filter:blur(6px);';
  document.body.appendChild(el);
  const unlock = () => {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      g.gain.value = 0.0001;      // 静音振荡：只为拿一次交互授权
      o.connect(g); g.connect(ctx.destination);
      o.start(); o.stop(ctx.currentTime + 0.02);
      ctx.close();
    } catch (e) { /* 忽略 */ }
    ensureAudioCtx();
    el.remove();
    window.removeEventListener('pointerdown', unlock);
  };
  window.addEventListener('pointerdown', unlock, { once: true });
  el.addEventListener('pointerdown', unlock, { once: true });
}

// 空闲时无人操作，眼神回正 + 轻微点头，制造“在等你”的感觉
function idleLoopSpeech() {
  setInterval(() => {
    if (!state.talking && !state.busy) {
      state.targetLook.set(Math.sin(Date.now() / 5300) * 0.35, Math.sin(Date.now() / 7700) * 0.18);
    }
  }, 2600);
}

window.DH = { say, setState, setVoice, alarm, hide, show, updateHealthHud,
              model: null, state, scene, renderer, camera };
installAudioUnlock();
console.log('[DH] 前端就绪');
tick();
