# -*- coding: utf-8 -*-
"""
数字人 · 身边的运维屏数据源
===========================
这块屏只回答三个问题，全部用真数据，不画饼：

  1. 模型性能在往哪走？   → ai_learning_runs.metrics.map50 序列 + sessions.avg_conf 序列
  2. GPU 现在在干什么？   → nvidia-smi 实时采样（利用率/显存/温度/功耗）
  3. 这一句指令的活干完没？→ gpu_runner 的作业状态（进度/指标/日志尾巴）

数据库只读；GPU 采样靠 nvidia-smi，取不到就明确说取不到，不编。
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import tools  # 复用只读 db()

_CACHE: dict = {"gpu": (0.0, None)}
GPU_TTL = 1.2  # 秒，前端 2s 轮一次，够用


def _num(x, cast=float):
    """nvidia-smi 在某些笔记本 GPU 上会返回 [N/A]（功耗/温度常见），不能因此整条采样失败。"""
    try:
        s = str(x).strip()
        if not s or s.upper().startswith("[N/A"):
            return None
        return cast(float(s))
    except Exception:
        return None


# ────────────────────────────────────────── GPU
def gpu() -> dict:
    now = time.time()
    ts, cached = _CACHE["gpu"]
    if cached and now - ts < GPU_TTL:
        return cached
    q = "utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit"
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        line = (out.stdout or "").strip().splitlines()[0]
        v = [x.strip() for x in line.split(",")]
        used, total = _num(v[1], int), _num(v[2], int)
        data = {
            "ok": True,
            "util": _num(v[0], int) or 0,
            "mem_used": used or 0,
            "mem_total": total or 0,
            "mem_pct": round((used or 0) / max(1, total or 1) * 100, 1),
            "temp": _num(v[3], int),
            "power": _num(v[4]),
            "power_limit": _num(v[5]),
            "at": time.strftime("%H:%M:%S"),
        }
    except Exception as e:
        data = {"ok": False, "error": f"{type(e).__name__}: {e}", "util": 0,
                "mem_used": 0, "mem_total": 0, "mem_pct": 0, "temp": 0,
                "power": 0, "power_limit": 0, "at": time.strftime("%H:%M:%S")}
    _CACHE["gpu"] = (now, data)
    return data


# ────────────────────────────────────────── 模型性能趋势
def trend(limit: int = 30) -> dict:
    """模型性能趋势：自学习每一轮的 mAP50 + 会话平均置信度。

    这是「模型性能检测趋势图」的数据源，全部来自本地库，可回查。
    """
    out = {"ok": True, "learning": [], "sessions": [], "daily": [], "source": []}
    try:
        con = tools.db()
        cur = con.cursor()

        # 1) 自学习轮次：metrics 里带 map50
        rows = cur.execute(
            "select id, started_at, status, metrics, trigger from ai_learning_runs "
            "order by id desc limit ?", (max(3, limit),)
        ).fetchall()
        for rid, ts_, status, metrics, trigger in reversed(rows):
            m = {}
            try:
                m = json.loads(metrics or "{}")
            except Exception:
                m = {}
            out["learning"].append({
                "id": rid, "at": ts_, "status": status, "trigger": trigger,
                "map50": m.get("map50"), "map50_95": m.get("map50_95"),
                "precision": m.get("precision"), "recall": m.get("recall"),
            })
        out["source"].append("wood_inspect.db::ai_learning_runs(metrics.map50)")

        # 2) 会话置信度漂移：模型性能在产线上的真实表现
        rows = cur.execute(
            "select id, started_at, avg_conf, total_defects from sessions "
            "where avg_conf is not null order by id desc limit ?", (max(5, limit),)
        ).fetchall()
        for sid, ts_, conf, nd in reversed(rows):
            out["sessions"].append({"id": sid, "at": ts_, "avg_conf": round(float(conf or 0), 4),
                                    "defects": nd})
        out["source"].append("wood_inspect.db::sessions(avg_conf)")

        # 3) 按天：缺陷数 / 平均置信度
        rows = cur.execute(
            "select day, sum(cnt), round(avg(avg_conf),4) from daily_stats "
            "group by day order by day desc limit 14"
        ).fetchall()
        for day, cnt, conf in reversed(rows):
            out["daily"].append({"day": day, "cnt": int(cnt or 0), "avg_conf": float(conf or 0)})
        out["source"].append("wood_inspect.db::daily_stats(day)")

        # 4) 当前线上权重的指标（ai_models.stage='current'）
        row = cur.execute(
            "select name, created_at, metrics from ai_models where stage='current' "
            "order by id desc limit 1"
        ).fetchone()
        if row:
            try:
                m = json.loads(row[2] or "{}")
            except Exception:
                m = {}
            out["current_model"] = {"name": row[0], "at": row[1],
                                    "map50": m.get("map50") or m.get("mAP50"),
                                    "map50_95": m.get("map50_95")}
            out["source"].append("wood_inspect.db::ai_models(stage=current)")

        con.close()
    except Exception as e:
        out["ok"] = False
        out["error"] = f"{type(e).__name__}: {e}"
    return out


# ────────────────────────────────────────── 汇总一块屏要的数字
def state(job: dict | None = None, asr: dict | None = None) -> dict:
    g = gpu()
    return {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "gpu": g,
        "job": job or {"state": "idle"},
        "trend": trend(),
        "asr": asr or {"state": "unknown"},
        "health": _health_brief(),
    }


def _health_brief() -> dict:
    try:
        h = tools.model_health(20)
        c = h.get("confidence", {})
        return {"degraded": bool(h.get("degraded")),
                "recent": c.get("recent"), "baseline": c.get("baseline"),
                "drift": c.get("drift"), "thresh": c.get("thresh"),
                "reasons": (h.get("reasons") or [])[:2]}
    except Exception as e:
        return {"degraded": None, "error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(gpu(), ensure_ascii=False, indent=2))
    t = trend()
    print("learning:", len(t["learning"]), "sessions:", len(t["sessions"]), "daily:", len(t["daily"]))
    print("current_model:", t.get("current_model"))
    print("last learning:", t["learning"][-3:] if t["learning"] else None)
    print("health:", _health_brief())
