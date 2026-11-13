/**
 * FBX 模型适配层（用于 Rocketbox 写实人物）
 * ==========================================
 * 为什么单独一层：VRM 有标准化的人形骨骼与表情预设，FBX 没有。
 * Rocketbox 的命名很规整，这里把它的 morphtarget / 骨骼映射成
 * 与 VRM 一致的那套接口（aa/ih/ou/blink/smile…），上层动画代码无需分叉。
 *
 * Rocketbox 实测命名（从 m08_facial.fbx 二进制里扫出来的）：
 *   口型   AA_VI_00_Sil … AA_VI_14_U   （Oculus LipSync 15 viseme）
 *   眨眼   AK_09_EyeBlinkLeft / AK_10_EyeBlinkRight / AU_45_Blink
 *   笑脸   AK_44_MouthSmileLeft / AK_45_MouthSmileRight / HB_07_MouthSmile
 *   张口   AK_25_JawOpen / AU_26_JawDrop / SR_21_Jaw_Open
 *   挑眉   AK_03_BrowInnerUp
 * 实际 morphtarget 名可能带 `blendShape1.` 前缀，这里一律用「去前缀 + 取尾段」匹配。
 */
import * as THREE from 'three';
import { FBXLoader } from 'three/addons/loaders/FBXLoader.js';

/** 去掉 "blendShape1." 之类前缀，返回 tail 名 */
function tailName(n) {
  const i = n.lastIndexOf('.');
  return (i >= 0 ? n.slice(i + 1) : n).trim();
}

/** 在字典里找第一个匹配（大小写不敏感，包含匹配） */
function findMorph(dict, candidates) {
  const keys = Object.keys(dict);
  const norm = keys.map((k) => ({ k, t: tailName(k).toLowerCase() }));
  for (const c of candidates) {
    const cl = c.toLowerCase();
    // 先精确匹配 tail
    let hit = norm.find((x) => x.t === cl);
    if (hit) return hit.k;
    // 再包含匹配
    hit = norm.find((x) => x.t.includes(cl));
    if (hit) return hit.k;
  }
  return null;
}

// viseme 别名表：上层只认 aa/ih/ou/ee/oh/pp/ff/ss/nn，这里映射到 Rocketbox
const VISEME_ALIAS = {
  aa: ['aa_vi_10_aa', 'sr_23_mouth_ape_shape', 'ak_25_jawopen', 'au_26_jawdrop'],
  ih: ['aa_vi_12_i', 'aa_vi_11_e'],
  ou: ['aa_vi_14_u', 'aa_vi_13_o', 'ak_38_mouthpucker', 'sr_31_mouth_pout'],
  ee: ['aa_vi_11_e', 'aa_vi_12_i'],
  oh: ['aa_vi_13_o', 'ak_38_mouthpucker'],
  pp: ['aa_vi_01_pp'],
  ff: ['aa_vi_02_ff'],
  ss: ['aa_vi_07_ss'],
  nn: ['aa_vi_08_nn'],
};

const BLINK_ALIAS = {
  left: ['ak_09_eyeblinkleft', 'au_43_l_eyeclosed', 'sr_05_eye_left_blink'],
  right: ['ak_10_eyeblinkright', 'au_43_r_eyeclosed', 'sr_12_eye_right_blink'],
  both: ['au_45_blink', 'ak_09_eyeblinkleft'],
};

const SMILE_ALIAS = {
  left: ['ak_44_mouthsmileleft', 'hb_07_mouthsmile', 'sr_34_mouth_smile_left'],
  right: ['ak_45_mouthsmileright', 'hb_07_mouthsmile', 'sr_35_mouth_smile_right'],
};

const BROW_ALIAS = ['ak_03_browinnerup', 'au_01_innerbrowraiser'];

/**
 * 载入 FBX 人物，返回统一接口。
 * @param {string} url  fbx 地址
 * @param {object} opts { textures: {bodyColor, bodyNormal, headColor, headNormal} }
 */
