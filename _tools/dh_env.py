# -*- coding: utf-8 -*-
"""
数字人 · 跨机器环境解析与部署自检（纯标准库，Windows 优先）
=============================================================
换电脑时唯一需要「知道的事」都由这个脚本现场探测出来，脚本里不再写死任何
`E:\\conda3\\envs\\xxx` 或 `C:\\Users\\xxx\\Desktop\\...`。

用法：
    python _tools/dh_env.py --check                 人看的体检表（有 FAIL 退出码 1）
    python _tools/dh_env.py --check --no-fix        同上，但只读：不建/不修 ASCII 软链
    python _tools/dh_env.py --fix                   只建/修 ASCII 软链（C:\\dh_root 等），然后打表
    python _tools/dh_env.py --emit-bat out.cmd      给 .bat 用的 `set "K=V"` 批处理
    python _tools/dh_env.py --write-config          把探测到的路径写回 config/paths.ini

关于 ASCII 软链：C:\\dh_root → 本项目，C:\\dh_prod → new mcjc，C:\\dh_ds → 数据集。
`--check` 会顺手补上「缺失」的那几条（mklink /J，不需要管理员）；
但「指错方向」（stale）只会报警告，必须显式 `--fix` 才重指 ——
免得在临时 clone 里跑一次体检，把别人机器上的 C:\\dh_root 抢过来。
纯只读用 `--no-fix`。

配置文件 config/paths.ini 一律是 KEY=VALUE，值留空 = 自动探测。
环境变量优先级最高：DH_PY / DH_ASR_PY / DH_GPU_PY / DH_PROD / DH_DS / DH_BROWSER。
（本文件里写进去的绝对路径是「当前这台机器」的：换机器重跑 --write-config，
  不要把它当成跨机器可用的默认值。）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

IS_WIN = os.name == "nt"
CONFIG_REL = Path("config") / "paths.ini"
KEYS = ["PORT", "ASR_PORT", "PYTHON", "ASR_PYTHON", "GPU_PYTHON",
        "PROD_ROOT", "DATASET_ROOT", "BROWSER"]


# ----------------------------------------------------------------- 配置读写
def read_ini(path: Path) -> dict:
    cfg = {k: "" for k in KEYS}
    if not path.exists():
        return cfg
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line[0] in ";#[":
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip().upper()] = v.strip()
    return cfg


def write_ini(path: Path, values: dict) -> None:
    """只改 KEY 的值，注释与顺序原样保留。"""
    header = (
        "; 数字人 · 跨机器路径表（换电脑只改这个文件；值留空 = 自动探测）\n"
        "; 优先级：环境变量 DH_PY/DH_ASR_PY/DH_GPU_PY/DH_PROD/DH_DS/DH_BROWSER > 本文件 > 自动探测\n"
    )
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if not lines:
        lines = header.splitlines() + [f"{k}=" for k in KEYS]
    out, seen = [], set()
    for raw in lines:
        s = raw.strip()
        if s and s[0] not in ";#[" and "=" in s:
            k = s.split("=", 1)[0].strip().upper()
            if k in values and k in KEYS:
                out.append(f"{k}={values[k]}")
                seen.add(k)
                continue
        out.append(raw)
    for k in KEYS:
        if k in values and k not in seen:
            out.append(f"{k}={values[k]}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\r\n".join(out) + "\r\n", encoding="utf-8", newline="")


# ----------------------------------------------------------------- 小工具
def _run(cmd, timeout=8):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace").strip()
    except Exception:
        return 1, ""


def _sp_of(py_exe: Path):
    """由 python.exe 反推 site-packages（不启动进程，够快）。"""
    env = py_exe.parent.parent if py_exe.parent.name.lower() in ("scripts", "bin") else py_exe.parent
    cands = [env / "Lib" / "site-packages"]
    cands += [Path(p) for p in glob.glob(str(env / "lib" / "python*" / "site-packages"))]
    for c in cands:
        if c.is_dir():
            return c
    return None


def has_pkg(py_exe: Path, pkg: str) -> bool:
    sp = _sp_of(py_exe)
    if not sp:
        return False
    if (sp / pkg).is_dir():
        return True
    return bool(glob.glob(str(sp / f"{pkg}-*.dist-info")) or glob.glob(str(sp / f"{pkg.replace('_', '-')}-*.dist-info")))


def pkg_meta(py_exe: Path, pkg: str) -> str:
    sp = _sp_of(py_exe)
    if not sp:
        return ""
    for d in glob.glob(str(sp / f"{pkg}-*.dist-info")):
        return Path(d).name.split("-", 1)[1].rsplit(".dist-info", 1)[0]
    return ""


def torch_cuda_tag(py_exe: Path) -> str:
    sp = _sp_of(py_exe)
    if not sp:
        return ""
    vf = sp / "torch" / "version.py"
    try:
        txt = vf.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    for line in txt.splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip("'\"")
    return ""


def py_version(py_exe: Path) -> str:
    rc, out = _run([str(py_exe), "-c", "import sys;print('%d.%d.%d'%sys.version_info[:3])"], timeout=15)
    return out if rc == 0 else ""


def candidate_pythons(root: Path, cfg: dict) -> list:
    """按「越可能带依赖越靠前」收集候选解释器，去重保序。"""
    out = []

    def add(p, why=""):
        if not p:
            return
        try:
            p = Path(p)
        except Exception:
            return
        if p.is_file() and p.suffix.lower() == ".exe" or (p.is_file() and not IS_WIN):
            key = str(p).lower()
            if key not in [str(x[0]).lower() for x in out]:
                out.append((p, why))

    for env_key in ("DH_PY",):
        if os.environ.get(env_key):
            add(os.environ[env_key], f"环境变量 {env_key}")
    add(cfg.get("PYTHON"), "config.PYTHON")
    add(root / ".venv" / "Scripts" / "python.exe", "项目 .venv")
    add(root / ".venv" / "bin" / "python", "项目 .venv")
    for extra in ("DH_ASR_PY", "DH_GPU_PY"):
        if os.environ.get(extra):
            add(os.environ[extra], f"环境变量 {extra}")
    add(cfg.get("ASR_PYTHON"), "config.ASR_PYTHON")
    add(cfg.get("GPU_PYTHON"), "config.GPU_PYTHON")
    add(root / ".venv-asr" / "Scripts" / "python.exe", "项目 .venv-asr")
    add(sys.executable, "当前解释器")

    # conda 环境：~/.conda/environments.txt 是 conda 自己维护的索引，最快
    env_dirs = []
    txt = Path.home() / ".conda" / "environments.txt"
    if txt.exists():
        env_dirs += [Path(x.strip()) for x in txt.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
    bases = [Path.home() / "miniconda3", Path.home() / "anaconda3", Path.home() / "miniforge3",
             Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "miniconda3",
             Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "anaconda3"]
    for drv in ("C:", "D:", "E:", "F:"):
        bases += [Path(drv) / "conda3", Path(drv) / "miniconda3", Path(drv) / "anaconda3"]
    for base in bases:
        envs = base / "envs"
        if envs.is_dir():
            try:
                env_dirs += [d for d in envs.iterdir() if d.is_dir()]
            except Exception:
                pass
    for d in env_dirs:
        add(d / "python.exe", f"conda {d.name}")
        add(d / "Scripts" / "python.exe", f"conda {d.name}")
        add(d / "bin" / "python", f"conda {d.name}")

    # py 启动器登记的安装
    rc, out_txt = _run(["py", "-0p"])
    if rc == 0:
        for line in out_txt.splitlines():
            for tok in line.replace("\t", " ").split():
                if tok.lower().endswith(".exe") and "python" in tok.lower():
                    add(tok.strip('"'), "py 启动器")
    # uv 管理的解释器
    for pat in ("uv/python/*/python.exe",):
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming")))
        for p in glob.glob(str(base / pat)):
            add(p, "uv python")
    # PATH 上的 python
    for name in ("python.exe", "python3.exe", "python", "python3"):
        import shutil
        w = shutil.which(name)
        if w:
            add(w, "PATH")
    # 常见安装位置
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData/Local")))
    for pat in (local / "Programs/Python/Python3*", Path("C:/Python3*"), Path("E:/python")):
        for p in glob.glob(str(pat)):
            pp = Path(p)
            add(pp / "python.exe" if pp.is_dir() else pp, "常见安装位置")
    return out


def pick_by(root: Path, cfg: dict, want, prefer=None):
    """返回第一个装了 want 里全部包的候选；prefer(py) 用来在同分里挑更好的。"""
    hits = []
    for py, why in candidate_pythons(root, cfg):
        if all(has_pkg(py, p) for p in want):
            score = 1 if (not prefer or prefer(py)) else 0
            hits.append((score, str(py), why, py))
    if not hits:
        return None, ""
    hits.sort(key=lambda t: (-t[0], t[1].lower()))
    return hits[0][3], hits[0][2]


def find_prod_root(root: Path, cfg: dict):
    cands = [os.environ.get("DH_PROD"), cfg.get("PROD_ROOT")]
    home = Path.home()
    cands += [
        root.parent / "new mcjc",
        root.parent / "项目与竞赛" / "aa比赛项目" / "new mcjc",
        root.parent / "aa比赛项目" / "new mcjc",
        home / "Desktop" / "项目与竞赛" / "aa比赛项目" / "new mcjc",
        home / "Desktop" / "aa比赛项目" / "new mcjc",
    ]
    for pat in (home / "Desktop" / "*" / "*" / "new mcjc", home / "Desktop" / "*" / "new mcjc"):
        cands += [Path(p) for p in glob.glob(str(pat))]
    best, best_score = None, -1
    for c in cands:
        if not c:
            continue
        p = Path(c)
        try:
            if not p.is_dir():
                continue
        except Exception:
            continue
        score = 0
        if (p / "data" / "wood_inspect.db").is_file():
            score += 3
        if (p / "screen_service.py").is_file():
            score += 2
        if (p / "models").is_dir():
            score += 1
        if score > best_score:
            best, best_score = p, score
    return (str(best) if best else ""), best_score


def find_dataset_root(root: Path, cfg: dict) -> str:
    cands = [os.environ.get("DH_DS"), cfg.get("DATASET_ROOT")]
    for drive in ("F:/", "E:/", "D:/", "C:/"):
        for pat in ("木材*", "*缺陷*", "*dataset*"):
            try:
                cands += [Path(p) for p in glob.glob(drive + pat)]
            except Exception:
                pass
    for c in cands:
        if not c:
            continue
        p = Path(c)
        try:
            if p.is_dir():
                return str(p)
        except Exception:
            continue
    return ""


def find_browser(cfg: dict) -> str:
    cands = [os.environ.get("DH_BROWSER"), cfg.get("BROWSER")]
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))
    cands += [
        pf + r"\Google\Chrome\Application\chrome.exe",
        pf86 + r"\Google\Chrome\Application\chrome.exe",
        local + r"\Google\Chrome\Application\chrome.exe",
        pf86 + r"\Microsoft\Edge\Application\msedge.exe",
        pf + r"\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in cands:
        if c and Path(c).is_file():
            return c
    return ""


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex((host, int(port))) == 0
    finally:
        s.close()


# ----------------------------------------------------------------- ASCII 软链
# ultralytics 起子进程时中文路径会被代码页吃掉，所以固定用 C:\dh_root / dh_prod / dh_ds
# 三个 junction 当中转。junction 不需要管理员（symlink 才需要），所以可以自动建。
def _real(p) -> str:
    try:
        return os.path.normcase(os.path.realpath(str(p)))
    except Exception:
        return ""


def _mklink(link: Path, target: Path) -> bool:
    """建目录 junction/软链，成功与否都以「建完能不能访问」为准。"""
    try:
        link.parent.mkdir(parents=True, exist_ok=True)
        if IS_WIN:
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           capture_output=True, timeout=30)
        else:
            os.symlink(str(target), str(link), target_is_directory=True)
    except Exception:
        return False
    return link.exists()


def _rmdir_link(link: Path) -> bool:
    """只删「链接本身」。真实目录里只要有东西就会失败，因此不会误删数据。"""
    try:
        if IS_WIN:
            subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True, timeout=30)
        else:
            os.unlink(str(link))
    except Exception:
        return False
    return not link.exists()


def junction_targets(env: dict) -> list:
    return [("dh_root", env["root"]), ("dh_prod", env["DH_PROD"]), ("dh_ds", env["DH_DS"])]


def junction_state(env: dict) -> dict:
    """ok | missing | no-target（没有可指的目标）| stale（指到了别的目录）"""
    out = {}
    for name, target in junction_targets(env):
        link = Path("C:/" + name)
        if not link.exists():
            out[name] = "no-target" if not target else "missing"
            continue
        if not target:
            out[name] = "ok"          # 已经在用了，目标未知也不动它
            continue
        out[name] = "ok" if _real(link) == _real(target) else "stale"
    return out


def ensure_junctions(env: dict, force: bool = True) -> dict:
    """把缺的软链建起来、指错方向的改过来。返回 {名字: 动作}。"""
    actions = {}
    for name, target in junction_targets(env):
        link = Path("C:/" + name)
        state = junction_state(env)[name]
        if state == "ok" or state == "no-target":
            actions[name] = state
            continue
        if state == "stale":
            if not force:
                actions[name] = "stale"
                continue
            if not _rmdir_link(link):
                actions[name] = "stale-keep（不是链接或删不掉，请手动处理）"
                continue
        actions[name] = "created" if _mklink(link, Path(target)) else "failed"
    return actions


# ----------------------------------------------------------------- 汇总
def resolve(root: Path, cfg: dict) -> dict:
    env = {"root": str(root), "cfg_path": str(root / CONFIG_REL)}
    env["PORT"] = cfg.get("PORT") or "8772"
    env["ASR_PORT"] = cfg.get("ASR_PORT") or "8773"

    # 主解释器：优先 .venv（带 edge-tts），否则任意能跑 3.9+ 的解释器
    venv_py = root / (".venv/Scripts/python.exe" if IS_WIN else ".venv/bin/python")
    if cfg.get("PYTHON") and Path(cfg["PYTHON"]).is_file():
        main_py, main_src = Path(cfg["PYTHON"]), "config.PYTHON"
    elif venv_py.is_file():
        main_py, main_src = venv_py, "项目 .venv"
    elif os.environ.get("DH_PY") and Path(os.environ["DH_PY"]).is_file():
        main_py, main_src = Path(os.environ["DH_PY"]), "DH_PY"
    else:
        main_py, main_src = None, "未找到"
        for py, why in candidate_pythons(root, cfg):
            if py_version(py):
                main_py, main_src = py, why
                break
    env["DH_PY"] = str(main_py) if main_py else ""
    env["DH_PY_SRC"] = main_src
    env["DH_PY_VER"] = py_version(main_py) if main_py else ""
    env["DH_EDGE_TTS"] = "1" if main_py and has_pkg(main_py, "edge_tts") else "0"

    asr_py, asr_src = None, ""
    if cfg.get("ASR_PYTHON") and Path(cfg["ASR_PYTHON"]).is_file() and has_pkg(Path(cfg["ASR_PYTHON"]), "funasr"):
        asr_py, asr_src = Path(cfg["ASR_PYTHON"]), "config.ASR_PYTHON"
    if not asr_py:
        asr_py, asr_src = pick_by(root, cfg, ["funasr"])
    env["DH_ASR_PY"] = str(asr_py) if asr_py else ""
    env["DH_ASR_SRC"] = asr_src
    env["DH_ASR_OK"] = "1" if asr_py else "0"

    gpu_py, gpu_src = None, ""
    if cfg.get("GPU_PYTHON") and Path(cfg["GPU_PYTHON"]).is_file() and has_pkg(Path(cfg["GPU_PYTHON"]), "torch"):
        gpu_py, gpu_src = Path(cfg["GPU_PYTHON"]), "config.GPU_PYTHON"
    if not gpu_py:
        gpu_py, gpu_src = pick_by(root, cfg, ["torch", "ultralytics"],
                                  prefer=lambda p: "cu" in torch_cuda_tag(p))
    env["DH_GPU_PY"] = str(gpu_py) if gpu_py else ""
    env["DH_GPU_SRC"] = gpu_src
    env["DH_GPU_OK"] = "1" if gpu_py else "0"
    env["DH_GPU_VER"] = torch_cuda_tag(gpu_py) if gpu_py else ""

    prod, prod_score = find_prod_root(root, cfg)
    env["DH_PROD"] = prod
    env["DH_PROD_OK"] = "1" if prod_score >= 3 else "0"
    env["DH_DS"] = find_dataset_root(root, cfg)
    env["DH_DS_OK"] = "1" if env["DH_DS"] else "0"
    # 只用来出提示：ini 里写了、但本机不存在的那两个值
    env["CFG_PROD"] = cfg.get("PROD_ROOT") or ""
    env["CFG_DS"] = cfg.get("DATASET_ROOT") or ""
    env["DH_BROWSER"] = find_browser(cfg)
    env["DH_BROWSER_OK"] = "1" if env["DH_BROWSER"] else "0"

    env["PORT_BUSY"] = "1" if port_open(int(env["PORT"])) else "0"
    env["ASR_PORT_BUSY"] = "1" if port_open(int(env["ASR_PORT"])) else "0"

    env["DH_JUNC"] = json.dumps(junction_state(env), ensure_ascii=False)
    return env


def rows(env: dict) -> list:
    """[(level, 项, 值, 提示)]"""
    out = []

    def r(lv, name, val, hint=""):
        out.append((lv, name, val or "-", hint))

    if env["DH_PY"]:
        r("OK", "主解释器", env["DH_PY"], f"来源 {env['DH_PY_SRC']} ｜ Python {env['DH_PY_VER']}")
        try:
            vt = tuple(int(x) for x in str(env["DH_PY_VER"]).split(".")[:2])
        except Exception:                        # noqa: BLE001
            vt = (0, 0)
        if vt and not ((3, 9) <= vt <= (3, 12)):
            r("WARN", "解释器版本", f"Python {env['DH_PY_VER']}",
              "推荐 3.10（3.9~3.12）：主服务能跑，但 funasr / torch 在 "
              f"{env['DH_PY_VER']} 上多半没有轮子，asr / gpu 环境会装不上。"
              "要全功能就用 3.10 重建 .venv（一键配置环境.bat 会自动挑 3.9~3.12 打底）")
    else:
        r("FAIL", "主解释器", "", "本机没找到可用的 Python 3.9+：装一个 3.10，或先跑 一键部署_新电脑.bat")
    venv_ok = Path(env["root"]) / (".venv/Scripts/python.exe" if IS_WIN else ".venv/bin/python")
    r("OK" if venv_ok.is_file() else "WARN", "项目虚拟环境 .venv",
      str(venv_ok) if venv_ok.is_file() else "没有",
      "" if venv_ok.is_file() else "新机器第一次要建：双击 一键部署_新电脑.bat（约 1 分钟）")
    r("OK" if env["DH_EDGE_TTS"] == "1" else "FAIL", "edge-tts 语音合成",
      "已装" if env["DH_EDGE_TTS"] == "1" else "未装",
      "" if env["DH_EDGE_TTS"] == "1" else "缺它启动脚本会拒绝启动（起来也没声音）：双击 一键部署_新电脑.bat")
    if env["DH_ASR_OK"] == "1":
        r("OK", "语音识别解释器", env["DH_ASR_PY"], f"来源 {env['DH_ASR_SRC']} ｜ funasr 已装")
    else:
        r("WARN", "语音识别解释器", "", "没有装了 funasr 的环境：只能打字，不能说话（不影响其它功能）")
    if env["DH_GPU_OK"] == "1":
        r("OK", "GPU 解释器", env["DH_GPU_PY"], f"来源 {env['DH_GPU_SRC']} ｜ torch {env['DH_GPU_VER']}")
    else:
        r("WARN", "GPU 解释器", "", "没有装 torch+ultralytics 的环境：⑤调度 GPU 失效，其它照常")
    if env["DH_PROD_OK"] == "1":
        r("OK", "产线库", env["DH_PROD"], "data/wood_inspect.db 在")
    elif env["DH_PROD"]:
        r("WARN", "产线库", env["DH_PROD"], "目录在但没找到 data/wood_inspect.db：查数类回答会说库不存在")
    elif env.get("CFG_PROD"):
        r("WARN", "产线库", "",
          f"config/paths.ini 里的 PROD_ROOT={env['CFG_PROD']} 在这台机器上不存在（多半是别人机器的路径）："
          f"改成这台机器的 new mcjc，或删空让它自动探测")
    else:
        r("WARN", "产线库", "", "没找到 new mcjc 产线目录；在 config/paths.ini 里写 PROD_ROOT=（指向 new mcjc 本身）")
    if env["DH_DS_OK"] == "1":
        r("OK", "数据集", env["DH_DS"], "")
    elif env.get("CFG_DS"):
        r("WARN", "数据集", "",
          f"config/paths.ini 里的 DATASET_ROOT={env['CFG_DS']} 在这台机器上不存在："
          f"改成这台机器的缺陷数据集根（含 images/train），或删空让它自动探测")
    else:
        r("WARN", "数据集", "", "没有数据集：训练类作业会明确报「数据集软链不存在」")
    r("OK" if env["DH_BROWSER_OK"] == "1" else "WARN", "浏览器",
      env["DH_BROWSER"] or "",
      "" if env["DH_BROWSER_OK"] == "1" else "没找到 Chrome/Edge：会用系统默认浏览器开地址")
    junc = json.loads(env["DH_JUNC"])
    bad = {k: v for k, v in junc.items() if v not in ("ok", "no-target")}
    if not bad:
        junc_hint = ""
    elif any(str(v).startswith(("failed", "stale-keep")) for v in bad.values()):
        junc_hint = ("建不起来（可能被占用）：命令行手动跑 "
                     r"mklink /J C:\dh_root \"<项目根>\"，或用 --fix 再试")
    else:
        junc_hint = ("还没建/指错方向：跑 python _tools\\dh_env.py --fix；"
                     "双击 启动数字人_语音版.bat 或 一键部署_新电脑.bat 也会自动建（mklink /J，不需要管理员）")
    r("OK" if not bad else "WARN", "ASCII 软链",
      " ".join(f"{k}:{v}" for k, v in junc.items()), junc_hint)
    r("INFO", "端口", f"主服务 {env['PORT']} {'已在监听' if env['PORT_BUSY'] == '1' else '空闲'} ｜ "
                      f"语音 {env['ASR_PORT']} {'已在监听' if env['ASR_PORT_BUSY'] == '1' else '空闲'}")
    return out


def print_rows(env: dict) -> int:
    rs = rows(env)
    w = max(len(x[1]) for x in rs)
    print("=" * 74)
    print("数字人 · 跨机器部署自检")
    print("=" * 74)
    print(f"项目根  {env['root']}")
    print(f"配置文件 {env['cfg_path']}")
    print("-" * 74)
    for lv, name, val, hint in rs:
        print(f"[{lv:<4}] {name.ljust(w)}  {val}")
        if hint:
            print(f"        {' ' * w}  └ {hint}")
    print("-" * 74)
    fail = sum(1 for x in rs if x[0] == "FAIL")
    warn = sum(1 for x in rs if x[0] == "WARN")
    print(f"结论：{'不能启动' if fail else '可以启动'} ｜ FAIL {fail} ｜ WARN {warn}")
    print("=" * 74)
    return 1 if fail else 0


def emit_bat(env: dict, out: Path) -> None:
    def esc(v: str) -> str:
        v = str(v).replace("%", "%%")
        for ch in "&|<>^":
            v = v.replace(ch, "^" + ch)
        return v

    lines = ["@echo off", "rem 由 _tools/dh_env.py 生成，勿手改"]
    for k in ("DH_PY", "DH_PY_SRC", "DH_PY_VER", "DH_EDGE_TTS",
              "DH_ASR_PY", "DH_ASR_SRC", "DH_ASR_OK",
              "DH_GPU_PY", "DH_GPU_SRC", "DH_GPU_OK", "DH_GPU_VER",
              "DH_PROD", "DH_PROD_OK", "DH_DS", "DH_DS_OK",
              "DH_BROWSER", "DH_BROWSER_OK", "PORT", "ASR_PORT",
              "PORT_BUSY", "ASR_PORT_BUSY"):
        lines.append(f'set "{k}={esc(env.get(k, ""))}"')
    out.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8", newline="")
    print(f"[dh_env] 已生成 {out}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--emit-bat", default="")
    ap.add_argument("--write-config", action="store_true")
    ap.add_argument("--fix", action="store_true", help="只建/修 ASCII 软链（C:\\dh_root 等）")
    ap.add_argument("--no-fix", action="store_true", help="只读：--check 也不建/不修软链")
    ap.add_argument("--strict", action="store_true", help="WARN 也算失败")
    ap.add_argument("--next-hint", action="store_true", help="体检后补一句下一步该点哪个脚本")
    a = ap.parse_args()

    root = Path(a.root).resolve() if a.root else Path(__file__).resolve().parent.parent
    cfg_path = root / CONFIG_REL
    cfg = read_ini(cfg_path)
    env = resolve(root, cfg)

    # ASCII 软链：--check 只补"缺失"的；指错方向(stale)必须显式 --fix 才动，
    # 免得在别的副本/临时 clone 里跑一次体检就把 C:\dh_root 抢走。
    if a.fix or (a.check and not a.no_fix):
        acts = ensure_junctions(env, force=bool(a.fix))
        changed = {k: v for k, v in acts.items() if v not in ("ok", "no-target")}
        if changed:
            print("[软链] " + " ".join(f"{k}:{v}" for k, v in changed.items()), file=sys.stderr)
            env = resolve(root, cfg)

    if a.json:
        print(json.dumps(env, ensure_ascii=False, indent=2))
        return 0
    if a.emit_bat:
        emit_bat(env, Path(a.emit_bat))
        return 0
    if a.write_config:
        vals = {}
        if not cfg.get("PYTHON") and env["DH_PY"]:
            vals["PYTHON"] = env["DH_PY"] if env["DH_PY_SRC"] in ("项目 .venv", "") else ""
        if not cfg.get("ASR_PYTHON") and env["DH_ASR_PY"]:
            vals["ASR_PYTHON"] = env["DH_ASR_PY"]
        if not cfg.get("GPU_PYTHON") and env["DH_GPU_PY"]:
            vals["GPU_PYTHON"] = env["DH_GPU_PY"]
        if not cfg.get("PROD_ROOT") and env["DH_PROD"]:
            vals["PROD_ROOT"] = env["DH_PROD"]
        if not cfg.get("DATASET_ROOT") and env["DH_DS"]:
            vals["DATASET_ROOT"] = env["DH_DS"]
        if not cfg.get("BROWSER") and env["DH_BROWSER"]:
            vals["BROWSER"] = env["DH_BROWSER"]
        vals.setdefault("PORT", cfg.get("PORT") or "8772")
        vals.setdefault("ASR_PORT", cfg.get("ASR_PORT") or "8773")
        write_ini(cfg_path, vals)
        print(f"[dh_env] 已写入 {cfg_path}")
        for k in KEYS:
            print(f"    {k}={vals.get(k, cfg.get(k, ''))}")
        return 0

    rc = print_rows(env)
    if a.next_hint:
        print()
        if rc == 0:
            print("下一步：双击 一键开启全部.bat（自动找齐各工程 → 起全部服务）")
            print("        或只起数字人本体：启动数字人_语音版.bat")
        else:
            print("先把上面 FAIL 行解决，再双击 一键配置环境.bat 重跑一次。")
        if any(r[0] == "WARN" for r in rows(env)):
            print("WARN 不影响启动，只是对应功能降级（缺 asr 只能打字、缺 gpu ⑤屏失效）。")
            print("要把 WARN 补掉：一键配置环境.bat（会按当前解释器装 asr / gpu 环境）")
    if a.strict and any(r[0] == "WARN" for r in rows(env)):
        print("[strict] 存在 WARN，按失败处理")
        return 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
