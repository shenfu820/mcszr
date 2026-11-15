# -*- coding: utf-8 -*-
"""
数字人 · 自动遍历找齐所有系统
==============================
不写死任何路径：按"特征文件"在磁盘上找工程，找到就把位置写回 config\\paths.ini。

为什么要这个：五块屏背后是五个工程（产线 new mcjc、对话中枢 angent、四屏舞台包、
本仓库的主服务、缺陷数据集），换台机器它们可能放在任何地方，靠人去改 ini 迟早改错。

用法：
    python _tools/discover.py --check            只报告找到什么（不动文件）
    python _tools/discover.py --write            找到的写回 config\\paths.ini
    python _tools/discover.py --root D:\\work --write   额外指定扫描根
    python _tools/discover.py --json             机器可读

判定方式（每个 key 一组特征文件，命中即认定）：
    self     server/dh_server.py
    prod     screen_service.py（+ run.py / data/wood_inspect.db 加权）
    agent    agent_service.py
    stage    05_服务/stage_server.py 或 stage_server.py
    dataset  images/train + labels/train
    defect   core_runtime.py 或 defect_runtime.py
    keyboard pycharm_bridge.py
    training train_wood.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dh_env  # noqa: E402

ROOT_DEFAULT = Path(__file__).resolve().parent.parent

# key -> {"any": 任一命中即认定, "all": 必须全有, "bonus": 加分项, "title": 说明}
MARKERS = OrderedDict([
    ("self",     {"all": ["server/dh_server.py"], "title": "数字人主服务（本项目）", "bonus": []}),
    ("prod",     {"all": ["screen_service.py"], "title": "产线工程 new mcjc（①②屏）",
                  "bonus": ["run.py", "data/wood_inspect.db", "models"]}),
    ("agent",    {"all": ["agent_service.py"], "title": "对话中枢 angent（③屏）",
                  "bonus": ["web/index.html", "angent"]}),
    ("stage",    {"any": ["05_服务/stage_server.py", "stage_server.py"],
                  "title": "四屏舞台包（①抓帧/④聚合）", "bonus": ["05_服务/wincap.py"]}),
    ("dataset",  {"all": ["images/train", "labels/train"], "title": "缺陷数据集",
                  "bonus": ["images/val", "data.yaml"]}),
    ("defect",   {"any": ["core_runtime.py", "defect_runtime.py"],
                  "title": "早期缺陷系统 defect_system", "bonus": ["detect.py"]}),
    ("keyboard", {"all": ["pycharm_bridge.py"], "title": "CodeMender 键盘工具", "bonus": ["main.py"]}),
    ("training", {"all": ["train_wood.py"], "title": "训练脚本 Training", "bonus": ["run_all.bat"]}),
])

# 不往里钻的目录（系统目录、依赖、第三方大件）
SKIP_NAMES = {
    "Windows", "Program Files", "Program Files (x86)", "ProgramData", "AppData",
    "$Recycle.Bin", "System Volume Information", "Recovery", "PerfLogs",
    "node_modules", "__pycache__", "site-packages", "dist-info", ".git",
    "odoo-20.0", "labelImg-main", "YSL301资料", "YSL301_YSL101二次开发资料(7)",
}
SKIP_PREFIX = (".venv", "venv", "build", "dist", ".idea", ".dsh")


def _skip(name: str) -> bool:
    if name in SKIP_NAMES:
        return True
    return any(name.startswith(p) for p in SKIP_PREFIX)


def scan_roots(extra: list) -> list:
    """候选扫描根：项目根的上级、桌面、家目录、各盘符。深度不同。"""
    roots = []
    p = ROOT_DEFAULT
    for _ in range(3):
        p = p.parent
        roots.append((p, 4))
    home = Path.home()
    roots.append((home / "Desktop", 4))
    roots.append((home / "Documents", 3))
    roots.append((home, 2))
    for drv in ("C:/", "D:/", "E:/", "F:/"):
        roots.append((Path(drv), 2))
    for e in extra:
        roots.append((Path(e), 5))
    seen, out = set(), []
    for r, depth in roots:
        try:
            if not r.is_dir():
                continue
            k = str(r.resolve()).lower()
            if k in seen:
                continue
            seen.add(k)
            out.append((r, depth))
        except Exception:                       # noqa: BLE001
            continue
    return out


def walk_find(roots: list, cfg: dict) -> "OrderedDict[str, list]":
    """返回 {key: [(score, real_path), ...]}；软链被解析成真实路径并去重。"""
    hits: "OrderedDict[str, list]" = OrderedDict((k, []) for k in MARKERS)
    seen_real = {k: set() for k in MARKERS}
    cfg_prod = cfg.get("PROD_ROOT") or ""
    cfg_ds = cfg.get("DATASET_ROOT") or ""

    for root, maxdepth in roots:
        base_depth = len(root.parts)
        for cur, dirs, files in os.walk(root):
            depth = len(Path(cur).parts) - base_depth
            if depth >= maxdepth:
                dirs[:] = []
            dirs[:] = [d for d in dirs if not _skip(d)]
            here = Path(cur)
            try:
                real = os.path.realpath(str(here))
            except Exception:                        # noqa: BLE001
                real = str(here)
            for key, spec in MARKERS.items():
                ok = (all((here / m).exists() for m in spec.get("all", [])) and
                      (not spec.get("any") or any((here / m).exists() for m in spec["any"])))
                if not ok:
                    continue
                if real.lower() in seen_real[key]:
                    break                            # 软链/重复扫描，跳过
                score = 1 + sum(1 for b in spec.get("bonus", []) if (here / b).exists())
                if (here / ".git").is_dir():
                    score += 1
                if os.path.realpath(str(here)) != str(here):     # 软链本身：降权
                    score -= 1
                if key == "dataset" and any(f.endswith(".py") for f in files):
                    score -= 2                       # 带 .py 的多半是工程目录，不是数据集
                if key == "prod" and cfg_prod and Path(cfg_prod).exists() and \
                        Path(os.path.realpath(cfg_prod)) == Path(real):
                    score += 2                       # 已经配好的优先，别乱动
                if key == "dataset" and cfg_ds and Path(cfg_ds).exists() and \
                        Path(os.path.realpath(cfg_ds)) == Path(real):
                    score += 2
                seen_real[key].add(real.lower())
                hits[key].append((score, real))
                break                                # 一个目录只算一个身份
    for k in hits:
        hits[k].sort(key=lambda t: (-t[0], len(t[1])))
    return hits


def best_of(hits: list) -> str:
    return hits[0][1] if hits else ""


def report(hits: dict) -> None:
    print("=" * 78)
    print("数字人 · 自动遍历找系统")
    print("=" * 78)
    for key, spec in MARKERS.items():
        paths = hits.get(key) or []
        feats = spec.get("all") or spec.get("any") or []
        if not paths:
            print(f"[--] {key:9} {spec['title']:26} 没找到（特征：{', '.join(feats)}）")
            continue
        for i, (score, p) in enumerate(paths[:3]):
            tag = "找到" if i == 0 else "备选"
            print(f"[{tag}] {key:9} {spec['title']:26} {p}  （得分 {score}）")
    print("-" * 78)


def write_ini(root: Path, hits: dict) -> dict:
    cfg_path = root / dh_env.CONFIG_REL
    cfg = dh_env.read_ini(cfg_path)
    vals = {}
    prod = best_of(hits.get("prod") or [])
    ds = best_of(hits.get("dataset") or [])
    if prod and Path(prod) != Path(cfg.get("PROD_ROOT") or ""):
        vals["PROD_ROOT"] = prod
    if ds and Path(ds) != Path(cfg.get("DATASET_ROOT") or ""):
        vals["DATASET_ROOT"] = ds
    if vals:
        dh_env.write_ini(cfg_path, vals)
        print(f"[write] 已写回 {cfg_path}")
        for k, v in vals.items():
            print(f"        {k}={v}")
    else:
        print("[write] 现有配置已经是最优，没改")
    return vals


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--extra-root", action="append", default=[], help="额外扫描根，可多次给")
    a = ap.parse_args()

    root = Path(a.root).resolve() if a.root else ROOT_DEFAULT
    cfg = dh_env.read_ini(root / dh_env.CONFIG_REL)
    roots = scan_roots(a.extra_root)
    hits = walk_find(roots, cfg)

    if a.json:
        print(json.dumps({k: [p for _s, p in v] for k, v in hits.items()},
                         ensure_ascii=False, indent=2))
        return 0

    report(hits)
    print("扫描根：" + "；".join(f"{r}（深 {d}）" for r, d in roots))

    missing = [k for k in ("self", "prod", "agent", "stage") if not hits.get(k)]
    if a.write:
        print()
        write_ini(root, hits)
    if missing:
        print()
        print("[提示] 这些还没找到：" + ", ".join(missing))
        print("       如果是本机没装，把工程放好后重跑；如果装在了别的盘，用 --extra-root 指一下")
    return 0


if __name__ == "__main__":
    sys.exit(main())
