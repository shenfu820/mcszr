/**
 * 数字人 · 3D 网格形象适配器（GLB）
 * =================================================================
 * 干什么：把「单图生 3D + Blender 规整」得到的 GLB 头像接进现有渲染管线，
 *        并给出与 VRM / FBX / 2.5D 完全一致的适配接口，上层 tick 不用改。
 *
 * 为什么用着色器而不是形态键：
 *   model.ckpt 走的是 TripoSR（单图生 3D），产物是一张静态网格，
 *   没有骨骼、没有表情 blendshape。要在前端让它"张嘴说话"，
 *   最省的做法是在顶点着色器里按区域做形变：
 *     · 下颌：以嘴角高度为轴、朝前的那半边绕 X 轴旋转（开口）
 *     · 眨眼：眼睛高度附近沿 Y 往眼线挤
 *     · 微笑：嘴部轻微上提
 *   比在 Blender 里烘形态键更灵活——阈值、幅度都能用 URL 参数现场调。
 *
 * 适配接口（与 main.js: makeAdapter 对齐）：
 *   kind, raw, head, chest, setLip({aa,ih,ou}), setBlink(v), setSmile(v), setBrow(v), update(dt)
 *
 * URL 参数（都可不写）：
 *   ?model=../models/dh01/dh01_head.glb   指定资产
 *   &yaw=180                              朝向修正（度）。资产导出后如果背对镜头就加 180
 *   &mouth=0.32&eye=0.58                  区域高度，按头像总高的比例（0=脚底 1=头顶）
 */
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const qs = new URLSearchParams(location.search);

const VERT_PARS = `
uniform float uLip;
uniform float uBlink;
uniform float uSmile;
uniform float uMouthY;
uniform float uEyeY;
uniform float uJawFade;
uniform float uEyeBand;
uniform float uPivotZ;
uniform float uFrontLo;
uniform float uFrontHi;
uniform float uJawAngle;
uniform float uBlinkSquash;
`;

const VERT_BODY = `
  vec3 transformed = vec3( position );
  // 只在脸的正面做形变（背面顶点 z 很小，权重自然衰减到 0）
  float frontW = smoothstep( uFrontLo, uFrontHi, transformed.z );

  // —— 下颌开合：绕 (uMouthY, uPivotZ) 这根 X 轴转
  float jawW = smoothstep( uMouthY + uJawFade, uMouthY - uJawFade, transformed.y ) * frontW;
  float ang = uLip * uJawAngle * jawW;
  float cs = cos( ang ), sn = sin( ang );
  vec2 rel = vec2( transformed.y - uMouthY, transformed.z - uPivotZ );
  transformed.y = uMouthY + ( rel.x * cs - rel.y * sn );
  transformed.z = uPivotZ + ( rel.x * sn + rel.y * cs );

  // —— 微笑：嘴角轻微上提（开口时减弱）
  float smileW = ( 1.0 - jawW ) * frontW;
  transformed.y += uSmile * 0.006 * smileW;

  // —— 眨眼：眼线上下一小段往眼线挤
  float d = ( transformed.y - uEyeY ) / uEyeBand;
  float eyeW = exp( - d * d ) * frontW;
  transformed.y -= ( transformed.y - uEyeY ) * uBlink * uBlinkSquash * eyeW;
`;

