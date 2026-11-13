# 板材数字人 · 四屏环舞舞台

> 「小星」这个人直接复用（素材包里的现用形象），场景换成板材检测项目：
> 数字人站在中间，四块屏绕着他漂。**屏里放的是正在跑的系统本体，不是重画的仪表盘。**

| 屏 | 放的是什么 | 怎么接的 |
|---|---|---|
| ① 板材缺陷检测系统 | **桌面端 run.py 本体**（PyQt5 窗口，连标题栏一起） | `05_服务/wincap.py` 用 `PrintWindow` 抓窗口位图，约 4 帧/秒推到 `<img>`；被遮挡也能抓 |
| ② 大屏数据可视化 | **8770 的页面本体**（`screen_service.py` 托管的 Vue 大屏） | `<iframe src="http://127.0.0.1:8770/">` 直连，同源不需要代理 |
| ③ 六专家复核 · 人工裁定 | **8999 对话中枢本体**（`angent agent_service.py`，左栏就是人工复核队列） | `<iframe src="http://127.0.0.1:8999/">` 直连，按 0.4953 缩到整页可见 |
| ④ 模型自学习闭环 | 闭环**动画**（样本池→闸门→训练→评估→上线） | 本包 canvas 自绘，链路与数字来自 `ai_learning_*`；真实跑批入口在 8999 的「蒸馏·自学习」 |

①②③ 任一没启动，对应屏上会写"未启动（run.py / screen_service.py / agent_service.py）"，
**不会拿假画面顶替**。状态由 `8790/api/systems` 每 5 秒探一次。

---

## 0. 一条命令开全场

```bat
05_服务\启动四屏.bat
```

它会按顺序做（已经在跑的服务会跳过）：

1. `8770` 大屏 —— `new mcjc\screen_service.py`
2. `8999` 对话中枢 —— `angent\agent_service.py`
3. `run.py` 桌面端 —— `new mcjc\run.py`
4. `8790` 舞台服务 —— `05_服务\stage_server.py`
5. 等 6 秒，用 Chrome `--app` 模式开形象窗口 `http://127.0.0.1:8790/`

关闭：`05_服务\停止四屏.bat`（只停 8790，不动三个真系统）。

| 操作 | 效果 |
|---|---|
| `1` `2` `3` `4` | 切到某块屏讲解（该屏放大 1.18× 并前移，对应台词自动播） |
| `空格` | 下一条台词 |
| `A` | 自动轮播开/关 |
| URL 加 `?still=1` | 人物降级成静态帧（视频解不出来时用，画面不空） |

---

## 1. 目录

```
板材数字人_四屏舞台_20261113/
├─ 00_先读我.txt                 一页纸说明（现场看这个）
├─ README.md                     本文件
├─ 01_人物素材_复用/              "小星"现用素材（复制自素材包，未改源包）
│   ├─ H3/idle_rgb_alpha.mp4      待机/聆听，1536×1344，24fps，5.2s
│   ├─ H3/talk_rgb_alpha.mp4      说话动作，1536×1344，24fps，24.5s
│   ├─ H3/plate.json              人物显示平面尺寸（Unity 侧原参数）
│   ├─ pose_still_rgba.png        首帧 + Alpha 合成的静态兜底图
│   ├─ 现用形象定妆图_anchor_J.png
│   └─ 来源与SHA256.json          复制来源与逐文件哈希
├─ 02_四屏舞台/                   前端（纯静态，无构建、不联网）
│   ├─ index.html
│   ├─ css/stage.css              walnut 配色（取自主系统 theme.py::WALNUT）
│   ├─ js/character.js            人物播放器（复刻 AlphaVideo.shader 的思路）
│   ├─ js/panels.js               四块屏的渲染 + 自学习闭环动画
│   └─ js/stage.js                布局/漂浮/光束/台词/窗口抓帧/系统探活
├─ 03_数据/
│   ├─ build_snapshot.py          只读库 → 算出屏上要用的数字与台词数字
│   ├─ prepare_assets.py          人物素材复制 + 静态兜底图生成
│   ├─ make_manifest.py           生成校验清单
│   ├─ shot_stage.mjs             CDP 截图工具（不装 npm 包）
│   └─ snapshot.json / snapshot.js
├─ 04_台词配音/
│   ├─ script.json / script.js    台词（数字由 build_snapshot.py 注入）
│   ├─ make_voice.py              edge-tts 预渲染（只在生成时联网）
│   └─ voice/*.mp3                6 段配音（zh-CN-YunxiNeural）
├─ 05_服务/
│   ├─ stage_server.py            127.0.0.1:8790：静态 + /api/stage + /api/win + /api/systems
│   ├─ wincap.py                  桌面窗口抓帧（GDI PrintWindow，被遮挡也能抓）
│   └─ 启动四屏.bat / 停止四屏.bat
├─ 06_验收截图/                   本轮实测截图
└─ 校验清单.json                  全包文件清单 + SHA256
```

