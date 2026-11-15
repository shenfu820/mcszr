# -*- coding: utf-8 -*-
"""
数字人 · GPU 作业执行器（worker）
=================================
这个脚本由 **yoloces 环境** 的 python 跑起来（torch 2.14.1+cu130，cuda=True），
它才是真正把 GPU 转起来的那一层：

  train  : 用 YOLO26m 在真实板材数据集上做微调，跑到设定轮数自己退出
  eval   : 在验证集上跑一遍 mAP，产出真实指标
  infer  : 对一批真实板材图跑检测，统计缺陷数

设计约束（红线）：
  - 只读产线的权重与数据集，训练产物全部写进本次作业的 workdir，
    **绝不写 new mcjc/models/**，不覆盖 yolo26m_wood_best.pt。
  - 每个阶段都把进度追加进 workdir/progress.jsonl，上层只读这个文件，
    所以父进程随时能看到「跑到第几轮、GPU 是否真的在动」。
  - 跑完就退；没有常驻循环，所以「转一会自己停」是天然成立的。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


class Progress:
    """把进度同时写进 progress.jsonl 和 stdout，父进程两边都能看。"""

    def __init__(self, workdir: Path):
        self.path = workdir / "progress.jsonl"
        self.t0 = time.time()

    def emit(self, **kw) -> None:
        rec = {"t": round(time.time() - self.t0, 2), "at": now(), **kw}
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        try:
            print(f"[{rec['at']}] {line}", flush=True)
        except Exception:
            pass


CLASSES = ["dry_knot", "sound_knot", "edge_knot", "small_knot", "split", "wave"]

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import dh_paths  # noqa: E402  跨机器路径：环境变量 > config/paths.ini > 自动探测

DS_ROOT = dh_paths.ds_root()   # ASCII 软链优先 -> 数据集（绕开中文路径）

TEMPLATE = """# 由数字人 GPU worker 自动生成，勿手改
path: {root}
train: images/train
val: images/val
test: images/val
nc: {nc}
names: {names}
"""


def make_data_yaml(workdir: Path) -> str:
    """数据集原始 wood.yaml 里是作者机器上的 Linux 绝对路径，不能用。

    这里按 ASCII 软链重新生成一份，路径全部是 C:/dh_ds，避免中文路径与
    Windows 命令行编码互相踩脚。
    """
    if not Path(DS_ROOT).exists():
        raise FileNotFoundError(
            f"数据集根不存在：{DS_ROOT}（在 config/paths.ini 里写 DATASET_ROOT=数据集目录，"
            f"或设环境变量 DH_DS；启动脚本会自动建 C:\\dh_ds 软链）")
    p = workdir / "data.yaml"
    p.write_text(TEMPLATE.format(root=DS_ROOT, nc=len(CLASSES), names=CLASSES), encoding="utf-8")
    return p.as_posix()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="train", choices=["train", "eval", "infer"])
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--data", default="", help="留空则按数据集根自动生成 data.yaml")
    ap.add_argument("--weights", default=dh_paths.prod_weights())
    ap.add_argument("--source", default=dh_paths.infer_src(), help="infer 模式的图片目录")
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--imgsz", type=int, default=320)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--fraction", type=float, default=0.12, help="只喂一部分数据，保证现场几十秒级")
    ap.add_argument("--device", default="0")
    ap.add_argument("--conf", type=float, default=0.25)
    args = ap.parse_args()

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    P = Progress(workdir)

    data_yaml = args.data or make_data_yaml(workdir)
    t_start = time.time()
    P.emit(stage="boot", mode=args.mode, weights=Path(args.weights).name, data=data_yaml,
           epochs=args.epochs, imgsz=args.imgsz, batch=args.batch, fraction=args.fraction)

    try:
        import torch
        from ultralytics import YOLO
        P.emit(stage="torch", torch=torch.__version__, cuda=torch.cuda.is_available(),
               gpu=torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU")
        if not torch.cuda.is_available():
            P.emit(stage="error", error="CUDA 不可用，拒绝假跑")
            return 3

        if args.mode == "train":
            def on_epoch_end(trainer):
                ep = int(getattr(trainer, "epoch", 0)) + 1
                loss = trainer.label_loss_items(trainer.tloss, prefix="train") if hasattr(trainer, "tloss") else {}
                P.emit(stage="epoch", epoch=ep, epochs=args.epochs,
                       gpu_mem_mb=round(torch.cuda.memory_reserved() / 1048576, 0),
                       loss={k: round(float(v), 4) for k, v in (loss or {}).items()})

            def on_fit_end(trainer):
                m = getattr(trainer, "metrics", {}) or {}
                P.emit(stage="metrics",
                       mAP50=round(float(m.get("metrics/mAP50(B)", 0) or 0), 4),
                       mAP50_95=round(float(m.get("metrics/mAP50-95(B)", 0) or 0), 4),
                       precision=round(float(m.get("metrics/precision(B)", 0) or 0), 4),
                       recall=round(float(m.get("metrics/recall(B)", 0) or 0), 4))

            model = YOLO(args.weights)
            model.add_callback("on_train_epoch_end", on_epoch_end)
            model.add_callback("on_fit_epoch_end", on_fit_end)
            r = model.train(
                data=data_yaml, epochs=args.epochs, imgsz=args.imgsz, batch=args.batch,
                device=args.device, workers=0, fraction=args.fraction,
                amp=False,   # 关掉 AMP：ultralytics 的 AMP 自检会去外网下 yolo26n.pt，现场断网就卡死
                project=str(workdir / "runs"), name="train", exist_ok=True,
                plots=False, val=True, verbose=False, seed=0,
            )
            best = Path(str(getattr(r, "save_dir", workdir / "runs" / "train"))) / "weights" / "best.pt"
            P.emit(stage="done", secs=round(time.time() - t_start, 1),
                   best_pt=str(best), exists=best.exists(),
                   note="权重只落在本次作业目录，未触碰产线 models/")

        elif args.mode == "eval":
            model = YOLO(args.weights)
            m = model.val(data=data_yaml, imgsz=args.imgsz, batch=args.batch,
                          device=args.device, workers=0, split="val", verbose=False, plots=False)
            P.emit(stage="done", secs=round(time.time() - t_start, 1),
                   mAP50=round(float(m.box.map50), 4), mAP50_95=round(float(m.box.map), 4),
                   precision=round(float(m.box.mp), 4), recall=round(float(m.box.mr), 4),
                   classes=[m.names[i] for i in range(len(m.names))] if hasattr(m, "names") else [])

        else:  # infer
            src = args.source or r"C:\Users\Administrator\Desktop\项目与竞赛\aa比赛项目\new mcjc\data\real_boards\images"
            P.emit(stage="infer", source=src)
            model = YOLO(args.weights)
            res = model.predict(source=src, imgsz=args.imgsz, conf=args.conf,
                                device=args.device, save=True, verbose=False,
                                project=str(workdir / "runs"), name="predict", exist_ok=True)
            boxes = sum(len(getattr(r_, "boxes", []) or []) for r_ in res)
            kinds: dict[str, int] = {}
            for r_ in res:
                for b in (getattr(r_, "boxes", []) or []):
                    k = r_.names[int(b.cls[0])]
                    kinds[k] = kinds.get(k, 0) + 1
            P.emit(stage="done", secs=round(time.time() - t_start, 1),
                   images=len(res), boxes=boxes, kinds=kinds,
                   out=str(workdir / "runs" / "predict"))

        return 0

    except Exception as e:
        P.emit(stage="error", error=f"{type(e).__name__}: {e}", tb=traceback.format_exc()[-1200:])
        return 2


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
