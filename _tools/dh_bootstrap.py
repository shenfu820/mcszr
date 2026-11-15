# -*- coding: utf-8 -*-
"""
数字人 · 环境一键配置（main / asr / gpu 三个解释器环境）
=========================================================
上一版 `dh_deploy.py` 只装主 `.venv`（edge-tts + Pillow），语音识别和 GPU 两个环境
还要人自己去 conda 里建、自己 pip install —— 新机器上那一步没人带着走，就等于"部署不了"。
这个脚本把三个环境都收进来，一条命令装完，并且**只装缺的**：

    main  →  <项目>/.venv        requirements.txt       主服务 8772 / 舞台 8790 / 对话中枢
    asr   →  <项目>/.venv-asr    requirements-asr.txt   语音识别 8773（funasr）
    gpu   →  <项目>/.venv-gpu    requirements-gpu.txt   训练/评估/推理 worker + 产线桌面端/大屏

判定顺序（每个环境各自独立）：
    1) 本机已有满足要求的环境（比如 conda 里的 voice / yoloces）→ 直接复用，写回 config\\paths.ini，不重复下载
    2) 没有 → 建项目内虚拟环境 → pip 装（带重试 / 可离线 / 有 NVIDIA 卡就装 CUDA 版 torch）
    3) 装完 import 校验，校验不过就明确报错，不假装成功

用法：
    python _tools/dh_bootstrap.py --check                 只看计划，不动手
    python _tools/dh_bootstrap.py                         装 main + asr（默认）
    python _tools/dh_bootstrap.py --all                   装 main + asr + gpu
    python _tools/dh_bootstrap.py --only asr              只装一个
    python _tools/dh_bootstrap.py --force --only asr      已有也重建项目内环境
    python _tools/dh_bootstrap.py --offline --wheels D:\\wheels
    python _tools/dh_bootstrap.py --warm-asr              顺便把 ASR 模型先下好（约 2 GB）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dh_env  # noqa: E402

IS_WIN = os.name == "nt"
ROOT_DEFAULT = Path(__file__).resolve().parent.parent
LOG_DIR = "logs"

# 目标解释器版本区间：项目官方口径 3.10；3.9/3.11/3.12 也能跑
VER_LO, VER_HI = (3, 9), (3, 12)

SPECS = {
    "main": {
        "title": "主服务环境",
        "venv": ".venv",
        "req": "requirements.txt",
        "pkgs": ["edge_tts"],                     # 判定"这个环境够用"的最小包集
        "verify": ["edge_tts", "PIL"],
        "ini": "PYTHON",
        "size": "小（数十 MB，1~2 分钟）",
        "why": "8772 主服务 / 8790 舞台 / 8999 对话中枢；缺 edge-tts 就没有声音",
    },
    "asr": {
        "title": "语音识别环境",
        "venv": ".venv-asr",
        "req": "requirements-asr.txt",
        "pkgs": ["funasr"],
        "verify": ["funasr", "soundfile", "numpy"],
        "ini": "ASR_PYTHON",
        "size": "中（funasr + torch CPU，数百 MB~1.6 GB 量级，几分钟到十几分钟）",
        "why": "8773 离线语音识别；缺它只能打字",
    },
    "gpu": {
        "title": "GPU 环境",
        "venv": ".venv-gpu",
        "req": "requirements-gpu.txt",
        "pkgs": ["torch", "ultralytics"],
        "verify": ["torch", "ultralytics"],
        "ini": "GPU_PYTHON",
        "size": "大（有 NVIDIA 卡装 CUDA 版 torch，2 GB 量级；没卡装 CPU 版几百 MB）",
        "why": "训练/评估/推理 worker + 产线桌面端与大屏；缺它 ⑤ 那块失效",
    },
}
ORDER = ["main", "asr", "gpu"]

RE_IMPORTANT = re.compile(
    r"(Downloading|Collecting|Building|Installing collected|Successfully installed|"
    r"ERROR|error:|Could not|No matching|failed|FAILED|Requirement already satisfied: (torch|funasr|ultralytics|edge-tts))",
    re.I,
)


# ------------------------------------------------------------------ 基础工具
def venv_py(root: Path, venv_name: str) -> Path:
    return root / (venv_name + "/Scripts/python.exe" if IS_WIN else venv_name + "/bin/python")


def child_env() -> dict:
    """给 pip / python 子进程强制 UTF-8。

    踩过的坑：pip 读 requirements 文件用的是**系统 locale**（中文 Windows 上是 GBK），
    文件里只要有中文注释就抛 `UnicodeDecodeError: 'gbk' codec can't decode byte 0xad`。
    所以我们两头都按住：requirements 文件只用 ASCII（见各 requirements*.txt 顶部注释），
    同时给子进程 PYTHONUTF8=1，让它内部读写走 UTF-8。
    """
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_stream(cmd, log_path: Path, verbose: bool = False) -> int:
    """跑子进程，逐行写日志；控制台只打关键行，避免几分钟没有任何输出让人以为卡死。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(c) for c in cmd]
    print(f"$ {' '.join(cmd)}")
    with open(log_path, "a", encoding="utf-8", errors="replace", newline="") as fh:
        fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        fh.write(" ".join(cmd) + "\n")
        fh.flush()
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             cwd=str(log_path.parent.parent), bufsize=1,
                             universal_newlines=True, encoding="utf-8", errors="replace",
                             env=child_env())
        assert p.stdout is not None
        for line in p.stdout:
            fh.write(line)
            fh.flush()
            clean = line.strip()
            if verbose or RE_IMPORTANT.search(clean):
                print("  " + (clean[:200] + ("…" if len(clean) > 200 else "")))
        return p.wait()