---

## 2. 舞台服务 8790 的四个接口

| 接口 | 作用 | 备注 |
|---|---|---|
| `/api/stage` | 屏上数字与台词数字（只读库聚合） | 5 秒刷新，3 秒缓存 |
| `/api/win/desktop.jpg` | 桌面端窗口抓帧（JPEG） | 找不到窗口返回 503，前端显示"未启动" |
| `/api/systems` | 三套系统探活：`desktop`（找窗口）/ `screen`（8770 `/api/health`）/ `agent`（8999 `/api/health`） | 实测样例见下 |
| `/api/health` | 本服务自检 | |

`/api/systems` 实测返回（2026-11-13 12:47:16）：

```json
{"desktop":{"online":true,"title":"临沂科技职业学院木材/板材表面缺陷智能检测系统","pid":54408,"capture_at":"12:47:16"},
 "screen":{"online":true,"pid":9788,"url":"http://127.0.0.1:8770"},
 "agent":{"online":true,"pid":44972,"url":"http://127.0.0.1:8999"}}
```

---

## 3. 屏上数字的出处

> 全部**只读**打开 `new mcjc/data/wood_inspect.db`（`mode=ro`）+ 读 `config/settings.json`。

| 位置 | 内容 | 来源 | 本轮实测值（2026-11-13 12:48） |
|---|---|---|---|
| ① 窗口本身 | 桌面端真实界面（数据集 119 会话 / 793 明细、v1.1.0） | run.py 实时抓帧 | 抓帧 12:48:36，pid 54408，窗口 1040×660 |
| ① 屏脚注 | 会话/明细计数 | `sessions`、`detections` | 119 场会话（44 场检出过缺陷）/ 793 条明细 / 平均置信 0.5540 / 最高 0.8888 |
| ② 页面本身 | 大屏实时看板 | 8770 自身 | 在线 pid 9788（**已按本包启动方式从 `new mcjc` 重启**） |
| ③ 页面本身 | 对话中枢 + 人工复核队列 | 8999 自身 | 在线 pid 44972；库里待裁定 1263 条 |
| ④ 动画数值 | 样本池 112（teacher 80 / review 32）、闸门 30 已过、run#51 ok 3 epoch、mAP50 0.4950 = 基线 0.4950 + δ 0.0、current `selflearn_run51_20261112_090729`、候选 23 / 归档 27 | `ai_learning_samples/runs`、`ai_models`、`settings.json` | 同上 |

台词里的数字由 `build_snapshot.py` 直接注入，避免"台上说的"和"库里有的"不一致。

---

## 4. 换上衣（商品图"灰色拼藏青"工装夹克）—— 生成式逐帧换装

小星是实拍视频人像（不是 3D 模型），要"真穿上"只能逐帧重绘。流水线（全部本地、可复现）：

| 步 | 脚本 | 干什么 |
|---|---|---|
| 1 | `03_数据/prepare_garment_plate.py` | 商品图抠白底、切掉裤子与文字、估藏青/灰色 |
| 2 | `03_数据/prepare_garment_fit.py` | 用**整个人形轮廓**逐行把夹克贴上去，出 `assets/jacket_fit.png`（照片版）与 `jacket_maps.png`（版型图） |
| 3 | `03_数据/inpaint_batch.py` | 合成 + **SD1.5 inpainting 重绘**（strength 0.58、36 步、固定 seed、遮罩含脖子一圈让 SD 自己长领子）→ `H3/<name>_rgb_alpha_work.mp4` |
| 4 | `03_数据/smooth_video.py` | 衣服区一阶 EMA 时间平滑（α=0.65），压帧间闪烁 → `H3/<name>_rgb_alpha_works.mp4`（舞台优先用这个） |

