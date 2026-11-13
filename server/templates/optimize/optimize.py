# -*- coding: utf-8 -*-
"""
YOLO26m 优化模块：专家复核 + 模型自学习准入
==========================================
这两块是“针对企业提出的微小缺陷漏检多、质检精度低”做的优化，要点：

【专家复核 review() 修的三处】
  1. 空框/越界框会直接抛异常 → 先算宽高并做有效性检查，无效直接 reject
  2. 微小缺陷一刀切拒 → 面积改用像素占比，阈值 5e-5（约 640×640 下 20 像素见方）
  3. 贴板边的小缺陷被误杀 → 离板边 >3px 的转人工，只有真贴边的才 reject

【自学习 loop() 修的三处】
  1. 只看总分 → 增加逐类检查，任何一类退步超过 drop 就拒绝上线
  2. 新模型直接覆盖旧版 → 晋升前把旧 baseline/权重压进 history，可回退
  3. 首次运行没有 baseline 会 KeyError → 全部用 st.get() 带默认值

用法：
    python optimize.py selftest        # 不需要模型，验证逻辑
    python optimize.py review <img> <x1> <y1> <x2> <y2>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# ─────────────────────────────── 专家复核
MIN_AREA_RATIO = 5e-5      # 框占全图面积下限：再小就不是缺陷，是噪点
MAX_AREA_RATIO = 0.25      # 上限：超过四分之一图，基本是整块板误判
MIN_ASPECT = 0.1           # 宽高比下限（细长裂纹也算合格）
MAX_ASPECT = 10.0          # 宽高比上限
MIN_TEXTURE = 3.0          # 框内纹理丰富度下限：太平 → 没有缺陷特征
EDGE_GUARD = 3             # 离板边多少像素内算“贴边”


def review(b, img):
    """判定一个标注框/检测框该接受、转人工还是驳回。

    b: (x1, y1, x2, y2)  像素坐标
    img: PIL.Image
    返回: 'accept' | 'manual' | 'reject'
    """
    w, h = img.size
    bw, bh = b[2] - b[0], b[3] - b[1]

    # ① 先做有效性检查，避免后续除法/裁剪炸掉
    if bw <= 0 or bh <= 0 or b[0] < 0 or b[1] < 0 or b[2] > w or b[3] > h:
        return "reject"

    # ② 框内纹理丰富度：相邻像素灰度差均值，纹理越丰富数值越大
    gray = img.convert("L").crop((b[0], b[1], b[2], b[3]))
    p = list(gray.tobytes())
    if not p:
        return "reject"
    g = sum(abs(p[i + 1] - p[i]) for i in range(len(p) - 1)) / len(p)

    # ③ 面积占比 + 宽高比（按像素算，不按绝对面积，跨分辨率才稳）
    area_ratio = bw * bh / float(w * h)
    aspect = bw / float(max(1, bh))
    ok = MIN_AREA_RATIO < area_ratio < MAX_AREA_RATIO and MIN_ASPECT < aspect < MAX_ASPECT

    if ok and g > MIN_TEXTURE:
        return "accept"
    # 贴板边的可能是真缺陷但特征不全，宁可交给人看，不要直接丢
    near_edge = min(b[0], b[1], w - b[2], h - b[3])
    return "reject" if near_edge <= EDGE_GUARD else "manual"


# ─────────────────────────────── 自学习准入
MIN_SAMPLES = 30
PROMOTE_MARGIN = 0.005     # 总分至少要提升这么多才考虑上线
PER_CLASS_DROP = 0.02      # 单类允许的退步幅度


def loop(imgs, lbls, st, train_fn=None, eval_fn=None,
         mg=PROMOTE_MARGIN, drop=PER_CLASS_DROP):
    """训练准入闸门。返回 (结论字符串, 新状态)。

    st 结构：{"baseline": float, "weights": str, "per_class": {cls: float}, "history": [...]}
    train_fn(imgs, lbls) -> weights
    eval_fn(weights) -> (score, per_class_dict)
    """
    n = count_samples(imgs, lbls)
    if n < MIN_SAMPLES:
        return f"样本不足（{n}/{MIN_SAMPLES}），不训练", st

    if train_fn is None or eval_fn is None:
        return "未提供 train_fn/eval_fn，仅完成样本检查", st

    w = train_fn(imgs, lbls)
    s, per = eval_fn(w)

    baseline = float(st.get("baseline", 0.0) or 0.0)
    if s < baseline + mg:
        return f"没过基线（{s:.4f} < {baseline:.4f}+{mg}），拒绝上线", st

    prev_per = st.get("per_class", {}) or {}
    regressed = [c for c in per if per[c] < float(prev_per.get(c, 0.0) or 0.0) - drop]
    if regressed:
        return f"有类别退步 {regressed}，拒绝上线", st

    # 晋升：先把旧版本压进 history，出问题能回退
    st.setdefault("history", []).append({
        "baseline": st.get("baseline"),
        "weights": st.get("weights"),
        "per_class": prev_per,
    })
    st["baseline"], st["weights"], st["per_class"] = s, w, per
    return "已晋升", st


def count_samples(imgs, lbls) -> int:
    """统计有标注的有效样本数（图片有对应 txt 且非空）。"""
    n = 0
    labels = Path(lbls)
    for p in sorted(Path(imgs).glob("*.jpg")):
        f = labels / f"{p.stem}.txt"
        if f.exists() and f.read_text(encoding="utf-8", errors="ignore").strip():
            n += 1
    return n


# ─────────────────────────────── 自检
def selftest() -> int:
    from PIL import Image
    import random

    print("=" * 58)
    print("专家复核 + 自学习准入 自检")
    print("=" * 58)
    ok = 0
    fail = 0

    def check(name, cond, detail=""):
        nonlocal ok, fail
        print(f"  [{'OK' if cond else 'FAIL'}] {name:<28} {detail}")
        ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)

    # 造一张带纹理的图（检查“纹理不足”那条）
    img = Image.new("L", (640, 640), 128)
    px = img.load()
    for y in range(640):
        for x in range(640):
            px[x, y] = (x * 7 + y * 13) % 256
    flat = Image.new("RGB", (640, 640), (120, 120, 120))
    img = img.convert("RGB")

    check("空框", review((100, 100, 100, 120), img) == "reject")
    check("越界框", review((-5, 10, 100, 100), img) == "reject")
    check("超小框", review((300, 300, 301, 301), img) == "reject")
    check("超大框", review((0, 0, 639, 639), img) == "reject")
    check("正常带纹理框", review((200, 200, 260, 250), img) == "accept")
    check("平坦无纹理→转人工", review((200, 200, 260, 250), flat) == "manual")

    # 自学习闸门
    print("\n  样本数闸门：")
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        im = Path(td) / "imgs"; lb = Path(td) / "lbls"
        im.mkdir(); lb.mkdir()
        for i in range(10):
            (im / f"B{i}_0.jpg").write_bytes(b"x")
            (lb / f"B{i}_0.txt").write_text("0 0.5 0.5 0.1 0.1", encoding="utf-8")
        r, _ = loop(str(im), str(lb), {})
        check("样本<30 不训练", "样本不足" in r, r)

    print("\n  晋升闸门：")
    st = {"baseline": 0.90, "weights": "old.pt", "per_class": {"干节": 0.9, "裂纹": 0.8}}
    r, st2 = loop("x", "y", dict(st),
                  train_fn=lambda a, b: "new.pt",
                  eval_fn=lambda w: (0.91, {"干节": 0.92, "裂纹": 0.81}))
    check("总分达标+无退步→晋升", r == "已晋升" and len(st2.get("history", [])) == 1, r)

    r, _ = loop("x", "y", dict(st),
                train_fn=lambda a, b: "new.pt",
                eval_fn=lambda w: (0.901, {"干节": 0.92, "裂纹": 0.81}))
    check("提升不足基线+0.005", "没过基线" in r, r)

    r, _ = loop("x", "y", dict(st),
                train_fn=lambda a, b: "new.pt",
                eval_fn=lambda w: (0.95, {"干节": 0.92, "裂纹": 0.70}))
    check("单类退步20pt→拒绝", "退步" in r, r)

    r, _ = loop("x", "y", {},                 # 首次运行没有 baseline
                train_fn=lambda a, b: "new.pt",
                eval_fn=lambda w: (0.80, {"干节": 0.8}))
    check("无历史基线不崩", isinstance(r, str), r)

    print("=" * 58)
    print(f"通过 {ok} 项，失败 {fail} 项")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        sys.exit(selftest())
    if len(sys.argv) > 6 and sys.argv[1] == "review":
        from PIL import Image
        img = Image.open(sys.argv[2])
        b = tuple(int(v) for v in sys.argv[3:7])
        print(review(b, img))
        sys.exit(0)
    print(__doc__)