export function loadFBX(url, opts = {}) {
  return new Promise((resolve, reject) => {
    const loader = new FBXLoader();
    loader.load(
      url,
      (obj) => {
        const root = obj;

        // Rocketbox 导出单位是厘米（实测 Male_Adult_08 高 182.57），
        // 统一归一化到 1.80 米，这样相机/灯光/取景逻辑与 VRM 完全共用。
        try {
          const box0 = new THREE.Box3().setFromObject(root);
          const h0 = box0.getSize(new THREE.Vector3()).y;
          if (h0 > 10) {
            const k = 1.80 / h0;
            root.scale.setScalar(k);
            root.updateMatrixWorld(true);
          }
        } catch (e) { /* 尺寸异常就算了，按原样渲染 */ }

        root.traverse((o) => {
          if (o.isMesh || o.isSkinnedMesh) {
            o.frustumCulled = false;
            o.castShadow = false;
            o.receiveShadow = false;
            // Rocketbox 的 FBX 用的是 Maya/3dsMax 的 Phong，转成能接贴图的材质
            if (Array.isArray(o.material)) {
              o.material = o.material.map((m) => prepMaterial(m));
            } else if (o.material) {
              o.material = prepMaterial(o.material);
            }
          }
        });

        // 收集所有带 morphtarget 的 mesh
        const morphMeshes = [];
        root.traverse((o) => {
          if (o.morphTargetDictionary && Object.keys(o.morphTargetDictionary).length) {
            morphMeshes.push(o);
          }
        });

        const model = new FBXModel(root, morphMeshes, opts);
        model.bindTextures().then(() => resolve(model)).catch(() => resolve(model));
      },
      undefined,
      (err) => reject(err),
    );
  });
}

function prepMaterial(m) {
  // 关键：Rocketbox 的 FBX 材质基础色是黑的（颜色全靠贴图），
  // 若不强制改成白色，贴图会被黑色基色乘成一片黑。
  const mat = new THREE.MeshStandardMaterial({
    name: m.name || 'rocketbox',
    color: new THREE.Color(0xffffff),
    roughness: 0.68,
    metalness: 0.0,
  });
  mat.side = THREE.FrontSide;
  return mat;
}

class FBXModel {
  constructor(root, morphMeshes, opts) {
    this.kind = 'fbx';
    this.scene = root;
    this.morphMeshes = morphMeshes;
    this.opts = opts;
    // 调试开关：?nonormal=1 关闭法线贴图，用于排查法线/切线导致的异常着色
    this._noNormal = !!opts.noNormal ||
      (typeof location !== 'undefined' && /[?&]nonormal=1/.test(location.search));
    this.baseValues = new Map();   // morphKey -> 初始权重，恢复用

    // 建立别名 -> {mesh, index} 的索引
    this.index = [];
    for (const mesh of morphMeshes) {
      const dict = mesh.morphTargetDictionary;
      for (const [aliasGroup, cands] of Object.entries(VISEME_ALIAS)) {
        const k = findMorph(dict, cands);
        if (k) this.index.push({ group: 'viseme', alias: aliasGroup, mesh, key: k, idx: dict[k] });
      }
      for (const [aliasGroup, cands] of Object.entries(BLINK_ALIAS)) {
        const k = findMorph(dict, cands);
        if (k) this.index.push({ group: 'blink', alias: aliasGroup, mesh, key: k, idx: dict[k] });
      }
      for (const [aliasGroup, cands] of Object.entries(SMILE_ALIAS)) {
        const k = findMorph(dict, cands);
        if (k) this.index.push({ group: 'smile', alias: aliasGroup, mesh, key: k, idx: dict[k] });
      }
      const bk = findMorph(dict, BROW_ALIAS);
      if (bk) this.index.push({ group: 'brow', alias: 'up', mesh, key: bk, idx: dict[bk] });
    }

    // 记录骨骼
    this.bones = {};
    root.traverse((o) => {
      if (o.isBone) this.bones[o.name] = o;
    });

    // 记录初始姿态（用于呼吸/头部微动的基准）
    this.restRot = new Map();
    for (const [n, b] of Object.entries(this.bones)) {
      this.restRot.set(n, b.rotation.clone());
    }

    this.head = this.pickBone(['Bip01 Head', 'Head', 'mixamorig:Head']);
    this.spine = this.pickBone(['Bip01 Spine2', 'Bip01 Spine1', 'Spine', 'Bip01 Spine']);

    // Rocketbox 是 T-pose 导出，手臂平举。这里把上臂放下来成自然站姿。
    this.restPose();
  }