实测：idle 124 帧 衣服区相邻帧平均绝对差 **5.17 → 3.50（↓32%）**；talk 588 帧 **3.54 → 2.36（↓33%）**。
模型：`AI-ModelScope/stable-diffusion-inpainting`（ModelScope，fp16，落 `~/.cache/modelscope`）；
依赖装在独立 venv `数字人\.venv-sd`（`--system-site-packages` 复用产线 torch，不污染 `yoloces` 环境）。

舞台切换逻辑（`js/stage.js`）：

| 状态 | 行为 |
|---|---|
| `idle_rgb_alpha_work.mp4` + `talk_rgb_alpha_work.mp4` 都在 | 默认放**真换装视频**（`?outfit=video`） |
| 缺任意一段 | 自动回落到着色器换装（`?outfit=plate`，结构转移） |
| `T` 键 / `?outfit=suit` | 随时切回原西装 |

命令行复现（GPU：RTX 5060 Laptop，torch cu130）：

```powershell
$env:SD_INPAINT_DIR='C:\Users\Administrator\.cache\modelscope\models\AI-ModelScope--stable-diffusion-inpainting\snapshots\master'
$SD="C:\Users\Administrator\Desktop\数字人\.venv-sd\Scripts\python.exe"
& $SD "03_数据\prepare_garment_fit.py"
& $SD "03_数据\inpaint_batch.py" --video idle --strength 0.58
& $SD "03_数据\inpaint_batch.py" --video talk --strength 0.58
& $SD "03_数据\smooth_video.py" --video idle --alpha 0.65
```

**仍然存在的限制**：逐帧独立重绘，领口/口袋位置会有**轻微帧间跳动**（已用 EMA 压掉约三成）；
手臂大幅摆动时袖口仍可能不跟手。要彻底稳，需要"参考帧锁定"（每帧以上一帧结果为初值、低 strength 传播）。

---
## 5. 现场动过的东西（如实记录）

1. **8770 被重启过**：原来在跑的那只是旧部署（读 `Desktop\aa比赛项目\大屏系统\data\wood_inspect.db`，
   会话 99 条、conf 0.25、报警 COM6）。本包启动脚本按 `new mcjc` 目录重启了它，
   现在 `/api/health` 里 `screenDist` 指向 `new mcjc\screen/dist`。
   —— 端口没改，配置没改，只是换了工作目录重起。
2. **8999 是新起的**：`angent\agent_service.py`，只读门户，不改库。
3. **run.py 是新起的**：桌面端本体，模型和输入源都还是 `settings.json` 里的原样（未点"开始检测"）。
4. 没有修改 `new mcjc` 的任何文件；没有写库；没有换权重；没有动 `screen_port`/`chat_port`。

---

## 6. 改东西的入口

| 想改 | 改哪 | 注意 |
|---|---|---|
| 四块屏的位置/大小/倾角 | `02_四屏舞台/js/stage.js` 的 `LAYOUT` | 画布逻辑尺寸固定 1920×1080；焦点放大倍数在 `FOCUS_SCALE` |
| 屏①抓帧频率/尺寸 | `02_四屏舞台/js/stage.js` 的 `startWindowFeed()`（260ms）、`05_服务/wincap.py` 的 `max_width` | 太快会吃 CPU |
| 屏②③ 的页面地址 | `02_四屏舞台/js/panels.js` 的 `data-src` | 端口要跟 `settings.json` 一致 |
| ③ 的缩放比例 | `02_四屏舞台/css/stage.css` 里 `#p-review .liveframe` 的 `scale()` | 想让字大就把面板做大，别只放大 iframe |
| 台词 | `04_台词配音/script.json`，再跑 `build_snapshot.py` + `make_voice.py --force` | 数字由构建器注入 |
| 数据字段 | `03_数据/build_snapshot.py` | 保持 `mode=ro` |

```powershell
$PY = "E:\conda3\envs\yoloces\python.exe"
& $PY "03_数据\build_snapshot.py"
& "C:\Users\Administrator\Desktop\数字人\.venv\Scripts\python.exe" "04_台词配音\make_voice.py" --force
& $PY "03_数据\make_manifest.py"
```

---

## 7. 现场 checklist

1. 双击 `05_服务\启动四屏.bat`，等形象窗口出来
2. 窗口里点一下（浏览器要求先有交互才允许出声）
3. 看四块屏：① 里应是**桌面端真实界面**（能看到"监控台/统计分析/模型仓库"左栏），
   ② 是大屏看板，③ 是对话中枢（左栏人工复核队列）
