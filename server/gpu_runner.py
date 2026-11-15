# -*- coding: utf-8 -*-
"""
数字人 · GPU 作业调度器（父进程侧）
===================================
数字人「听懂一句话 → 把 GPU 真的转起来 → 跑完自己停」这条链路里，
这里是「调度」那一层：只负责起进程、读进度、收尸，不碰模型代码。

对外三个动作：
  start(mode, **params)  起一个真实 GPU 作业（同一时刻只允许一个）
  status()               读进度（state: idle/running/done/failed）
  stop()                 现场需要中断时用（自保，不是常规路径）

进度来源是 worker 写的 progress.jsonl，所以「GPU 有没有真在动」不是演出来的：
上层面板读的是同一份文件 + nvidia-smi 实时采样。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

_RUNTIME = Path("C:/dh_root")                            # ASCII 软链 -> 数字人/
ROOT = _RUNTIME if _RUNTIME.exists() else Path(__file__).resolve().parent.parent
JOBS_DIR = ROOT / "ops_jobs"
WORKER = ROOT / "server" / "gpu_worker.py"

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import dh_paths  # noqa: E402  跨机器路径：环境变量 > config/paths.ini > 自动探测

YOLOCES_PY = Path(dh_paths.gpu_python())                 # 带 CUDA 的环境，不再写死

# 这三个必须全 ASCII：Windows 命令行传中文参数会被 PowerShell/cmd 的代码页吃掉，
# 之前踩过——worker 收到的是 "F:\鏈ㄦ潗..."，数据集直接找不到。
DATA_YAML = ""                                           # 留空：worker 自己生成 data.yaml
PROD_WEIGHTS = dh_paths.prod_weights()                   # 只读引用，绝不写回
INFER_SRC = dh_paths.infer_src()

MODES = {
    "train": {"label": "微调训练", "epochs": 2, "imgsz": 320, "batch": 8, "fraction": 0.12},
    "eval": {"label": "验证集评估", "epochs": 0, "imgsz": 320, "batch": 8, "fraction": 0},
    "infer": {"label": "批量推理", "epochs": 0, "imgsz": 320, "batch": 8, "fraction": 0},
}


class JobRunner:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.job: dict | None = None
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        self._adopt_last()

    def _adopt_last(self) -> None:
        """进程重启后把上一次作业记录下来，运维屏不会显示成「从来没过作业」。

        注意：这只是展示用，真的在跑的进程不归这个 runner 管（重启前就断了），
        所以状态一律按 progress.jsonl 的收尾记录判定为 done / failed。
        """
        try:
            dirs = [d for d in JOBS_DIR.iterdir() if d.is_dir() and not d.name.startswith("_")]
        except Exception:
            return
        if not dirs:
            return
        last = max(dirs, key=lambda d: d.stat().st_mtime)
        mode = last.name.split("_")[0]
        p = last / "progress.jsonl"
        if not p.exists():
            return
        try:
            recs = [json.loads(ln) for ln in p.read_text(encoding="utf-8", errors="replace").strip().splitlines()]
        except Exception:
            return
        boot = next((r for r in recs if r.get("stage") == "boot"), {}) or {}
        last_t = float(recs[-1].get("t") or 0) if recs else 0.0
        self.job = {
            "job_id": last.name.replace(mode + "_", ""), "mode": mode,
            "label": MODES.get(mode, {}).get("label", mode),
            "workdir": str(last), "log": str(last / "worker.log"), "pid": None,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last.stat().st_mtime)),
            "t0": time.time() - last_t,          # 让 elapsed 显示成这次作业的真实耗时
            "params": {k: boot.get(k) for k in
                       ("epochs", "imgsz", "batch", "fraction", "data", "weights")},
            "_logf": None, "_adopted": True,
        }

    # ────────────────────────────── 起作业
    def start(self, mode: str = "train", **params) -> dict:
        mode = (mode or "train").strip().lower()
        if mode not in MODES:
            return {"ok": False, "error": f"未知作业类型 {mode}，可选 {list(MODES)}"}
        if not YOLOCES_PY.exists():
            return {"ok": False, "error": f"找不到 CUDA 环境解释器 {YOLOCES_PY}"}
        if not WORKER.exists():
            return {"ok": False, "error": f"找不到 worker {WORKER}"}

        with self.lock:
            if self.proc is not None and self.proc.poll() is None:
                return {"ok": False, "busy": True, "job": self._view(),
                        "error": "上一个作业还在跑，我不重复起"}

            jid = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]
            workdir = JOBS_DIR / f"{mode}_{jid}"
            workdir.mkdir(parents=True, exist_ok=True)
            log_path = workdir / "worker.log"

            cfg = dict(MODES[mode])
            epochs = int(params.get("epochs") or cfg["epochs"])
            imgsz = int(params.get("imgsz") or cfg["imgsz"])
            batch = int(params.get("batch") or cfg["batch"])
            fraction = float(params.get("fraction") or cfg["fraction"])
            data = params.get("data") or DATA_YAML
            weights = params.get("weights") or PROD_WEIGHTS
            source = params.get("source") or INFER_SRC

            cmd = [str(YOLOCES_PY), str(WORKER),
                   "--mode", mode, "--workdir", str(workdir),
                   "--data", data, "--weights", weights,
                   "--epochs", str(epochs), "--imgsz", str(imgsz),
                   "--batch", str(batch), "--fraction", str(fraction),
                   "--device", "0", "--source", source]

            logf = open(log_path, "w", encoding="utf-8", errors="replace")
            env = dict(os.environ, PYTHONIOENCODING="utf-8", YOLO_VERBOSE="False")
            flags = 0
            if os.name == "nt":
                flags = subprocess.CREATE_NEW_PROCESS_GROUP  # 便于整组收掉
            proc = subprocess.Popen(cmd, cwd=str(workdir), stdout=logf,
                                    stderr=subprocess.STDOUT, env=env,
                                    creationflags=flags)
            self.proc = proc
            self.job = {
                "job_id": jid, "mode": mode, "label": cfg["label"],
                "workdir": str(workdir), "log": str(log_path),
                "pid": proc.pid, "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "t0": time.time(), "params": {"epochs": epochs, "imgsz": imgsz,
                                              "batch": batch, "fraction": fraction,
                                              "data": data, "weights": weights},
                "_logf": logf,
            }
            return {"ok": True, "job": self._view()}

    # ────────────────────────────── 读进度
    def _records(self, limit: int = 400) -> list:
        if not self.job:
            return []
        p = Path(self.job["workdir"]) / "progress.jsonl"
        if not p.exists():
            return []
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        except Exception:
            return []
        out = []
        for ln in lines[-limit:]:
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        return out

    def _view(self) -> dict:
        if not self.job:
            return {"state": "idle"}
        recs = self._records()
        running = self.proc is not None and self.proc.poll() is None
        rc = None if running else (self.proc.poll() if self.proc else None)
        err = next((r for r in reversed(recs) if r.get("stage") == "error"), None)
        done = next((r for r in reversed(recs) if r.get("stage") == "done"), None)
        # 指标分两处：训练中每轮一条 metrics，收尾那条 done 只带 secs/best_pt，这里合并
        merged = {}
        for r in recs:
            if r.get("stage") == "metrics":
                merged.update({k: r[k] for k in ("mAP50", "mAP50_95", "precision", "recall") if r.get(k) is not None})
        if done:
            merged.update({k: done[k] for k in ("mAP50", "mAP50_95", "precision", "recall",
                                                "best_pt", "boxes", "images", "kinds", "secs")
                           if done.get(k) is not None})
        metrics = merged
        epochs_seen = [r for r in recs if r.get("stage") == "epoch"]
        last = recs[-1] if recs else {}

        state = "running" if running else ("failed" if (err or (rc not in (0, None))) else "done")
        return {
            "state": state,
            "job_id": self.job["job_id"], "mode": self.job["mode"], "label": self.job["label"],
            "pid": self.job["pid"], "started_at": self.job["started_at"],
            "elapsed": round(time.time() - self.job["t0"], 1),
            "exit_code": rc,
            "params": self.job["params"],
            "stage": last.get("stage"),
            "epoch": (last.get("epoch") if last.get("stage") == "epoch" else
                      (len(epochs_seen) or None)),
            "epochs": self.job["params"]["epochs"],
            "metrics": {k: metrics.get(k) for k in
                        ("mAP50", "mAP50_95", "precision", "recall", "best_pt", "boxes", "images", "kinds", "secs")
                        if metrics.get(k) is not None},
            "error": (err or {}).get("error"),
            "log_tail": Path(self.job["log"]).read_text(encoding="utf-8", errors="replace").strip().splitlines()[-6:]
                        if Path(self.job["log"]).exists() else [],
            "workdir": self.job["workdir"],
        }

    def status(self) -> dict:
        with self.lock:
            v = self._view()
            if v.get("state") != "running" and self.job and self.job.get("_logf"):
                try:
                    self.job["_logf"].close()
                except Exception:
                    pass
                self.job["_logf"] = None
            return v

    def stop(self) -> dict:
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                return {"ok": True, "note": "没有在跑的作业"}
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                                   capture_output=True)
                else:
                    self.proc.terminate()
            except Exception as e:
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}
            return {"ok": True, "note": "已中断作业"}


RUNNER = JobRunner()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    mode = sys.argv[1] if len(sys.argv) > 1 else "train"
    r = RUNNER.start(mode)
    print("start:", json.dumps(r, ensure_ascii=False, default=str)[:400])
    while True:
        s = RUNNER.status()
        print(f"  [{s.get('state')}] stage={s.get('stage')} epoch={s.get('epoch')}/{s.get('epochs')} elapsed={s.get('elapsed')}s")
        if s.get("state") != "running":
            print("final:", json.dumps(s, ensure_ascii=False, default=str)[:800])
            break
        time.sleep(5)
