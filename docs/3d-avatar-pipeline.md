# 单图生 3D 数字人链路（model.ckpt → GLB → 前端）

> 目标：用桌面上的 `model.ckpt` 做「推理 → 提取网格 → Blender 规整 → 接进数字人」。
> 本文只写**实测过**的东西，每条结论后面都给了可复现命令。

---

## 一、model.ckpt 是什么（实测）

| 项 | 值 | 证据 |
|---|---|---|
| 文件 | `C:\Users\Administrator\Desktop\数字人\model.ckpt`，1 677 246 742 字节 | `Get-Item` |
| 格式 | PyTorch zip，553 entries，纯 `state_dict`（只含 `_rebuild_tensor_v2`） | 读 zip 目录 + `pickletools` |
| 顶层键 | `backbone` 326 / `image_tokenizer` 200 / `decoder` 20 / `post_processor` 2 / `tokenizer` 1 | `torch.load(..., mmap=True)` |
| 架构 | **TripoSR**（VAST-AI / Stability 的单图生 3D） | 见下三条 |
| 参数量 | 419.3 M | 严格加载后 `sum(p.numel())` |

判定依据（三条独立证据）：

1. 键名形状与 TripoSR 官方 `config.yaml` 完全对应：`tokenizer.embeddings (3,1024,32,32)`、
   `post_processor.upsample (1024,40,2,2)`、`decoder.layers.0 (64,120)`、`backbone.proj_in (1024,1024)`、
   `backbone.transformer_blocks.0.attn2.to_k (1024,768)`、`image_tokenizer` 为 12 层 ViT-B/16（patch 16、dim 768）。
2. 用官方 `config.yaml`（`_tools/i23d/TripoSR/config.yaml`，从 `hf.co/stabilityai/TripoSR` 取回）
   实例化 `tsr.system.TSR` 后 **`load_state_dict(strict=True)` 通过**，无缺失/多余键。
3. 官方仓库里没有任何一份权重比这个 ckpt 更匹配：TripoSR 的 `model.ckpt` 就是 1.6 GB 级、
   键名与上面逐条一致。

## 二、跑通链路（实测命令与耗时）

解释器：`_tools\i23d\venv\Scripts\python.exe`（torch 2.14.1+cu130，RTX 5060 Laptop 8 GB 可用）

```powershell
# 1) 推理 + 抽网格（复用已算好的 scene.npz 可跳过推理）
_tools\i23d\venv\Scripts\python.exe _tools\i23d\tsr_pipeline.py ^
  --image _tools\i23d\ref2.png --out _tools\i23d\out --name dh01 ^
  --mc 384 --threshold 25 --render

# 2) 摆正 + 切掉背面拉出来的锥壳（Python/trimesh，见下文"为什么不在 Blender 里转"）
_tools\i23d\venv\Scripts\python.exe _tools\i23d\mesh_prep.py ^
  --in _tools\i23d\out\dh01\mesh_raw.obj ^
  --out _tools\i23d\out\dh01\mesh_prep.glb --report _tools\i23d\out\dh01\prep.json

# 3) Blender 无头规整 + 资产门禁 + 正面贴图投影
"E:\Blender\blender.exe" -b --factory-startup --python _tools\blender\regularize_mesh.py -- ^
  --args-json _tools\i23d\out\args_face.json
```

实测耗时（同一台机器）：

| 步骤 | 耗时 |
|---|---|
| 加载 ckpt + 严格校验 | 5–6 s |
| 抠图（GrabCut）+ 预处理 | 11.5 s |
| 推理（triplane 场景码） | 8.8 s |
| 抽网格（mc 256 / 384） | 3.4 s / 10–15 s |
| Blender 清理+减面+UV+导出 | ~10 s |