4. 顶栏右侧应显示 `数据源：实时库`；若显示 `快照`，说明 8790 的 `/api/stage` 没通
5. 按 `1`→`4` 走一遍，字幕在动、嘴在动、光束指向对应屏
6. 报数（例如"793 条标注"）后当场打开 `new mcjc\data\wood_inspect.db` 核对

| 现象 | 处理 |
|---|---|
| 屏① 显示"桌面端未启动" | `run.py` 没起或窗口标题里没有"缺陷智能检测系统"；跑 `run.py` 或改 `wincap.WIN_TITLE` |
| 屏②/③ 白块 | 对应端口没起；看 `/api/systems` 返回的 online |
| 画面卡 | 关掉不看的服务，或把抓帧间隔从 260ms 调到 500ms |
| 人物是静态图 | 视频没解出来，属兜底；或加了 `?still=1` |
| 没声音 | 点一下窗口；确认 `04_台词配音/voice/*.mp3` 存在 |

---

## 8. 明确的边界

1. **①②③ 是"真窗口/真页面"，但被缩进了 600px 面板里**：① 抓帧按 1040 宽原样缩到面板；
   ②③ 是整页缩放，字号会很小。按 `1`~`4` 聚焦时该屏会放大 1.18× 并前移。
   想看清细节，直接看真窗口（桌面端/浏览器）比看舞台更合适。
2. **iframe 里可以点，但要小心**：面板有 3D 旋转和缩放，鼠标落点会偏；演示时建议在真窗口里操作。
3. **屏④ 是动画，不是一次真跑批**：链路和数字来自真实表；要真跑，去 8999 →「蒸馏·自学习」。
   跑批时 8999 会推真实阶段事件，但这块动画不会跟着变（要看实时就得盯 8999 那一页）。
4. **人物不是 3D 可转视角模型**：复用素材包的"视频 + Alpha 平面"，正脸对话够用，转不了身；口型不是逐字对齐。
5. **不改产线**：数字人是独立程序，读库 + 抓窗口 + 开页面，`new mcjc` 一行没动。
6. 未做语音识别；交互是键盘 + 控制台 `StageCtl`（`StageCtl.focus('learn')` / `.play(4)` / `.auto(false)`）。

---

## 9. 技术栈

| 层 | 用什么 | 为什么 |
|---|---|---|
| 人物 | WebGL1 单 quad + 两个 `<video>` 纹理 | 不用 three.js、不打包、离线可用 |
| 窗口投屏 | Win32 `PrintWindow(PW_RENDERFULLCONTENT)` + GDI `GetDIBits` + Pillow | 原生窗口只能这么进网页；被遮挡也能抓 |
| 舞台 | 纯 HTML/CSS 3D + iframe | 真页面直接嵌，不重画 |
| 数据 | Python 标准库 `sqlite3`（只读）+ `http.server` | 断网也能起 |
| 动画 | canvas 2D + rAF | 自学习闭环、流动光点、run 折线 |
| 语音 | edge-tts 预渲染（仅生成时联网） | 现场断网不影响 |

解释器：`E:\conda3\envs\yoloces\python.exe`（3.10.21，与主系统同环境；舞台只用标准库 + Pillow）。
语音生成用数字人工程的 `.venv`（edge-tts 7.3.1）。

---

## 10. 本轮验收（实测）

- `/api/systems` 三套系统全部 `online`（desktop pid 54408 / screen pid 9788 / agent pid 44972）
- `/api/win/desktop.jpg` 返回 94 KB JPEG，抓帧时间与截图时间一致（12:48:36）
- CDP 实拍：`06_验收截图/07_真实系统_全景.png`（1920×1080），① 屏里能直接读到桌面端
  "数据 119 会话 / 793 明细 v1.1.0"，② 屏是大屏看板本体，③ 屏是对话中枢本体
- 页面无脚本错误；`/api/stage` 每 5 秒刷新，顶栏显示"数据源：实时库"
- 人物 alpha：左半 RGB / 右半 Alpha 已用 OpenCV 实测确认（bbox 374×1278，与 `plate.json` 一致）

```powershell
E:\conda3\envs\yoloces\python.exe "05_服务\stage_server.py" --check     # 数据自检
curl http://127.0.0.1:8790/api/systems                                  # 三套系统探活
node 03_数据\shot_stage.mjs "http://127.0.0.1:8790/" "out.png" 12000 1920 1080 "" 2
```