  /**
   * T-pose → 自然站姿。
   * 不猜欧拉轴（Bip01 的局部轴与 VRM 完全不同：试 Z 会前伸、试 Y 会举手、
   * 加大角度又会手臂贴进躯干）。这里按世界方向解算：把上臂骨骼的
   * 「骨骼长度方向」直接转到目标方向（斜下方、略外扩）。
   */
  restPose() {
    const armL = this.pickBone(['Bip01 L UpperArm', 'L UpperArm', 'LeftArm', 'leftUpperArm']);
    const armR = this.pickBone(['Bip01 R UpperArm', 'R UpperArm', 'RightArm', 'rightUpperArm']);

    const aimBone = (bone, targetWorldDir) => {
      if (!bone || !bone.parent) return;
      bone.updateWorldMatrix(true, false);
      const qWorld = bone.getWorldQuaternion(new THREE.Quaternion());
      // Biped 骨骼长度方向是局部 +X
      const curDir = new THREE.Vector3(1, 0, 0).applyQuaternion(qWorld).normalize();
      const target = targetWorldDir.clone().normalize();
      const qDelta = new THREE.Quaternion().setFromUnitVectors(curDir, target);
      // 换算到父空间再叠加，不破坏骨骼原有绑定
      const qParent = bone.parent.getWorldQuaternion(new THREE.Quaternion());
      const qParentInv = qParent.clone().invert();
      const localDelta = qParentInv.clone().multiply(qDelta).multiply(qParent);
      bone.quaternion.premultiply(localDelta);
      bone.updateMatrixWorld(true);
    };

    // 衬衫模型本身比较宽松，手臂需要明显外扩 + 略微前摆才不会陷进衣服里
    aimBone(armL, new THREE.Vector3(-0.60, -1.0, 0.18));
    aimBone(armR, new THREE.Vector3(0.60, -1.0, 0.18));

    // 肘部：上臂放下后，前臂仍保持 T-pose 的横向，必须单独把它也指向下方，
    // 否则小臂会横着往身体前面拐，手就交叉在腹前了。
    const foreL = this.pickBone(['Bip01 L Forearm', 'L Forearm', 'LeftForeArm']);
    const foreR = this.pickBone(['Bip01 R Forearm', 'R Forearm', 'RightForeArm']);
    aimBone(foreL, new THREE.Vector3(-0.10, -1.0, 0.12));
    aimBone(foreR, new THREE.Vector3(0.10, -1.0, 0.12));

    this.armBones = { armL, armR, foreL, foreR };
  }

  pickBone(names) {
    for (const n of names) {
      if (this.bones[n]) return this.bones[n];
    }
    // FBXLoader 会把 3ds Max 的空格名字转成下划线（Bip01 L UpperArm -> Bip01_L_UpperArm），
    // 所以匹配前统一去掉空格/下划线再比。
    const norm = (s) => String(s).toLowerCase().replace(/[\s_]/g, '');
    const keys = Object.keys(this.bones);
    for (const n of names) {
      const want = norm(n).replace(/^bip01/, '');
      const hit = keys.find((k) => norm(k).includes(want));
      if (hit) return this.bones[hit];
    }
    return null;
  }

  /** 绑定贴图：按材质组名精确分配（体/头/透明度），这是 Rocketbox 的实际结构 */
  async bindTextures() {
    const t = this.opts.textures || {};
    if (!Object.keys(t).length) return;
    const texLoader = new THREE.TextureLoader();
    // 实测：Rocketbox 的 TGA 贴图必须 flipY=true，否则 UV 整体上下错位
    // （表现为衣服对、手臂全黑、裤子显示成肤色+内裤、鞋子串色）。
    // 需要回退时用 ?flipy=0。
    const useFlipY = !(this.opts.flipY === false) &&
      !(typeof location !== 'undefined' && /[?&]flipy=0/.test(location.search));
    const loadTex = (u, srgb) => new Promise((res) => {
      if (!u) return res(null);
      texLoader.load(u, (tex) => {
        tex.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
        tex.flipY = useFlipY;
        tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
        res(tex);
      }, undefined, () => res(null));
    });

    const [bodyC, bodyN, headC, headN, opacityT] = await Promise.all([
      loadTex(t.bodyColor, true), loadTex(t.bodyNormal, false),
      loadTex(t.headColor, true), loadTex(t.headNormal, false),
      loadTex(t.opacityColor, false),
    ]);

    this._texStats = { body: 0, head: 0, opacity: 0, other: 0 };

    this.scene.traverse((o) => {
      if (!(o.isMesh || o.isSkinnedMesh) || !o.material) return;
      // FBX 常见一个 mesh 挂多个材质，必须逐个处理
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      mats.forEach((mat) => {
        if (!mat) return;
        // 只认材质名！早期版本把 mesh 名也拼进来判断，而 mesh 叫
        // `m014_hipoly_81_bones_opacity`，含 "opacity"，导致三个材质全走了 opacity 分支。
        const mn = (mat.name || '').toLowerCase();
        mat.color = new THREE.Color(0xffffff);
        mat.roughness = 0.68;
        mat.metalness = 0.0;

        if (/opacity/.test(mn)) {
          // m014_opacity 组：实测把它当 alphaMap 或给它 head 贴图都会在头顶
          // 留下褐色斑块（该组 UV 与头部 UV 对不齐）。它的作用只是睫毛/眼球周边
          // 这类细节，直接隐藏最干净，不影响观感。要排查时用 ?showopacity=1。
          const showOpacity = typeof location !== 'undefined' &&
            /[?&]showopacity=1/.test(location.search);
          if (!showOpacity) {
            mat.visible = false;
          } else if (headC) {
            mat.map = headC;
          }
          this._texStats.opacity++;
        } else if (/head|face|eye|teeth|hair/.test(mn)) {
          if (headC) mat.map = headC;
          if (headN && !this._noNormal) mat.normalMap = headN;
          this._texStats.head++;
        } else if (/body|cloth|shirt|pant/.test(mn)) {
          if (bodyC) mat.map = bodyC;
          if (bodyN && !this._noNormal) mat.normalMap = bodyN;
          this._texStats.body++;
        } else {
          if (bodyC) mat.map = bodyC;
          this._texStats.other++;
        }
        mat.needsUpdate = true;
      });
    });
  }

