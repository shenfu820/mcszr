# -*- coding: utf-8 -*-
"""
数字人 · 一键部署（新电脑）
===========================
目标：在一台**只有 Windows + Python** 的机器上，把能跑起来的最小环境建出来，
不需要人工改脚本里的任何路径。

做的事（按顺序，失败会明确停下并说清原因）：
  1. 检查基础解释器版本（>= 3.9）
  2. 在项目根建 .venv（已存在且可用就跳过）
  3. 装 requirements.txt（有 wheels/ 目录就离线装）
  4. 探测语音识别 / GPU / 产线库 / 数据集，写回 config/paths.ini，
     并顺手把 C:\dh_root / C:\dh_prod / C:\dh_ds 三条 ASCII 软链建好（junction，不需要管理员）
  5. 跑一遍自检，打印体检表

    python _tools/dh_deploy.py --root <项目根> [--check] [--no-install] [--offline]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dh_env  # noqa: E402

IS_WIN = os.name == "nt"


def sh(cmd, timeout=1200):
    print(f"$ {' '.join(str(c) for c in cmd)}")
    p = subprocess.run([str(c) for c in cmd], capture_output=True, timeout=timeout)
    out = (p.stdout + p.stderr).decode("utf-8", "replace").strip()
    if out:
        print("\n".join(out.splitlines()[-25:]))
    return p.returncode, out


def venv_py(root: Path) -> Path:
    return root / (".venv/Scripts/python.exe" if IS_WIN else ".venv/bin/python")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="")
    ap.add_argument("--python", default="")
    ap.add_argument("--check", action="store_true", help="只报告，不动文件")
    ap.add_argument("--no-install", action="store_true", help="只建 venv，不 pip install")
    ap.add_argument("--offline", action="store_true", help="只用 wheels/ 目录离线装")
    a = ap.parse_args()

    root = Path(a.root).resolve() if a.root else Path(__file__).resolve().parent.parent
    base = Path(a.python) if a.python else Path(sys.executable)
    print("=" * 70)
    print("数字人 · 一键部署")
    print("=" * 70)
    print(f"项目根   {root}")
    print(f"基础解释器 {base}")

    # 官方口径是 Python 3.10；找得到 3.9~3.12 就用它，别拿 3.14 去碰运气
    def ver_of(py: Path) -> tuple:
        try:
            out = subprocess.run([str(py), "-c", "import sys;print('%d.%d'%sys.version_info[:2])"],
                                 capture_output=True, timeout=20).stdout.decode().strip()
            return tuple(int(x) for x in out.split(".")) if out else (0, 0)
        except Exception:
            return (0, 0)

    base_ver = ver_of(base)
    if base.is_file() and not (3, 9) <= base_ver <= (3, 12):
        for cand, why in dh_env.candidate_pythons(root, dh_env.read_ini(root / dh_env.CONFIG_REL)):
            v = ver_of(cand)
            if (3, 9) <= v <= (3, 12):
                print(f"[i] {base.name} 是 {base_ver}，换成更稳的 {cand}（{v}，来源 {why}）")
                base, base_ver = cand, v
                break
        else:
            print(f"[WARN] 只用得到 {base_ver}：项目官方口径是 3.10，出怪问题先换 3.10 复现")
    if not base.is_file():
        print(f"[FAIL] 基础解释器不存在：{base}")
        print("       装 Python 3.10 后重跑；或 --python 指定别的 python.exe")
        return 2
    print(f"基础版本  Python {base_ver[0]}.{base_ver[1]}")
    if base_ver < (3, 9):
        print("[FAIL] 需要 Python 3.9 及以上（推荐 3.10）")
        return 2

    vpy = venv_py(root)
    steps = []

    # 1) venv
    if vpy.is_file():
        print(f"[skip] 已有 .venv：{vpy}")
        steps.append(("venv", "已存在", True))
    elif a.check:
        print(f"[plan] 会创建 .venv：{root / '.venv'}")
        steps.append(("venv", "待创建", False))
    else:
        rc, out = sh([base, "-m", "venv", root / ".venv"])
        ok = vpy.is_file()
        steps.append(("venv", "已创建" if ok else f"失败 rc={rc}", ok))
        if not ok:
            print("[FAIL] 建 venv 失败。常见原因：基础解释器是 Microsoft Store 版（换官网安装包装）")
            return 3

    # 2) pip install
    req = root / "requirements.txt"
    if a.no_install or not vpy.is_file():
        steps.append(("依赖", "跳过", a.no_install))
    elif a.check:
        print(f"[plan] 会用 .venv 装 {req.name}")
        steps.append(("依赖", "待安装", False))
    else:
        cmd = [vpy, "-m", "pip", "install", "-r", req, "--disable-pip-version-check", "-q"]
        wheels = root / "wheels"
        if a.offline or (wheels.is_dir() and any(wheels.glob("*.whl"))):
            cmd += ["--no-index", "--find-links", wheels]
            print(f"[i] 离线安装：{wheels}")
        rc, out = sh(cmd)
        ok = subprocess.run([str(vpy), "-c", "import edge_tts"],
                            capture_output=True).returncode == 0
        steps.append(("依赖", "装好" if ok else f"失败 rc={rc}", ok))
        if not ok:
            print("[WARN] edge-tts 没装上：主服务能跑，但语音合成会静音。")
            print("       有网时重跑本脚本；离线就把 wheels/ 目录拷到项目根再跑。")

    # 3) 探测并写回路径
    cfg_path = root / dh_env.CONFIG_REL
    cfg = dh_env.read_ini(cfg_path)
    env = dh_env.resolve(root, cfg)
    if a.check:
        print("[plan] 会把探测到的路径写回 config/paths.ini（留空项才写），并建好 C:\\dh_root 等软链")
    else:
        vals = {"PORT": cfg.get("PORT") or "8772", "ASR_PORT": cfg.get("ASR_PORT") or "8773"}
        if not cfg.get("PYTHON") and env["DH_PY"]:
            vals["PYTHON"] = env["DH_PY"]
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
        dh_env.write_ini(cfg_path, vals)
        print(f"[ok] 已写回 {cfg_path}")
        for k in dh_env.KEYS:
            print(f"     {k}={vals.get(k, cfg.get(k, '')) or '(自动探测)'}")
        acts = dh_env.ensure_junctions(env)
        changed = {k: v for k, v in acts.items() if v not in ("ok", "no-target")}
        print("[ok] ASCII 软链 " + (" ".join(f"{k}:{v}" for k, v in changed.items())
                                   if changed else "C:\\dh_root 等已就绪"))
        # .bat 行尾：新机器上万一 clone/编辑器把文件存成 LF，双击就会报
        # `'项目根' is not recognized` 这种鬼话。这里顺手修一遍（去 BOM + LF→CRLF）。
        try:
            import bat_lint
            n = bat_lint.fix_all(root)
            print("[ok] .bat/.cmd 行尾 " + (f"修了 {n} 个" if n else "都是 CRLF"))
        except Exception as e:                    # noqa: BLE001
            print(f"[warn] .bat 行尾体检跳过：{type(e).__name__}: {e}")

    # 4) 自检
    print()
    env = dh_env.resolve(root, dh_env.read_ini(cfg_path))
    rc = dh_env.print_rows(env)
    print()
    for name, detail, ok in steps:
        print(f"  {'OK  ' if ok else 'TODO'} {name}: {detail}")
    if rc != 0:
        print("部署还没完成：解决上面的 FAIL 行后重跑本脚本。")
    elif a.check:
        print("（--check 模式：什么都没改）")
    return rc


if __name__ == "__main__":
    sys.exit(main())
