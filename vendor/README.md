# vendor/ —— 产线工程的本地副本（不进仓库）

为什么要这个目录、而仓库里又为什么没有 `new mcjc` / `angent`：

| 工程 | 体量 | 能不能进仓库 | 原因 |
|---|---|---|---|
| `new mcjc`（①② 屏本体：桌面端 `run.py` + 大屏 `screen_service.py`） | **45.7 GB** | **不能** | `output/` 34.8 GB、`data/` 1.3 GB、`models/` 1.2 GB、`.venv/` 319 MB、`screen/` 101 MB；公网仓库放不下，也不该把产线库和权重推上去 |
| `angent`（③ 屏本体：对话中枢 8999） | 8.3 MB（代码 0.57 MB + data/screenshots 7.4 MB） | 可以，但没必要 | 它本来就躺在 `new mcjc` 旁边，启动器按 `PROD_ROOT\..\angent` 自动找 |
| `odoo-20.0`（ERP，不在五块屏里） | 1.07 GB | 不能 | 整套 Odoo 源码 + 依赖 |

所以仓库走的是「**代码 + 启动编排在仓库，大件留在产线机**」：

1. 产线机（有 `new mcjc` 的那台）：什么都不用做，`config\paths.ini` 里的 `PROD_ROOT` 指过去即可。
2. 展厅机（只放屏）：两种接法
   - **本机没有产线，屏接产线机**：`paths.ini` 里写
     `SCREEN_URL=http://<产线机IP>:8770`、`AGENT_URL=http://<产线机IP>:8999`（产线机那三个服务要 `--host 0.0.0.0`）
   - **想在本机也跑一份产线**：把 `new mcjc` 整个拷到这台机的任意位置，然后
     `paths.ini` 的 `PROD_ROOT=` 指过去；也可以拷到本目录 `vendor\new mcjc\`（已 gitignore，不会被提交）

`一键启动全部.bat` 会按 `PROD_ROOT` 找齐 ①②③，缺哪个就跳过并说清缺什么，不会假装成功。