产物（`_tools\i23d\out\dh01\`）：`input.png`、`scene.npz`（1.8 MB，可复算）、`mesh_raw.glb|obj`、
`render_00..05.png`（NeRF 多视角预览）、`asset_report*.json`、`prep.json`。

## 三、踩到并修掉的坑（都有实测证据）

1. **`torchmcubes` 没编译**：`tsr/models/isosurface.py` 直接 `from torchmcubes import marching_cubes`。
   本机用 PyMCubes 写了替身 `_tools/i23d/shim/torchmcubes.py`。
2. **PyMCubes 与 torchmcubes 面片绕序相反** → 网格法线朝内。
   证据：`probe_normals.py` 报 `volume = -0.088`、外指面仅 **7.4%**；翻转绕序后 `volume = +0.088`、
   外指面 **92.6%**。修好后画面才正常（之前看到的都是"内壁"）。
3. **transformers v5 改了 ViT 参数名**（`encoder.layer.N.attention.attention.query` →
   `layers.N.attention.q_proj`），ckpt 是 v4 命名 → 严格加载报 192 个键不匹配。
   `tsr_pipeline.py` 里做等价重映射后 `strict=True` 通过。
4. **外网不稳**：`hf_hub_download` 取 DINO 的 `config.json` 会卡住 → 落成本地
   `dino_vitb16_config.json`（权重仍然全部来自 model.ckpt）；`rembg` 要下 u2net 也不可用 →
   改用 `_tools/i23d/cutout.py` 的 GrabCut（本机 cv2，无需联网）。
5. **Blender 的轴变换**：trimesh 导出的 glTF 进 Blender 会做一次 Y-up→Z-up，
   自己再叠旋转极易把正负号搞错（本轮踩了两次）。改为**在 trimesh 里一次换到 glTF 约定**
   `(x,y,z)_raw → (y,z,x)`，Blender 只做清理/减面/UV/导出，进出各一次转换互相抵消。
6. **中文路径**：给 Blender 传中文路径会变成代理字符（打印/写 json 乱码）→ 统一走
   `--args-json <utf8 json>`；脚本读 json 用 `utf-8-sig`（容忍 BOM）。

## 四、资产门禁（按 blender-skills 的口径执行）

`_tools\skills\blender-skills`（从 `gitcode.com/gh_mirrors/bl/blender-skills` 克隆，95 个 skill）
里取了 `asset-optimization`、`export-pipeline`、`qa-review`、`retopology` 与
`references/polycount-budgets.md`、`references/validation-checklist.md` 的可执行子集，
落成 `regularize_mesh.py` 的 `validate()`：

```
[blender] VALIDATION REVIEW tris=20121/30000 outward=0.9864 uv=1.6618 nonmanifold=17 boundary=26
```

| 检查项 | 门禁 | 实测 |
|---|---|---|
| 三角面 | ≤ 30 000（Web 端头像档：Mobile hero 15–30k） | 20 121 ✅ |
| 法线朝外 | 外指面 ≥ 0.90 且体积 > 0 | 0.986 ✅ |
| 变换已应用 | scale=1/rot=0 | ✅ |
| 材质数 | ≤ 1 | 1（`MAT_dh_face`）✅ |
| 顶点色 | 存在 | `Color` ✅ |
| UV 打包效率 | ≥ 0.75 | 1.66（正面平面投影，必然重叠，指标不适用）⚠️ |
| 非流形边 / 边界边 | 记录 | 17 / 26（切尾留下的开口，已用 `cap_open` 补了大部分）⚠️ |

结论 **REVIEW** 而不是 PASS，扣分项只有 UV 指标口径与切口边界，几何与法线都是干净的。

## 五、接进数字人前端

新增 `avatar/src/mesh.js`（GLB 适配器），`avatar/src/main.js` 只加了两处：
`makeAdapter` 里认 `kind === 'mesh'`、`loadModel` 里多一个 `.glb` 分支。

- 接口与 VRM/FBX/2.5D 完全一致：`setLip({aa,ih,ou}) / setBlink / setSmile / setBrow / update`
- 因为没有骨骼和 blendshape，**张嘴/眨眼在顶点着色器里做**：
  下颌绕"嘴角高度"的 X 轴旋转、眼睛高度附近沿 Y 挤压、微笑轻微上提；
  区域参数全部来自 URL，可现场调。
- URL 参数：`?model=../models/dh01/dh01_face.glb&yaw=0&mouth=0.32&eye=0.58&jaw=0.75`
- 朝向修正挂在**外层 pivot**上（`main.js` 的 tick 每帧会写 `adapter.head.rotation.y`，
  挂 head 上会被覆盖——这个坑也踩过）。

实测（headless Chrome + CDP，`_tools/shot.mjs`）：

| 检查 | 结果 |
|---|---|
| GLB 加载 | `[DH] mesh 头像已就绪`，`triangles: 20121`，`bootHidden: true` |
| 口型驱动 | uLip 0→1 画面变化 3182 px（0.59%），变化区在脸部 |
| 朝向 | `yaw=0` 正脸朝镜头；`yaw=180` 露出切口（背面） |

截图：`models\dh01\preview_face.png`、`models\dh01\preview_head_vcol.png`。

## 六、诚实的质量结论

- TripoSR 对**人像**是弱项（它在 Objaverse 物体上训练）。NeRF 渲染（`render_*.png`）能看出
  眼镜、耳机、发型，但**等值面几何是一颗"没五官的圆头"**，五官信息主要在颜色场里。
- 所以做了第二手：把参考人像按正面平面投影贴到网格上（`--project-image` + `face.json`
  标定的 UV 矩形）。贴图后能看出眼睛/皮肤/头发分区，但**几何没有起伏**，
  侧看会露馅，贴图在轮廓外侧会带一点背景色。
- 结论：**这条链路适合当"彩蛋/自研能力"演示，不适合替代现场形象**。
  现场仍建议用 `models/rocketbox/m08/m08_facial.fbx`（README 第七节）。

## 七、下一步（按性价比排序）

1. **换输入图**：正面、纯色背景、无耳机无眼镜、无刘海遮挡的证件照式人像，
   重跑第二节三步（约 40 s）。这是提升最大的一步。
2. **提高等值面分辨率**：`--mc 512`（显存约 7 GB，需要 `--chunk 4096`），台阶纹更细。
3. **贴图对齐自动化**：现在 UV 矩形是 `face.json` 标定值，可用眼睛/嘴两个锚点做最小二乘拟合，
   允许 `yaw/pitch` 三个自由度。
4. **要能真的说话**：几何没有口部结构，最稳的做法是"头壳 + 2.5D 口部贴片"混合
   （现有 `portrait.js` 的 shader 已经能做口型），或者直接上带 blendshape 的人头模型。

## 八、文件清单

```
_tools/i23d/
  tsr_pipeline.py        推理→网格主入口（严格加载、绕序兜底、scene.npz 复用）
  mesh_prep.py           摆正到 glTF 约定 + 切背面锥壳（颜色/几何双判据）
  build_avatar_asset.py  一条命令跑完抽网格→规整→建资产→预览（阈值对比用）
  face_uv_rect.py        从参考图里量出头/五官矩形（给贴图投影）
  analyze_orientation.py 颜色+剖面判朝向
  probe_normals.py / probe_glb_attrs.py / probe_color_frame.py / probe_image_grid.py / probe_image_px.py
  shim/torchmcubes.py    PyMCubes 替身（含绕序修正）
  TripoSR/               官方源码 + config.yaml（v5 键名重映射在 tsr_pipeline 里）
_tools/blender/
  regularize_mesh.py     清理/定向/减面/平滑/UV/补洞/贴图投影/门禁/导出 GLB
  preview_glb.py         多方位预览（Workbench，顶点色或贴图）
  probe_axes.py / probe_uv_flip.py
_tools/skills/blender-skills/   外部 skill 库（参考口径，未改）
avatar/src/mesh.js              GLB 头像适配器（着色器做口型/眨眼）
models/dh01/dh01_face.glb       带照片投影的头像（贴图版）
models/dh01/dh01_head.glb       纯顶点色头像（几何版）
```
