/**
 * FBX 加载探针：只验证「能不能解析 + 表情名认不认得出 + 贴图绑不绑得上」。
 * 结果写进 document.title，由 shot.mjs 读回。
 */
import * as THREE from 'three';
import { loadFBX } from './fbx.js';

const canvas = document.getElementById('gl');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);
renderer.setClearColor(0x1a2230, 1);
renderer.outputColorSpace = THREE.SRGBColorSpace;

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 200);

scene.add(new THREE.HemisphereLight(0xffffff, 0x334455, 1.4));
const key = new THREE.DirectionalLight(0xffffff, 2.2);
key.position.set(2, 4, 3);
scene.add(key);
const fill = new THREE.DirectionalLight(0xaaccff, 1.0);
fill.position.set(-3, 2, 2);
scene.add(fill);

const status = (t) => { document.getElementById('st').textContent = t; };

loadFBX('../models/rocketbox/m08/m08_facial.fbx', {
  textures: {
    bodyColor: '../models/rocketbox/m08/m014_body_color.png',
    bodyNormal: '../models/rocketbox/m08/m014_body_normal.png',
    headColor: '../models/rocketbox/m08/m014_head_color.png',
    headNormal: '../models/rocketbox/m08/m014_head_normal.png',
    opacityColor: '../models/rocketbox/m08/m014_opacity_color.png',
  },
}).then((m) => {
  window.__fbxModel = m;
  m._noNormal = new URLSearchParams(location.search).get('nonormal') === '1';
  scene.add(m.scene);

  // 把「材质 -> 实际贴图文件」打出来，确认到底是哪张图被绑上了
  const matMaps = [];
  m.scene.traverse((o) => {
    if (!(o.isMesh || o.isSkinnedMesh)) return;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    mats.forEach((mt, i) => {
      if (!mt) return;
      matMaps.push({
        idx: i,
        name: mt.name,
        map: mt.map && mt.map.image ? (mt.map.image.src || '').split('/').pop() : null,
        normal: mt.normalMap && mt.normalMap.image ? (mt.normalMap.image.src || '').split('/').pop() : null,
        flipY: mt.map ? mt.map.flipY : null,
        colorSpace: mt.map ? mt.map.colorSpace : null,
        transparent: mt.transparent,
      });
    });
  });
  console.log('MATMAP ' + JSON.stringify(matMaps));
  console.log('TEXSTATS ' + JSON.stringify(m._texStats));
  const info = m.debugInfo();

  // 取景
  const box = new THREE.Box3().setFromObject(m.scene);
  const size = box.getSize(new THREE.Vector3());
  const h = size.y || 1.75;
  const headY = box.max.y - h * 0.06;
  camera.position.set(0, headY - h * 0.18, Math.max(1.6, h * 1.7));
  camera.lookAt(0, headY - h * 0.30, 0);

  // 试驱动：张嘴 + 眨眼 + 微笑，验证 morph 真的动
  m.applyVisemes({ aa: 0.9 });
  m.applyBlink(0.0);
  m.applySmile(0.8);
  m.update(0.016);

  const sample = {};
  const sm = m.morphMeshes[0];
  if (sm) {
    const d = sm.morphTargetDictionary;
    for (const k of Object.keys(d)) {
      const tail = k.slice(k.lastIndexOf('.') + 1);
      if (/^(AA_VI_(01|10|13)|AK_(09|44)|AU_45)/i.test(tail)) {
        sample[k] = +(sm.morphTargetInfluences[d[k]] ?? 0).toFixed(3);
      }
    }
  }

  document.title = 'FBX ' + JSON.stringify({
    ok: true,
    morphMeshes: info.morphMeshes,
    bones: info.bones,
    head: info.head,
    spine: info.spine,
    groups: Object.fromEntries(Object.entries(info.groups).map(([k, v]) => [k, v.length])),
    sampleValues: sample,
    size: { x: +size.x.toFixed(2), y: +size.y.toFixed(2), z: +size.z.toFixed(2) },
    meshes: info.meshes,
    allMorphNames: info.allMorphNames.slice(0, 80),
  });
  status('OK ' + info.morphMeshes + ' morph meshes');

  function loop() {
    requestAnimationFrame(loop);
    const w = canvas.clientWidth || 800, hh = canvas.clientHeight || 900;
    if (canvas.width !== w || canvas.height !== hh) {
      renderer.setSize(w, hh, false);
      camera.aspect = w / hh;
      camera.updateProjectionMatrix();
    }
    renderer.render(scene, camera);
  }
  loop();
}).catch((e) => {
  document.title = 'FBX FAIL ' + (e && e.message ? e.message : e);
  status('FAIL ' + e);
});
