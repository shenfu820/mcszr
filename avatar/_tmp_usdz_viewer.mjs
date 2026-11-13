import * as THREE from 'three';
import { USDLoader } from 'three/addons/loaders/USDLoader.js';

const q = new URLSearchParams(location.search);
const yaw = parseFloat(q.get('yaw') ?? '0');
const size = 900;

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
renderer.setSize(size, size);
renderer.setPixelRatio(1);
renderer.outputColorSpace = THREE.SRGBColorSpace;
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1a1c22);

const cam = new THREE.PerspectiveCamera(32, 1, 0.01, 500);
cam.position.set(0, 0, 4);

scene.add(new THREE.HemisphereLight(0xffffff, 0x30343c, 1.6));
const key = new THREE.DirectionalLight(0xffffff, 2.2);
key.position.set(1.4, 2.0, 2.6);
scene.add(key);
const rim = new THREE.DirectionalLight(0x8fb6ff, 1.0);
rim.position.set(-2.0, 1.0, -1.6);
scene.add(rim);

const grid = new THREE.GridHelper(6, 12, 0x333333, 0x24262b);
grid.position.y = -1.6;
scene.add(grid);

const info = document.createElement('div');
info.style.cssText = 'position:fixed;left:10px;top:10px;color:#ddd;font:13px monospace;white-space:pre';
document.body.appendChild(info);

new USDLoader().load('model.usdz', (root) => {
  const box = new THREE.Box3().setFromObject(root);
  const s = box.getSize(new THREE.Vector3());
  const c = box.getCenter(new THREE.Vector3());
  const radius = Math.max(s.x, s.y, s.z);
  root.position.sub(c);                       // 居中
  const holder = new THREE.Group();
  holder.add(root);
  holder.rotation.y = yaw;
  scene.add(holder);

  const dist = radius / (2 * Math.tan((cam.fov * Math.PI / 180) / 2)) * 1.35;
  cam.position.set(0, 0, dist);
  cam.lookAt(0, 0, 0);
  cam.updateProjectionMatrix();

  let meshes = 0, verts = 0, mats = new Set();
  root.traverse((o) => {
    if (o.isMesh) {
      meshes++;
      const g = o.geometry;
      verts += g.attributes.position ? g.attributes.position.count : 0;
      if (o.material) mats.add(o.material.type + (o.material.map ? '+map' : ''));
    }
  });
  info.textContent = [
    'size: ' + s.toArray().map((v) => v.toFixed(3)).join(' x '),
    'meshes: ' + meshes + '  verts: ' + verts,
    'materials: ' + Array.from(mats).join(', '),
  ].join('\n');
  renderer.render(scene, cam);
  window.__ready = info.textContent;
}, undefined, (e) => {
  info.textContent = 'load error: ' + e;
  window.__ready = 'error';
});

function tick() { renderer.render(scene, cam); requestAnimationFrame(tick); }
tick();