def sh_out(cmd, timeout=60) -> str:
    try:
        p = subprocess.run([str(c) for c in cmd], capture_output=True, timeout=timeout)
        return (p.stdout + p.stderr).decode("utf-8", "replace").strip()
    except Exception:
        return ""


def have_nvidia() -> bool:
    if not shutil.which("nvidia-smi"):
        return False
    out = sh_out(["nvidia-smi", "-L"], timeout=20)
    return out.upper().startswith("GPU")


def import_ok(py: Path, module: str) -> tuple:
    """只看 stdout：funasr 之类 import 时会往 stderr 打 warning，混在一起会误判。"""
    try:
        p = subprocess.run([str(py), "-c", f"import {module} as m;print(getattr(m,'__version__','?'))"],
                           capture_output=True, timeout=300)
    except Exception as e:                       # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    out = p.stdout.decode("utf-8", "replace").strip().splitlines()
    err = p.stderr.decode("utf-8", "replace").strip().splitlines()
    if p.returncode == 0 and out:
        return True, out[-1].strip()[:80]
    tail = (out or err or ["无输出"])[-1].strip()[:120]
    return False, tail


# ------------------------------------------------------------------ 解释器
def pick_base(root: Path, cfg: dict, exclude_pkgs=()) -> tuple:
    """挑一个 3.9~3.12 的解释器来建 venv。返回 (path, ver, why)。"""
    cands = dh_env.candidate_pythons(root, cfg)
    good = []
    for py, why in cands:
        v = dh_env.py_version(py)
        if not v:
            continue
        try:
            vt = tuple(int(x) for x in v.split(".")[:2])
        except Exception:
            continue
        if VER_LO <= vt <= VER_HI:
            good.append((vt == (3, 10), vt, str(py), why))     # 3.10 优先
    good.sort(key=lambda t: (-t[0], t[1]))
    for _, vt, py, why in good:
        return Path(py), f"{vt[0]}.{vt[1]}", why
    return None, "", ""