  /** 设置某组表情的权重（同一组多别名取第一个命中的 mesh 生效） */
  setGroup(group, alias, value) {
    for (const it of this.index) {
      if (it.group !== group || it.alias !== alias) continue;
      const arr = it.mesh.morphTargetInfluences;
      if (arr && it.idx != null && it.idx < arr.length) {
        arr[it.idx] = value;
      }
    }
  }

  /** 驱动口型：visemes = {aa:0..1, ih:.., ou:..} */
  applyVisemes(v) {
    const set = (alias, val) => this.setGroup('viseme', alias, Math.max(0, Math.min(1, val || 0)));
    // 先全部归零，再写入需要的
    for (const alias of Object.keys(VISEME_ALIAS)) set(alias, 0);
    for (const [k, val] of Object.entries(v || {})) {
      if (val > 0.001) set(k, val);
    }
  }

  applyBlink(v) {
    this.setGroup('blink', 'left', v);
    this.setGroup('blink', 'right', v);
    this.setGroup('blink', 'both', v);
  }

  applySmile(v) {
    this.setGroup('smile', 'left', v);
    this.setGroup('smile', 'right', v);
  }

  applyBrow(v) {
    this.setGroup('brow', 'up', v);
  }

  /** 每帧更新：写回 morphtarget（three 需要手动标记） */
  update(dt) {
    for (const m of this.morphMeshes) {
      if (m.morphTargetInfluences) m.morphTargetInfluences.needsUpdate = true;
    }
  }

  /** 调试：把识别到的表情映射打出来 */
  debugInfo() {
    const g = {};
    for (const it of this.index) {
      (g[it.group] = g[it.group] || []).push(it.alias + ' -> ' + it.key);
    }
    const meshes = [];
    this.scene.traverse((o) => {
      if (o.isMesh || o.isSkinnedMesh) {
        const mats = Array.isArray(o.material) ? o.material : [o.material];
        meshes.push({
          name: o.name,
          type: o.type,
          mats: mats.map((m) => ({
            name: m ? m.name : null,
            hasMap: !!(m && m.map),
            hasNormal: !!(m && m.normalMap),
            color: m && m.color ? '#' + m.color.getHexString() : null,
          })),
          morphs: o.morphTargetDictionary ? Object.keys(o.morphTargetDictionary).length : 0,
          groups: o.geometry && o.geometry.groups ? o.geometry.groups.length : 0,
          uv: !!(o.geometry && o.geometry.attributes && o.geometry.attributes.uv),
        });
      }
    });
    return {
      kind: 'fbx',
      morphMeshes: this.morphMeshes.length,
      groups: g,
      bones: Object.keys(this.bones).length,
      head: this.head ? this.head.name : null,
      spine: this.spine ? this.spine.name : null,
      meshes,
      allMorphNames: this.morphMeshes.length
        ? Object.keys(this.morphMeshes[0].morphTargetDictionary)
        : [],
    };
  }
}
