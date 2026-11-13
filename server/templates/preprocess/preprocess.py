# -*- coding: utf-8 -*-
"""
板材图像预处理
==============
依次完成：降噪 → 增强 → 归一化 → 按板号成组划分 train/val/test。

设计要点（对应验收要求）：
1. 所有参数在 config.yaml，代码不写死任何数字。
2. 每一步都记日志，出问题能定位到哪一步、哪张图。
3. 划分按 board_id 成组，同一块板的切片不跨集合 —— 否则验证集分数是虚高的。
4. 同一张图可输出处理前后对比图，肉眼可验。

用法：
    python preprocess.py                 # 跑全流程
    python preprocess.py --dry-run       # 只统计不写盘
    python preprocess.py --compare a.jpg # 只看单张处理前后对比
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

# ─────────────────────────────── 配置加载
def load_config(path: Path) -> dict:
    """只依赖 PyYAML；没装就退回内置默认值并在日志里说清楚。"""
    try:
        import yaml
    except ImportError:
        logging.warning("未安装 PyYAML，使用内置默认配置（请 pip install pyyaml 后重跑）")
        return DEFAULT_CONFIG
    if not path.exists():
        logging.warning("配置文件不存在：%s，使用内置默认配置", path)
        return DEFAULT_CONFIG
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


DEFAULT_CONFIG = {
    "input_dir": "../../samples/raw",
    "output_dir": "../../samples/preprocessed",
    "log_file": "preprocess.log",
    "denoise": {"method": "gaussian", "kernel": [5, 5], "sigma": 1.0},
    "enhance": {"method": "clahe", "clip_limit": 2.0, "tile_grid": [8, 8], "channel": "ycrcb"},
    "normalize": {"to_float": True, "save_preview": True},
    "split": {"train": 0.7, "val": 0.2, "test": 0.1, "seed": 42, "group_by": "board_id"},
    "dedup": {"enabled": True, "hamming_threshold": 4},
}


# ─────────────────────────────── 三个核心处理函数
def image_denoise(img: np.ndarray, cfg: dict) -> np.ndarray:
    """降噪：把产线上传送带震动、传感器噪点抹平，同时保住缺陷边缘。

    kernel 必须是奇数；核越大越柔和，但小缺陷也越容易被糊掉。
    1mm 级划痕建议不超过 5×5。
    """
    method = cfg.get("method", "gaussian")
    k = tuple(cfg.get("kernel", [5, 5]))
    if k[0] % 2 == 0 or k[1] % 2 == 0:
        raise ValueError(f"降噪核必须是奇数，当前 {k}")
    if method == "gaussian":
        return cv2.GaussianBlur(img, k, sigmaX=cfg.get("sigma", 1.0))
    if method == "median":
        return cv2.medianBlur(img, k[0])
    if method == "bilateral":
        d = max(k)
        return cv2.bilateralFilter(img, d, 75, 75)
    raise ValueError(f"未知降噪方法 {method}")


def image_enhance(img: np.ndarray, cfg: dict) -> np.ndarray:
    """增强：CLAHE 自适应直方图均衡，局部提亮压暗，暗处细节拉出来、亮处不过曝。

    关键在只对亮度通道（YCrCb 的 Y）做，否则会把木纹的颜色也一起拉偏。
    """
    method = cfg.get("method", "clahe")
    if method == "none":
        return img
    if method == "equalize":
        ycrcb = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
        y, cr, cb = cv2.split(ycrcb)
        return cv2.cvtColor(cv2.merge((cv2.equalizeHist(y), cr, cb)), cv2.COLOR_YCrCb2BGR)
    if method == "clahe":
        ycrcb = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
        y, cr, cb = cv2.split(ycrcb)
        clahe = cv2.createCLAHE(
            clipLimit=float(cfg.get("clip_limit", 2.0)),
            tileGridSize=tuple(cfg.get("tile_grid", [8, 8])),
        )
        return cv2.cvtColor(cv2.merge((clahe.apply(y), cr, cb)), cv2.COLOR_YCrCb2BGR)
    raise ValueError(f"未知增强方法 {method}")


def normalize_image(img: np.ndarray, to_float: bool = True) -> np.ndarray:
    """归一化：0~255 → 0~1。模型吃 [0,1] 更稳；保存图片时才用 uint8。"""
    if to_float:
        return img.astype(np.float32) / 255.0
    return np.clip(img, 0, 255).astype(np.uint8)


# ─────────────────────────────── 去重 & 分组
def phash(img: np.ndarray, hash_size: int = 8) -> int:
    """感知哈希，用于粗筛重复图（重复图会让验证分数虚高）。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    small = cv2.resize(gray, (hash_size * 4, hash_size * 4), interpolation=cv2.INTER_AREA)
    dct = cv2.dct(np.float32(small))
    low = dct[:hash_size, :hash_size]
    med = np.median(low[1:], axis=None) if low.size > 1 else np.mean(low)
    bits = (low > med).flatten()
    out = 0
    for b in bits:
        out = (out << 1) | int(bool(b))
    return out


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def board_id_of(path: Path) -> str:
    """板号约定：文件名前缀到第一个下划线之前。`B0123_x01_y02.jpg` → `B0123`。"""
    return path.stem.split("_")[0]