def uv_install(ver: str = "3.10") -> Path | None:
    """没有可用的 3.9~3.12 时，用 uv 现装一个（uv 会把它放在自己的托管目录里）。"""
    uv = shutil.which("uv")
    if not uv:
        for p in (ROOT_DEFAULT / "vendor" / "uv.exe", Path(os.environ.get("LOCALAPPDATA", ".")) / "uv" / "uv.exe"):
            if Path(p).is_file():
                uv = str(p)
                break
    if not uv:
        return None
    print(f"[i] 本机没有 3.9~3.12，用 uv 现装一个 Python {ver}（{uv}）")
    rc = run_stream([uv, "python", "install", ver], ROOT_DEFAULT / LOG_DIR / "bootstrap" / "uv.log")
    if rc != 0:
        return None
    found = sh_out([uv, "python", "find", ver]).splitlines()
    for line in found:
        line = line.strip()
        if line and Path(line).is_file():
            return Path(line)
    return None


# ------------------------------------------------------------------ 单个环境
def reuse_target(root: Path, cfg: dict, key: str) -> Path | None:
    """本机已有满足要求的环境就直接用（conda 里的 voice/yoloces 就是这种）。"""
    spec = SPECS[key]
    ini_val = cfg.get(spec["ini"], "")
    if ini_val and Path(ini_val).is_file() and all(dh_env.has_pkg(Path(ini_val), p) for p in spec["pkgs"]):
        return Path(ini_val)
    py, _why = dh_env.pick_by(root, cfg, spec["pkgs"])
    return py


