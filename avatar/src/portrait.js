/**
 * 2.5D 照片数字人
 * ================
 * 不切图、不建模：把整张人像做成一整块细分平面，在**顶点着色器里做局部 UV 变形**，
 * 得到“张嘴 / 眨眼”的效果，因此没有拼接缝。
 *
 * 原理：
 *   - 嘴：嘴部椭圆内的顶点按权重把下唇区域向下拉（模拟张口）
 *   - 眼：眼睑椭圆内的顶点向眼睛中线压缩（模拟闭眼）
 *   - 边缘：片元里做四边渐隐，让矩形人像融进界面
 *
 * 对外接口与 VRM/FBX 适配层保持一致，所以 main.js 的动画逻辑不用改。
 */
import * as THREE from 'three';

const VERT = `
  uniform float uLip;
  uniform float uBlink;
  uniform vec2  uMouthC;      // 嘴中心 uv
  uniform vec2  uMouthR;      // 嘴半径 uv
  uniform vec2  uEyeLC;
  uniform vec2  uEyeRC;
  uniform vec2  uEyeR;
  varying vec2 vUv;
  varying float vLipMark;

  void main() {
    vec2 uv = uv;
    vLipMark = 0.0;

    // ---- 张嘴：椭圆权重 + 只拉下唇
    vec2 md = (uv - uMouthC) / max(uMouthR, vec2(1e-5));
    float mdist = length(md);
    float mw = smoothstep(1.0, 0.0, mdist);
    if (mw > 0.001 && uLip > 0.001) {
      // 下唇（uv.y 低于嘴中心）权重更高
      float lower = smoothstep(uMouthC.y + uMouthR.y * 0.15, uMouthC.y - uMouthR.y, uv.y);
      uv.y -= uLip * mw * lower * uMouthR.y * 1.25;
      vLipMark = mw * lower;
    }

    // ---- 眨眼：眼睑向中线压缩
    if (uBlink > 0.001) {
      vec2 ed = (uv - uEyeLC) / max(uEyeR, vec2(1e-5));
      float ew = smoothstep(1.0, 0.0, length(ed));
      uv.y = mix(uv.y, uEyeLC.y, uBlink * ew * 0.92);

      vec2 ed2 = (uv - uEyeRC) / max(uEyeR, vec2(1e-5));
      float ew2 = smoothstep(1.0, 0.0, length(ed2));
      uv.y = mix(uv.y, uEyeRC.y, uBlink * ew2 * 0.92);
    }

    // ---- 整体呼吸：轻微纵向伸缩（几乎看不出，但让人“活着”）
    uv.y = mix(uv.y, 0.5, 0.0);

    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const FRAG = `
  uniform sampler2D uTex;
  uniform float uTime;
  uniform float uFade;
  uniform float uAspect;
  varying vec2 vUv;
  varying float vLipMark;

  void main() {
    vec2 uv = vUv;

    // 边缘渐隐：左右窄一点、顶部稍宽、底部更多（便于和字幕衔接）
    float fx = smoothstep(0.0, 0.055 * uAspect, uv.x) * smoothstep(1.0, 1.0 - 0.055 * uAspect, uv.x);
    float fy = smoothstep(0.0, 0.035, uv.y) * smoothstep(1.0, 0.90, uv.y);
    float fade = fx * fy;

    // 轻微提亮嘴巴内部，避免拉伸后偏暗
    vec3 col = texture2D(uTex, uv).rgb;
    col += vLipMark * vec3(0.06, 0.02, 0.02);

    gl_FragColor = vec4(col, fade * uFade);
  }
`;

/**
 * @param {object} opt { url, face, aspect }
 * face: { mouth:[u,v,w,h], eyes:[[u,v,w,h],[..]] } 均为归一化（v 轴向上）
 */
export function createPortrait(opt) {
  const { url, face, aspect = 0.63 } = opt;

  const tex = new THREE.TextureLoader().load(url);
  // 关键：ShaderMaterial 不参与 three 的自动色彩管理。
  // 这里让纹理保持"原始像素"，同时在 main.js 里把 portrait 模式的
  // toneMapping 关掉、outputColorSpace 设为 Linear，做到像素直通，
  // 否则照片会被 ACES 压暗、颜色发红。
  tex.colorSpace = THREE.NoColorSpace;
  tex.minFilter = THREE.LinearFilter;
  tex.generateMipmaps = false;

  const mouth = face.mouth || [0.5, 0.45, 0.2, 0.09];
  const eyes = face.eyes || [[0.35, 0.72, 0.1, 0.04], [0.55, 0.72, 0.1, 0.04]];

  const uniforms = {
    uTex: { value: tex },
    uTime: { value: 0 },
    uFade: { value: 0 },
    uLip: { value: 0 },
    uBlink: { value: 0 },
    uMouthC: { value: new THREE.Vector2(mouth[0] + mouth[2] / 2, mouth[1] + mouth[3] / 2) },
    uMouthR: { value: new THREE.Vector2(mouth[2] * 0.62, mouth[3] * 0.78) },
    uEyeLC: { value: new THREE.Vector2(eyes[0][0] + eyes[0][2] / 2, eyes[0][1] + eyes[0][3] / 2) },
    uEyeRC: { value: new THREE.Vector2(eyes[1][0] + eyes[1][2] / 2, eyes[1][1] + eyes[1][3] / 2) },
    uEyeR: { value: new THREE.Vector2(eyes[0][2] * 0.78, eyes[0][3] * 1.5) },
    uAspect: { value: 1 / aspect },
  };

  const geo = new THREE.PlaneGeometry(1, 1, 160, 160);
  const mat = new THREE.ShaderMaterial({
    vertexShader: VERT,
    fragmentShader: FRAG,
    uniforms,
    transparent: true,
    depthWrite: false,
  });

  const mesh = new THREE.Mesh(geo, mat);
  mesh.scale.set(aspect, 1, 1);

  const state = { lip: 0, blink: 0, smile: 0, t: 0, appear: 0 };

  return {
    kind: 'portrait',
    scene: mesh,
    head: null,      // 2.5D 没有骨骼
    chest: null,
    setLip(v) { state.lip = Math.max(0, Math.min(1, v.aa || 0)); },
    setBlink(v) { state.blink = Math.max(0, Math.min(1, v)); },
    setSmile(v) { state.smile = v; },   // 照片人像不做嘴角变形，保留接口
    setBrow() {},
    update(dt) {
      state.t += dt;
      state.appear = Math.min(1, state.appear + dt * 1.6);
      uniforms.uTime.value = state.t;
      uniforms.uFade.value = state.appear;
      // 口型/眨眼做一点点时间平滑，避免抖
      const cur = uniforms.uLip.value;
      uniforms.uLip.value = cur + (state.lip - cur) * Math.min(1, dt * 22);
      const cb = uniforms.uBlink.value;
      uniforms.uBlink.value = cb + (state.blink - cb) * Math.min(1, dt * 30);

      // 呼吸/待机浮动：位移 + 极轻微倾斜
      const breathe = Math.sin(state.t * 1.1) * 0.004;
      mesh.position.y = breathe;
      mesh.rotation.z = Math.sin(state.t * 0.42) * 0.006;
      mesh.rotation.y = Math.sin(state.t * 0.31) * 0.02;
    },
  };
}
