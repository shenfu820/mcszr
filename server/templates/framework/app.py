# -*- coding: utf-8 -*-
"""
板材表面缺陷检测 · 应用框架
==========================
这是“3号同事”要的骨架：拿它能直接起一个可用的检测程序，四个部分都在：

  1. 输入源    图片 / 视频 / 摄像头（工业线扫相机接入点留了钩子）
  2. 推理      加载 .pt 权重，按 settings 里的 conf/iou/imgsz 跑
  3. 联动      检出缺陷 → 画框 + 触发报警灯（串口，可选）+ 落库
  4. 落库      独立 sqlite，不与产线库混写

设计上刻意不依赖主系统：删掉这个文件对产线没有任何影响；
要接产线库/相机，只需改 Config 里的两行。

用法：
    python app.py --source image --path ../output/raw/a.jpg
    python app.py --source video --path demo.mp4 --save
    python app.py --source camera --index 0
    python app.py --selftest          # 不装模型也能验证骨架
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ─────────────────────────────── 配置
@dataclass
class Config:
    model_path: str = "yolo26m_wood_best.pt"
    conf: float = 0.35
    iou: float = 0.45
    imgsz: int = 640
    max_det: int = 300
    db_path: str = "detect.db"
    out_dir: str = "output"
    save_image: bool = True
    alarm_enabled: bool = False
    alarm_port: str = "COM8"
    class_cn: dict = field(default_factory=lambda: {
        "dry_knot": "干节", "sound_knot": "健全节", "edge_knot": "边界",
        "small_knot": "小节", "split": "裂纹", "wave": "波纹",
    })

    @classmethod
    def load(cls, path: str = "config.json") -> "Config":
        c = cls()
        p = Path(path)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            for k, v in data.items():
                if hasattr(c, k):
                    setattr(c, k, v)
        return c


# ─────────────────────────────── 存储
SCHEMA = """
create table if not exists sessions (
  id integer primary key autoincrement,
  started_at text not null, ended_at text,
  source_type text not null, source_name text default '',
  model_name text default '',
  total_frames integer default 0, defect_frames integer default 0,
  total_defects integer default 0, max_conf real default 0, avg_conf real default 0
);
create table if not exists detections (
  id integer primary key autoincrement,
  session_id integer, ts text not null,
  class_en text, class_cn text, confidence real,
  x1 integer, y1 integer, x2 integer, y2 integer,
  frame_no integer default 0
);
create index if not exists idx_det_ts on detections(ts);
"""


class Store:
    """独立库。绝不写产线 wood_inspect.db。"""

    def __init__(self, path: str):
        self.path = path
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self.con.commit()

    def start_session(self, source_type: str, name: str, model: str) -> int:
        cur = self.con.execute(
            "insert into sessions(started_at, source_type, source_name, model_name) values(?,?,?,?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), source_type, name, model))
        self.con.commit()
        return cur.lastrowid

    def add_detection(self, sid: int, det: dict, frame_no: int = 0) -> None:
        self.con.execute(
            "insert into detections(session_id, ts, class_en, class_cn, confidence, "
            "x1,y1,x2,y2, frame_no) values(?,?,?,?,?,?,?,?,?,?)",
            (sid, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             det.get("class_en", ""), det.get("class_cn", ""), float(det.get("confidence", 0)),
             *[int(v) for v in det.get("box", (0, 0, 0, 0))], frame_no))
        self.con.commit()

    def end_session(self, sid: int, frames: int, defect_frames: int,
                    defects: int, max_conf: float, avg_conf: float) -> None:
        self.con.execute(
            "update sessions set ended_at=?, total_frames=?, defect_frames=?, "
            "total_defects=?, max_conf=?, avg_conf=? where id=?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), frames, defect_frames,
             defects, max_conf, avg_conf, sid))
        self.con.commit()

    def close(self) -> None:
        self.con.close()


# ─────────────────────────────── 报警灯
class Alarm:
    """红灯 2Hz + 蜂鸣 1Hz。串口不在就静默降级，不影响检测。"""

    def __init__(self, enabled: bool, port: str, duration: float = 5.0):
        self.enabled = enabled
        self.port = port
        self.duration = duration
        self.ser = None
        if enabled:
            try:
                import serial
                self.ser = serial.Serial(port, 9600, timeout=0.2)
            except Exception as e:
                print(f"[报警] 串口打开失败（{port}）：{e} —— 降级为只打印")
                self.ser = None

    def trigger(self, reason: str = "") -> None:
        print(f"[报警] {reason}")
        if self.ser:
            try:
                self.ser.write(b"\x01")   # 协议按现场报警灯说明书改
                self.ser.write(b"\x00")
            except Exception as e:
                print(f"[报警] 写入失败：{e}")

    def close(self) -> None:
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass


# ─────────────────────────────── 推理
class Detector:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.model = None
        self.names = {}

    def load(self) -> bool:
        try:
            from ultralytics import YOLO
            self.model = YOLO(self.cfg.model_path)
            self.names = getattr(self.model, "names", {}) or {}
            print(f"[模型] 已加载 {self.cfg.model_path}，类别 {len(self.names)} 个：{list(self.names.values())[:8]}")
            return True
        except Exception as e:
            print(f"[模型] 加载失败：{type(e).__name__}: {e}")
            return False

    def infer(self, frame):
        """返回 [{class_en, class_cn, confidence, box}]。模型没加载就返回空。"""
        if self.model is None:
            return []
        r = self.model.predict(
            frame, conf=self.cfg.conf, iou=self.cfg.iou, imgsz=self.cfg.imgsz,
            max_det=self.cfg.max_det, verbose=False,
        )[0]
        out = []
        boxes = getattr(r, "boxes", None)
        if boxes is None:
            return out
        for b in boxes:
            cls_id = int(b.cls[0])
            en = self.names.get(cls_id, str(cls_id))
            xyxy = [int(v) for v in b.xyxy[0].tolist()]
            out.append({
                "class_en": en,
                "class_cn": self.cfg.class_cn.get(en, en),
                "confidence": float(b.conf[0]),
                "box": xyxy,
            })
        return out


# ─────────────────────────────── 主循环
def draw(frame, dets):
    import cv2
    for d in dets:
        x1, y1, x2, y2 = d["box"]
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
        label = f'{d["class_cn"]} {d["confidence"]:.2f}'
        cv2.putText(frame, label, (x1, max(18, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    return frame


def run(cfg: Config, source: str, path: str, index: int, save: bool) -> int:
    import cv2

    store = Store(cfg.db_path)
    alarm = Alarm(cfg.alarm_enabled, cfg.alarm_port)
    det = Detector(cfg)
    det.load()
    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sid = store.start_session(source, path or str(index), cfg.model_path)

    frames = defect_frames = defects = 0
    max_conf = 0.0
    conf_sum = 0.0
    t0 = time.time()

    try:
        if source == "image":
            img = cv2.imread(path)
            if img is None:
                print(f"[错误] 读不到图片：{path}")
                return 2
            frames = 1
            ds = det.infer(img)
            if ds:
                defect_frames = 1
                defects = len(ds)
                max_conf = max(d["confidence"] for d in ds)
                conf_sum = sum(d["confidence"] for d in ds)
                for d in ds:
                    store.add_detection(sid, d, 0)
                alarm.trigger(f"{path} 检出 {len(ds)} 处缺陷")
            vis = draw(img.copy(), ds)
            if save:
                dst = out_dir / f"det_{Path(path).stem}.jpg"
                cv2.imwrite(str(dst), vis)
                print(f"[输出] {dst}")
            print(f"[结果] {len(ds)} 处缺陷")

        elif source in ("video", "camera"):
            cap = cv2.VideoCapture(path if source == "video" else index)
            if not cap.isOpened():
                print(f"[错误] 打不开输入源：{path or index}")
                return 2
            n = 0
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                n += 1
                frames = n
                ds = det.infer(frame)
                if ds:
                    defect_frames += 1
                    defects += len(ds)
                    for d in ds:
                        store.add_detection(sid, d, n)
                        conf_sum += d["confidence"]
                        max_conf = max(max_conf, d["confidence"])
                    alarm.trigger(f"第 {n} 帧 {len(ds)} 处缺陷")
                vis = draw(frame, ds)
                if save and ds:
                    dst = out_dir / f"frame_{n:06d}.jpg"
                    cv2.imwrite(str(dst), vis)
                if n % 30 == 0:
                    el = time.time() - t0
                    print(f"[进度] {n} 帧，{n/max(el,1e-6):.1f} FPS")
            cap.release()

        else:
            print(f"[错误] 未知输入源 {source}")
            return 2

    finally:
        avg = (conf_sum / defects) if defects else 0.0
        store.end_session(sid, frames, defect_frames, defects, max_conf, avg)
        store.close()
        alarm.close()
        el = time.time() - t0
        print(f"[完成] session={sid} 帧={frames} 缺陷帧={defect_frames} "
              f"缺陷={defects} 耗时={el:.1f}s")
    return 0


def selftest() -> int:
    print("=" * 56)
    print("应用框架 自检（不需要模型）")
    print("=" * 56)
    import tempfile
    fails = []
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "t.db")
        s = Store(db)
        sid = s.start_session("image", "unit", "none.pt")
        print(f"[OK] 建库+开会话 session={sid}")
        s.add_detection(sid, {"class_en": "split", "class_cn": "裂纹",
                              "confidence": 0.8, "box": (1, 2, 30, 40)}, 0)
        n = s.con.execute("select count(*) from detections").fetchone()[0]
        print(f"[{'OK' if n == 1 else 'FAIL'}] 写检测记录 {n} 条")
        if n != 1:
            fails.append("store")
        s.end_session(sid, 1, 1, 1, 0.8, 0.8)
        row = s.con.execute("select total_defects, max_conf from sessions").fetchone()
        print(f"[OK] 收会话 缺陷={row[0]} max_conf={row[1]}")
        s.close()

        cfg = Config()
        cfg.model_path = os.path.join(td, "nope.pt")
        d = Detector(cfg)
        loaded = d.load()
        print(f"[{'OK' if not loaded else 'WARN'}] 模型缺失时优雅降级 loaded={loaded}")
        print(f"[OK] 空推理返回 {d.infer(None)}")

        a = Alarm(False, "COM_NONE")
        a.trigger("自检")
        a.close()
        print("[OK] 报警降级")

    print("=" * 56)
    print("自检通过" if not fails else f"失败：{fails}")
    return 0 if not fails else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--source", default="image", choices=["image", "video", "camera"])
    ap.add_argument("--path", default="")
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if args.selftest:
        return selftest()

    cfg = Config.load(args.config)
    return run(cfg, args.source, args.path, args.index, args.save)


if __name__ == "__main__":
    sys.exit(main())