export function loadMeshAvatar(url, opt = {}) {
  const yaw = Number(qs.get('yaw') ?? opt.yaw ?? 0);
  return new Promise((resolve, reject) => {
    new GLTFLoader().load(
      url,
      (gltf) => {
        const inner = gltf.scene;
        // 外层 pivot 专门放"朝向修正"，内层 headPivot 交给 main.js 的待机头部微动。
        // 注意：main.js 的 tick 每帧会写 A.head.rotation.y，所以朝向不能挂在 head 上。
        const headPivot = new THREE.Group();
        headPivot.add(inner);
        const pivot = new THREE.Group();
        pivot.rotation.y = THREE.MathUtils.degToRad(yaw);
        pivot.add(headPivot);
        pivot.updateMatrixWorld(true);

        // 贴地平 + 水平居中（Blender 里做过，这里兜底，防止资产被挪过）
        let box = new THREE.Box3().setFromObject(inner);
        const size = box.getSize(new THREE.Vector3());
        const ctr = box.getCenter(new THREE.Vector3());
        inner.position.set(-ctr.x, -box.min.y, -ctr.z);
        pivot.updateMatrixWorld(true);
        box = new THREE.Box3().setFromObject(inner);

        const h = box.max.y - box.min.y;
        const zMin = box.min.z, zMax = box.max.z;
        const mouthFrac = Number(qs.get('mouth') ?? opt.mouth ?? 0.32);
        const eyeFrac = Number(qs.get('eye') ?? opt.eye ?? 0.58);

        const uniforms = {
          uLip: { value: 0 },
          uBlink: { value: 0 },
          uSmile: { value: 0 },
          uMouthY: { value: box.min.y + h * mouthFrac },
          uEyeY: { value: box.min.y + h * eyeFrac },
          uJawFade: { value: h * 0.10 },
          uEyeBand: { value: h * 0.055 },
          uPivotZ: { value: zMin + (zMax - zMin) * 0.28 },
          uFrontLo: { value: zMin + (zMax - zMin) * 0.10 },
          uFrontHi: { value: zMin + (zMax - zMin) * 0.45 },
          uJawAngle: { value: Number(qs.get('jaw') ?? 0.75) },
          uBlinkSquash: { value: Number(qs.get('blink') ?? 0.6) },
        };

        inner.traverse((o) => {
          if (!o.isMesh) return;
          o.frustumCulled = false;
          const mats = Array.isArray(o.material) ? o.material : [o.material];
          mats.forEach((m) => {
            if (!m) return;
            m.vertexColors = !!(o.geometry && o.geometry.getAttribute('color'));
            m.onBeforeCompile = (shader) => {
              Object.assign(shader.uniforms, uniforms);
              shader.vertexShader = shader.vertexShader
                .replace('#include <common>', '#include <common>\n' + VERT_PARS)
                .replace('#include <begin_vertex>', VERT_BODY);
            };
            m.needsUpdate = true;
          });
        });

        const adapter = {
          kind: 'mesh',
          raw: gltf,
          scene: pivot,
          head: headPivot,        // 让 main.js 的待机头部微动作用在整个头像上
          chest: null,
          uniforms,
          size,
          setLip(v) {
            const aa = v.aa || 0, ou = v.ou || 0, ih = v.ih || 0;
            uniforms.uLip.value = clamp(aa * 0.95 + ou * 0.55 + ih * 0.25, 0, 1);
          },
          setBlink(v) { uniforms.uBlink.value = clamp(v, 0, 1); },
          setSmile(v) { uniforms.uSmile.value = clamp(v, 0, 1); },
          setBrow() { },
          update() { },
          report() {
            return {
              kind: 'mesh', yaw,
              height: +h.toFixed(4),
              bbox: { min: box.min.toArray().map((n) => +n.toFixed(3)), max: box.max.toArray().map((n) => +n.toFixed(3)) },
              tris: (() => { let n = 0; inner.traverse((o) => { if (o.isMesh && o.geometry.index) n += o.geometry.index.count / 3; else if (o.isMesh) n += o.geometry.attributes.position.count / 3; }); return Math.round(n); })(),
              mouthY: +uniforms.uMouthY.value.toFixed(4),
              eyeY: +uniforms.uEyeY.value.toFixed(4),
            };
          },
        };
        console.log('[DH] mesh 头像已就绪', adapter.report());
        resolve(adapter);
      },
      undefined,
      (err) => reject(err),
    );
  });
}