def ensure_env(root: Path, cfg: dict, key: str, base: Path, *, check_only: bool,
               force: bool, offline: bool, wheels: Path, verbose: bool,
               warm_asr: bool, index_url: str = "", torch_index: str = "") -> dict:
    spec = SPECS[key]
    target_py = venv_py(root, spec["venv"])
    res = {"key": key, "title": spec["title"], "ok": False, "status": "", "py": "", "detail": ""}

    # ① 复用现成环境
    if not force:
        found = reuse_target(root, cfg, key)
        if found:
            ok, ver = import_ok(found, spec["verify"][0]) if key != "gpu" else import_ok(found, "torch")
            res.update(ok=ok, status="已就绪（复用现成环境）", py=str(found),
                       detail=f"{spec['verify'][0]} {ver}" if ok else "包在但 import 失败")
            print(f"[{key}] 复用 {found}")
            return res

    # ② 计划模式
    if check_only:
        res.update(status="待安装", py=str(target_py),
                   detail=f"会用 Python {dh_env.py_version(base) or '?'} 建 {spec['venv']} 并装 {spec['req']}")
        print(f"[{key}] 计划：建 {target_py} ← {base}")
        return res

    # ③ 建 venv
    log = root / LOG_DIR / "bootstrap" / f"{key}-{time.strftime('%Y%m%d_%H%M%S')}.log"
    if target_py.is_file() and force:
        print(f"[{key}] --force：删掉重建 {target_py.parent.parent}")
        shutil.rmtree(target_py.parent.parent, ignore_errors=True)
    if not target_py.is_file():
        print(f"[{key}] 建虚拟环境 {target_py.parent.parent}")
        rc = run_stream([base, "-m", "venv", target_py.parent.parent], log, verbose)
        if rc != 0 or not target_py.is_file():
            res.update(status="失败", detail=f"建 venv 失败 rc={rc}，日志 {log}")
            print(f"[{key}] [FAIL] 建 venv 失败，见 {log}")
            return res

    # ④ 装依赖
    req = root / spec["req"]
    if not req.is_file():
        res.update(status="失败", detail=f"缺 {req.name}（仓库不完整）")
        print(f"[{key}] [FAIL] 缺 {req.name}")
        return res

    common = ["--disable-pip-version-check", "--timeout", "60", "--retries", "5"]
    wheel_dir = wheels or (root / "wheels")
    offline_now = offline or (wheel_dir.is_dir() and any(wheel_dir.glob("*.whl")))
    src = ["--no-index", "--find-links", str(wheel_dir)] if offline_now else []
    if index_url and not offline_now:
        src += ["-i", index_url]                      # 弱网/国内机器用镜像，例：清华 tuna

    if key == "gpu":
        # torch 先按是否有 NVIDIA 卡选 index（ultralytics 会依赖它，先装省一次解析）
        if not dh_env.has_pkg(target_py, "torch"):
            idx = torch_index or ("https://download.pytorch.org/whl/cu124" if have_nvidia()
                                  else "https://download.pytorch.org/whl/cpu")
            torch_src = [] if offline_now else ["--index-url", idx]
            print(f"[{key}] 先装 torch（{'CUDA' if 'cu' in idx else 'CPU'} 版，源 {idx}）")
            rc = run_stream([target_py, "-m", "pip", "install", "torch", "torchvision", *common, *torch_src],
                            log, verbose)
            if rc != 0:
                res.update(status="失败", detail=f"torch 装不上 rc={rc}，日志 {log}")
                print(f"[{key}] [FAIL] torch 没装上，见 {log}")
                return res
    print(f"[{key}] pip install -r {req.name}{'（离线）' if offline_now else ''}")
    rc = run_stream([target_py, "-m", "pip", "install", "-r", req, *common, *src], log, verbose)
    if rc != 0:
        # 离线失败时给一条明确的下一句
        hint = "离线包装不全" if offline_now else "网络不稳可以重跑（会接着装）"
        res.update(status="失败", detail=f"pip 退出码 {rc}：{hint}；日志 {log}")
        print(f"[{key}] [FAIL] pip 失败：{hint}")
        return res

    # ⑤ 校验
    missing = []
    vers = {}
    for mod in spec["verify"]:
        ok, ver = import_ok(target_py, mod)
        if not ok:
            missing.append(mod)
        else:
            vers[mod] = ver
    if missing:
        res.update(status="失败", detail=f"import 校验失败：{', '.join(missing)}；日志 {log}")
        print(f"[{key}] [FAIL] 装完但 import 不过：{', '.join(missing)}")
        return res

    # ⑥ ASR 可选预热（把模型先下好，现场第一句不用等）
    if key == "asr" and warm_asr:
        print(f"[{key}] 预热模型（首次约 2 GB，可能几分钟）")
        code = ("from funasr import AutoModel;"
                "AutoModel(model='paraformer-zh', vad_model='fsmn-vad', punc_model='ct-punc', device='cpu');"
                "print('warm-ok')")
        rc = run_stream([target_py, "-c", code], log, verbose)
        if rc != 0:
            print(f"[{key}] [WARN] 模型预热失败（不影响启动，首次说话时会再试）：日志 {log}")

    res.update(ok=True, status="已装好", py=str(target_py),
               detail=" ".join(f"{k} {v}" for k, v in vers.items()))
    print(f"[{key}] 完成：{target_py}")
    return res