def split_groups(items: list[tuple[Path, str]], cfg: dict) -> dict:
    """按板号成组划分，杜绝同一块板跨集合造成的数据泄漏。"""
    groups: dict[str, list[Path]] = defaultdict(list)
    for p, gid in items:
        groups[gid].append(p)
    keys = sorted(groups)
    random.Random(cfg.get("seed", 42)).shuffle(keys)

    n = len(keys)
    n_train = int(round(n * cfg.get("train", 0.7)))
    n_val = int(round(n * cfg.get("val", 0.2)))
    n_train = max(1, min(n_train, max(1, n - 1)))
    n_val = max(1, min(n_val, max(1, n - n_train)))

    out = {"train": [], "val": [], "test": []}
    for i, k in enumerate(keys):
        if i < n_train:
            out["train"].extend(groups[k])
        elif i < n_train + n_val:
            out["val"].extend(groups[k])
        else:
            out["test"].extend(groups[k])
    return out


# ─────────────────────────────── 主流程
def setup_logging(log_file: str) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(log_file, encoding="utf-8")],
    )


def process_one(img: np.ndarray, cfg: dict) -> np.ndarray:
    """单张图走完三步。返回 uint8 结果，便于保存。"""
    d = image_denoise(img, cfg["denoise"])
    e = image_enhance(d, cfg["enhance"])
    norm = normalize_image(e, to_float=bool(cfg["normalize"].get("to_float", True)))
    # 保存时统一回 uint8
    return (norm * 255.0).clip(0, 255).astype(np.uint8) if norm.dtype != np.uint8 else norm


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--compare", default="")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    setup_logging(cfg.get("log_file", "preprocess.log"))
    logging.info("配置载入完成：denoise=%s enhance=%s split=%s",
                 cfg["denoise"]["method"], cfg["enhance"]["method"], cfg["split"])

    if args.compare:
        p = Path(args.compare)
        if not p.exists():
            logging.error("对比图不存在：%s", p)
            return 2
        img = cv2.imread(str(p))
        out = process_one(img, cfg)
        side = np.hstack([cv2.resize(img, (640, 640)), cv2.resize(out, (640, 640))])
        dst = p.with_name(p.stem + "_compare.jpg")
        cv2.imwrite(str(dst), side)
        logging.info("处理前后对比已保存：%s", dst)
        return 0

    in_dir = Path(cfg["input_dir"])
    out_dir = Path(cfg["output_dir"])
    if not in_dir.exists():
        logging.error("输入目录不存在：%s（把待处理图放进去再跑）", in_dir.resolve())
        return 2

    exts = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
    files = [p for p in in_dir.rglob("*") if p.suffix.lower() in exts]
    logging.info("发现 %d 张待处理图", len(files))
    if not files:
        return 0

    # 去重
    kept: list[tuple[Path, str]] = []
    hashes: list[int] = []
    if cfg.get("dedup", {}).get("enabled"):
        thr = int(cfg["dedup"].get("hamming_threshold", 4))
        for p in files:
            img = cv2.imread(str(p))
            if img is None:
                logging.warning("读不了，跳过：%s", p)
                continue
            h = phash(img)
            if any(hamming(h, x) <= thr for x in hashes):
                logging.info("疑似重复，跳过：%s", p.name)
                continue
            hashes.append(h)
            kept.append((p, board_id_of(p)))
        logging.info("去重后保留 %d 张", len(kept))
    else:
        kept = [(p, board_id_of(p)) for p in files]

    splits = split_groups(kept, cfg["split"])
    for name, items in splits.items():
        logging.info("  %-5s %d 张 / %d 块板", name, len(items),
                     len({board_id_of(p) for p in items}))

    if args.dry_run:
        logging.info("dry-run：不写盘")
        return 0

    manifest = {"config": cfg, "splits": {}}
    for name, items in splits.items():
        d = out_dir / name
        d.mkdir(parents=True, exist_ok=True)
        names = []
        for p in items:
            img = cv2.imread(str(p))
            if img is None:
                continue
            out = process_one(img, cfg)
            dst = d / p.name
            cv2.imwrite(str(dst), out)
            names.append(p.name)
        manifest["splits"][name] = names
        logging.info("已写出 %s：%d 张", d, len(names))

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("完成。清单：%s", out_dir / "manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
