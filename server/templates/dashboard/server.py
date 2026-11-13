# -*- coding: utf-8 -*-
"""
板材缺陷数据 · 可视化大屏（后端）
================================
回答管理者的四个问题：
  1. 今天产了多少、质量怎么样？      → /api/summary
  2. 哪类缺陷最多、尺寸怎么分布？    → /api/classes
  3. 什么时候最容易出问题？          → /api/hourly
  4. 现在正在发生什么？              → /api/live （最近记录）

刻意零依赖：标准库 http.server + sqlite3，前端纯 Canvas 画图，
不引 ECharts、不连 CDN —— 车间断网也能开。

用法：
    python server.py                       # 默认 8773
    python server.py --db ../../data/wood_inspect.db --port 8773
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT.parent.parent.parent / "data" / "wood_inspect.db"
CN_ORDER = ["干节", "健全节", "边界", "小节", "裂纹", "波纹"]


def connect(db: str) -> sqlite3.Connection:
    con = sqlite3.connect("file:" + Path(db).as_posix() + "?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def q_summary(db: str) -> dict:
    con = connect(db)
    try:
        cur = con.cursor()
        today = datetime.now().strftime("%Y-%m-%d")
        det = cur.execute("select count(*) from detections where date(ts)=?", (today,)).fetchone()[0]
        boards = cur.execute(
            "select count(distinct session_id) from detections where date(ts)=?", (today,)).fetchone()[0]
        sessions = cur.execute(
            "select count(*) from sessions where date(started_at)=?", (today,)).fetchone()[0]
        conf = cur.execute(
            "select avg(confidence) from detections where date(ts)=?", (today,)).fetchone()[0] or 0
        return {
            "day": today, "detections": det, "boards": boards,
            "sessions": sessions, "avg_conf": round(conf, 4),
            "source": "detections + sessions（当日）",
            "at": datetime.now().strftime("%H:%M:%S"),
        }
    finally:
        con.close()


def q_classes(db: str, days: int = 1) -> dict:
    con = connect(db)
    try:
        cur = con.cursor()
        since = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        rows = cur.execute(
            "select class_cn, count(*) c, avg(confidence) a, "
            "       avg(x2-x1) w, avg(y2-y1) h "
            "from detections where date(ts)>=? group by class_cn", (since,)).fetchall()
        items = [{"class_cn": r["class_cn"], "count": r["c"],
                  "avg_conf": round(r["a"] or 0, 3),
                  "avg_w": round(r["w"] or 0, 1), "avg_h": round(r["h"] or 0, 1)}
                 for r in rows]
        items.sort(key=lambda x: -(x["count"] or 0))
        return {"days": days, "items": items, "source": f"detections(date>= {since})"}
    finally:
        con.close()


def q_hourly(db: str, days: int = 7) -> dict:
    con = connect(db)
    try:
        cur = con.cursor()
        since = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        rows = cur.execute(
            "select strftime('%H', ts) h, count(*) c from detections "
            "where date(ts)>=? group by h", (since,)).fetchall()
        buckets = {f"{i:02d}": 0 for i in range(24)}
        for r in rows:
            if r["h"] in buckets:
                buckets[r["h"]] = r["c"]
        peak = max(buckets.items(), key=lambda kv: kv[1]) if buckets else ("--", 0)
        return {"days": days, "buckets": buckets, "peak": peak[0], "peak_count": peak[1],
                "source": f"detections(date>= {since})"}
    finally:
        con.close()


def q_live(db: str, limit: int = 12) -> dict:
    con = connect(db)
    try:
        cur = con.cursor()
        rows = cur.execute(
            "select ts, class_cn, confidence, (x2-x1) w, (y2-y1) h "
            "from detections order by id desc limit ?", (limit,)).fetchall()
        return {"items": [dict(r) for r in rows], "source": "detections(order by id desc)"}
    finally:
        con.close()


def q_ai(db: str) -> dict:
    """AI 复核 / 自学习状态，对应脚本里“AI 检测能力怎么持续提升”那块。"""
    con = connect(db)
    try:
        cur = con.cursor()
        stats = {}
        for r in cur.execute("select human_state, count(*) c from ai_reviews group by human_state"):
            stats[r["human_state"]] = r["c"]
        pending = cur.execute(
            "select count(*) from ai_reviews where needs_human=1 and human_state='pending'"
        ).fetchone()[0]
        runs = cur.execute("select count(*) from ai_learning_runs").fetchone()[0]
        samples = cur.execute("select count(*) from ai_learning_samples").fetchone()[0]
        model = cur.execute(
            "select name, metrics, stage from ai_models where stage='current' "
            "order by id desc limit 1").fetchone()
        m = {}
        if model:
            try:
                m = json.loads(model["metrics"] or "{}")
            except Exception:
                m = {}
        return {
            "review_states": stats, "pending": pending,
            "learning_runs": runs, "samples": samples,
            "model": {"name": model["name"], "stage": model["stage"],
                      "map50": m.get("map50")} if model else None,
            "source": "ai_reviews + ai_learning_runs + ai_models",
        }
    finally:
        con.close()


class Handler(BaseHTTPRequestHandler):
    db = str(DEFAULT_DB)

    def log_message(self, *a):
        pass

    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        try:
            if p in ("/", "/index.html"):
                f = ROOT / "index.html"
                if not f.exists():
                    return self._json({"error": "index.html 缺失"})
                body = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if p == "/api/summary":
                return self._json(q_summary(self.db))
            if p == "/api/classes":
                return self._json(q_classes(self.db, int((q.get("days") or ["1"])[0])))
            if p == "/api/hourly":
                return self._json(q_hourly(self.db, int((q.get("days") or ["7"])[0])))
            if p == "/api/live":
                return self._json(q_live(self.db, int((q.get("limit") or ["12"])[0])))
            if p == "/api/ai":
                return self._json(q_ai(self.db))
            if p == "/api/health":
                return self._json({"ok": True, "at": datetime.now().isoformat()})
            self._json({"error": f"未知接口 {p}"})
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--port", type=int, default=8773)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if not Path(args.db).exists():
        print(f"[错误] 库不存在：{args.db}")
        return 2

    if args.check:
        print("库：", args.db)
        for name, fn in [("summary", lambda: q_summary(args.db)),
                         ("classes", lambda: q_classes(args.db, 1)),
                         ("hourly", lambda: q_hourly(args.db, 7)),
                         ("live", lambda: q_live(args.db, 3)),
                         ("ai", lambda: q_ai(args.db))]:
            try:
                d = fn()
                print(f"[OK] {name:<9} {json.dumps(d, ensure_ascii=False, default=str)[:150]}")
            except Exception as e:
                print(f"[FAIL] {name:<9} {type(e).__name__}: {e}")
                return 1
        return 0

    Handler.db = args.db
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True
    print(f"大屏已启动： http://127.0.0.1:{args.port}/   库：{args.db}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