# ------------------------------------------------------------------ 主流程
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="")
    ap.add_argument("--only", default="", help="main,asr,gpu 里挑（逗号分隔）")
    ap.add_argument("--all", action="store_true", help="等于 --only main,asr,gpu")
    ap.add_argument("--check", action="store_true", help="只打计划，不动手")
    ap.add_argument("--force", action="store_true", help="已有环境也重建项目内虚拟环境")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--wheels", default="")
    ap.add_argument("--python", default="", help="指定用来建 venv 的基础解释器")
    ap.add_argument("--index-url", default="", help="pip 源（弱网/国内建议用清华镜像，见文档）")
    ap.add_argument("--torch-index", default="", help="覆盖 torch 的 index-url（默认按有没有 GPU 自动选）")
    ap.add_argument("--warm-asr", action="store_true", help="顺便把语音模型下好（约 2 GB）")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--no-report", action="store_true", help="结束后不打印体检表")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    root = Path(a.root).resolve() if a.root else ROOT_DEFAULT
    cfg_path = root / dh_env.CONFIG_REL
    cfg = dh_env.read_ini(cfg_path)

    keys = ORDER if a.all else ([k.strip() for k in a.only.split(",") if k.strip()] or ["main", "asr"])
    bad = [k for k in keys if k not in SPECS]
    if bad:
        print(f"[FAIL] 不认识的 --only：{', '.join(bad)}（可选 main/asr/gpu）")
        return 2

    print("=" * 74)
    print("数字人 · 环境一键配置")
    print("=" * 74)
    print(f"项目根   {root}")
    print(f"配置文件 {cfg_path}")
    print(f"要处理   " + " / ".join("%s（%s）" % (k, SPECS[k]["title"]) for k in keys))
    for k in keys:
        print(f"  · {k:4} → {SPECS[k]['venv']:9} {SPECS[k]['req']:24} {SPECS[k]['size']}")
    print("  复用优先：本机已有满足要求的环境（如 conda 的 voice / yoloces）不会重复下载")
    print("-" * 74)

    # 基础解释器：只要有一个 3.9~3.12 就够（三个 venv 都从它建）
    base = Path(a.python) if a.python else None
    if base and not base.is_file():
        print(f"[WARN] --python 指的解释器不存在，改为自动挑：{base}")
        base = None
    if not base:
        base, bver, bwhy = pick_base(root, cfg)
        if base:
            print(f"[i] 基础解释器 {base}（Python {bver}，来源 {bwhy}）")
        else:
            base = uv_install("3.10")
            if base:
                print(f"[i] uv 装好了：{base}")
            else:
                print("[FAIL] 这台机器上没有可用的 Python 3.9~3.12，也没有 uv 可以现装")
                print("       装 Python 3.10（勾 Add python.exe to PATH）后重跑；")
                print("       或先装 uv（https://astral.sh/uv）再重跑，脚本会自己拉解释器")
                return 2

    results, changed = [], {}
    rc_all = 0
    for key in keys:
        try:
            r = ensure_env(root, cfg, key, base, check_only=a.check, force=a.force,
                           offline=a.offline, wheels=Path(a.wheels) if a.wheels else None,
                           verbose=a.verbose, warm_asr=a.warm_asr,
                           index_url=a.index_url, torch_index=a.torch_index)
        except KeyboardInterrupt:
            print("\n[i] 被中断：已经装好的部分会保留，重跑会接着装")
            return 130
        results.append(r)
        if r["ok"] and r["py"]:
            cur = cfg.get(SPECS[key]["ini"], "")
            if not cur or Path(cur) != Path(r["py"]):
                changed[SPECS[key]["ini"]] = r["py"]
        if not r["ok"] and not a.check:
            rc_all = 1

    # 写回 ini（只改有变化的键）
    if changed and not a.check:
        dh_env.write_ini(cfg_path, changed)
        print("-" * 74)
        print(f"[ok] 已写回 {cfg_path}")
        for k, v in changed.items():
            print(f"     {k}={v}")

    print("-" * 74)
    for r in results:
        mark = "OK  " if r["ok"] else ("PLAN" if a.check else "FAIL")
        print(f"  {mark} {r['key']:4} {r['status']:18} {r['py']}")
        if r["detail"]:
            print(f"       └ {r['detail']}")

    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    elif not a.no_report and not a.check:
        print()
        env = dh_env.resolve(root, dh_env.read_ini(cfg_path))
        dh_env.print_rows(env)

    if a.check:
        print("（--check：什么都没改）")
    elif rc_all == 0:
        print("环境就绪。下一步：双击 一键启动全部.bat（或 启动数字人_语音版.bat）")
    else:
        print("有环境没装上：按上面 FAIL 行的提示处理后重跑本脚本（已装好的不会重装）")
    return rc_all


if __name__ == "__main__":
    sys.exit(main())
