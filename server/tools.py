# -*- coding: utf-8 -*-
"""
数字人的真实工具层
==================
这里每一条都能在台前被验证：数字人说的话、写的文件、打的 git commit、报的警，
都要能追溯到真实文件、真实数据库行或真实命令输出。

硬约束（改之前先想清楚）：
1. 数据库一律 `mode=ro` 只读打开，绝不写产线库。
2. 文件写入被锁在 `交付/` 目录内，逃逸直接抛异常。
3. git 只在 `交付/` 内建**独立**仓库，不碰 new mcjc 主仓库 —— 主仓库有产线权重和配置。
4. 所有对外数字都带 `source` 字段，标明来自哪张表/哪个文件，方便台上被追问。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent                     # 数字人/
DELIVER_ROOT = ROOT / "交付"
PROJECT = Path(r"C:\Users\Administrator\Desktop\项目与竞赛\aa比赛项目")
NEW_MCJC = PROJECT / "new mcjc"
DB_PATH = NEW_MCJC / "data" / "wood_inspect.db"
SETTINGS = NEW_MCJC / "config" / "settings.json"
UPSTREAM = "http://127.0.0.1:8770"

CN_ORDER = ["干节", "健全节", "边界", "小节", "裂纹", "波纹"]


# ────────────────────────────────────────────── 基础设施
def db() -> sqlite3.Connection:
    """只读连接产线库。库不在就抛，不静默返回空。"""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"检测库不存在：{DB_PATH}")
    con = sqlite3.connect("file:" + DB_PATH.as_posix() + "?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_name(s: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fa5.\-]+", "_", s or "")
    return s.strip("._") or "unnamed"


def ensure_deliver_dir(task_id: str) -> Path:
    d = (DELIVER_ROOT / _safe_name(task_id)).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _assert_inside(path: Path, root: Path) -> Path:
    p = path.resolve()
    r = root.resolve()
    if not (p == r or r in p.parents):
        raise PermissionError(f"拒绝写入交付目录之外：{p}")
    return p


# ────────────────────────────────────────────── 只读查询
def overview(days: int = 1) -> dict:
    """今日（或近 N 天）检测概览。口径与桌面端一致：detections 表逐条计数。"""
    con = db()
    try:
        cur = con.cursor()
        since = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        total = cur.execute(
            "select count(*) from detections where date(ts) >= ?", (since,)
        ).fetchone()[0]
        sess = cur.execute(
            "select count(*) from sessions where date(start_ts) >= ?", (since,)
        ).fetchone()[0] if _has_col(cur, "sessions", "start_ts") else cur.execute(
            "select count(*) from sessions").fetchone()[0]
        rows = cur.execute(
            "select class_cn, count(*) c from detections where date(ts) >= ? "
            "group by class_cn order by c desc", (since,)
        ).fetchall()
        cls = {r["class_cn"]: r["c"] for r in rows}
        # 合格率：有检测记录且无缺陷的板数占比（以 session 为单位）
        boards = cur.execute(
            "select count(distinct session_id) from detections where date(ts) >= ?",
            (since,)
        ).fetchone()[0]
        last = cur.execute(
            "select ts, class_cn, confidence from detections order by id desc limit 1"
        ).fetchone()
        return {
            "days": days,
            "since": since,
            "detections": total,
            "classes": cls,
            "class_order": [c for c in CN_ORDER if c in cls],
            "boards_with_defect": boards,
            "sessions": sess,
            "last": dict(last) if last else None,
            "source": f"wood_inspect.db::detections(date(ts)>='{since}')",
            "at": _now(),
        }
    finally:
        con.close()


def _has_col(cur: sqlite3.Cursor, table: str, col: str) -> bool:
    try:
        cols = [r[1] for r in cur.execute(f'pragma table_info("{table}")').fetchall()]
        return col in cols
    except Exception:
        return False


def hourly(hours: int = 24) -> dict:
    """按小时分布 —— 对应脚本里“上午10-11点是缺陷高峰期”那段分析。"""
    con = db()
    try:
        cur = con.cursor()
        since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        rows = cur.execute(
            "select strftime('%H', ts) h, count(*) c from detections "
            "where ts >= ? group by h order by h", (since,)
        ).fetchall()
        buckets = {f"{i:02d}": 0 for i in range(24)}
        for r in rows:
            if r["h"] in buckets:
                buckets[r["h"]] = r["c"]
        peak = max(buckets.items(), key=lambda kv: kv[1]) if buckets else ("--", 0)
        return {
            "hours": hours,
            "buckets": buckets,
            "peak_hour": peak[0],
            "peak_count": peak[1],
            "source": f"wood_inspect.db::detections(ts>='{since}')",
            "at": _now(),
        }
    finally:
        con.close()


def model_health(recent_n: int = 20) -> dict:
    """模型性能健康度 —— 数字人“智能运维”的报警依据。

    只用库里真实存在的字段，三个口径都标明来源：

      A. 置信度漂移（主判据）
         sessions.avg_conf 是每个检测会话的平均置信度。拿最近 recent_n 个会话
         与更早的会话做对比，下滑比例超阈值即判定“性能异常”。
         这是“模型在新批次上不如以前确定”的直接证据。

      B. 复核异常分（辅判据）
         ai_reviews.anomaly_score（DINOv2 特征异常专家输出）与
         needs_human 转人工比例。异常分升高 = 模型对当前批次越来越拿不准。

      C. 时序风险（辅判据）
         ai_trends.risk，Mamba 条带模型的逐板风险值。

    不使用“人工改判率”作判据：本库 human_state 里没有 rejected 记录
    （只有 auto/pending/accepted），该指标恒为 0，拿它报警等于编。
    """
    con = db()
    try:
        cur = con.cursor()

        # ── A. 置信度漂移
        sess = cur.execute(
            "select id, started_at, source_type, total_defects, defect_frames, "
            "       avg_conf, max_conf "
            "from sessions where avg_conf is not null and avg_conf > 0 "
            "order by id desc limit 400"
        ).fetchall()
        latest = sess[:recent_n]
        older = sess[recent_n:]
        def _mean(xs):
            xs = [x for x in xs if x is not None]
            return sum(xs) / len(xs) if xs else 0.0

        cur_conf = _mean([r["avg_conf"] for r in latest])
        base_conf = _mean([r["avg_conf"] for r in older]) if older else 0.0
        drift = ((base_conf - cur_conf) / base_conf) if base_conf > 0 else 0.0

        # ── B. 复核异常分
        rev = cur.execute(
            "select anomaly_score, needs_human, panel_size, confidence "
            "from ai_reviews where ts >= date('now','-14 day') "
            "order by id desc limit 400"
        ).fetchall()
        anom = _mean([r["anomaly_score"] for r in rev])
        anom_all = cur.execute(
            "select avg(anomaly_score) a from ai_reviews"
        ).fetchone()["a"] or 0.0
        escalate_rate = (sum(1 for r in rev if r["needs_human"]) / len(rev)) if rev else 0.0
        panel = _mean([r["panel_size"] for r in rev])

        # ── C. 时序风险
        tr = cur.execute(
            "select risk from ai_trends where ts >= date('now','-7 day') "
            "order by id desc limit 200"
        ).fetchall()
        risks = [r["risk"] for r in tr if r["risk"] is not None]
        risk_avg = _mean(risks)
        risk_high = sum(1 for x in risks if x > 0.6)

        metrics = _current_model_metrics(cur)
        th = _thresholds()

        degraded = False
        reasons = []
        if len(older) >= 5 and drift >= th["conf_drift"]:
            degraded = True
            reasons.append(
                f"近 {len(latest)} 个会话平均置信度 {cur_conf:.3f}，"
                f"较此前 {len(older)} 个会话的 {base_conf:.3f} 下滑 {drift:.1%}"
                f"（阈值 {th['conf_drift']:.0%}）")
        if len(risks) >= 5 and risk_avg >= th["risk_avg"]:
            degraded = True
            reasons.append(
                f"近 7 天时序风险均值 {risk_avg:.3f} ≥ 阈值 {th['risk_avg']}"
                f"（{risk_high}/{len(risks)} 块板超 0.6）")
        if len(rev) >= 10 and escalate_rate >= th["escalate"]:
            degraded = True
            reasons.append(
                f"近 14 天转人工率 {escalate_rate:.1%} ≥ 阈值 {th['escalate']:.0%}")

        return {
            "confidence": {
                "recent_n": len(latest),
                "recent": round(cur_conf, 4),
                "baseline_n": len(older),
                "baseline": round(base_conf, 4),
                "drift": round(drift, 4),
                "thresh": th["conf_drift"],
            },
            "reviews": {
                "n": len(rev),
                "anomaly_recent": round(anom, 4),
                "anomaly_all": round(anom_all, 4),
                "escalate_rate": round(escalate_rate, 4),
                "escalate_thresh": th["escalate"],
                "panel_avg": round(panel, 2),
            },
            "risk": {
                "n": len(risks),
                "avg": round(risk_avg, 4),
                "high_boards": risk_high,
                "thresh": th["risk_avg"],
            },
            "model": metrics,
            "degraded": degraded,
            "reasons": reasons,
            "source": "wood_inspect.db::sessions(avg_conf) + ai_reviews(anomaly_score,needs_human) + ai_trends(risk)",
            "at": _now(),
        }
    finally:
        con.close()


def _current_model_metrics(cur: sqlite3.Cursor) -> dict:
    try:
        row = cur.execute(
            "select name, metrics, stage, updated_at from ai_models "
            "where stage='current' order by id desc limit 1"
        ).fetchone()
        if not row:
            row = cur.execute(
                "select name, metrics, stage, updated_at from ai_models order by id desc limit 1"
            ).fetchone()
        if not row:
            return {}
        m = {}
        try:
            m = json.loads(row["metrics"] or "{}")
        except Exception:
            pass
        return {
            "name": row["name"],
            "stage": row["stage"],
            "map50": m.get("map50"),
            "map50_95": m.get("map50_95"),
            "precision": m.get("precision"),
            "recall": m.get("recall"),
            "updated_at": row["updated_at"],
        }
    except Exception:
        return {}


def _thresholds() -> dict:
    """报警阈值。settings.json 里有覆盖就用覆盖值，否则用这里的默认口径。

    默认值依据本库真实数据标定（2026-11 批次置信度从 0.86 级掉到 0.44 级）：
    置信度下滑 5% 即报，意味着“明显不如以前”。
    """
    th = {"conf_drift": 0.05, "risk_avg": 0.60, "escalate": 0.65, "change_rate": 0.25}
    try:
        if SETTINGS.exists():
            s = json.loads(SETTINGS.read_text(encoding="utf-8"))
            th["conf_drift"] = float(s.get("dh_alert_conf_drift", th["conf_drift"]))
            th["risk_avg"] = float(s.get("dh_alert_risk_avg", th["risk_avg"]))
            th["escalate"] = float(s.get("dh_alert_escalate", th["escalate"]))
    except Exception:
        pass
    return th


def recent_detections(limit: int = 10) -> dict:
    con = db()
    try:
        cur = con.cursor()
        rows = cur.execute(
            "select id, ts, session_id, class_cn, confidence, x1, y1, x2, y2 "
            "from detections order by id desc limit ?", (limit,)
        ).fetchall()
        return {
            "items": [dict(r) for r in rows],
            "source": "wood_inspect.db::detections(order by id desc)",
            "at": _now(),
        }
    finally:
        con.close()


def pending_reviews(limit: int = 10) -> dict:
    con = db()
    try:
        cur = con.cursor()
        rows = cur.execute(
            "select id, ts, session_id, class_cn, confidence, panel_size, voter_n, abstain_n "
            "from ai_reviews where needs_human=1 and human_state='pending' "
            "order by id desc limit ?", (limit,)
        ).fetchall()
        return {
            "items": [dict(r) for r in rows],
            "source": "wood_inspect.db::ai_reviews(needs_human=1 and human_state='pending')",
            "at": _now(),
        }
    finally:
        con.close()


def upstream_health() -> dict:
    """8770 大屏服务是否在跑（不启动它，只探活）。"""
    import urllib.request

    out = {"url": UPSTREAM, "up": False}
    try:
        with urllib.request.urlopen(UPSTREAM + "/api/health", timeout=2.5) as r:
            out["up"] = (r.status == 200)
            out["body"] = r.read(400).decode("utf-8", "replace")
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    out["at"] = _now()
    return out


# ────────────────────────────────────────────── 交付（写文件 + git 留痕）
def write_deliverable(task_id: str, files: dict, note: str = "") -> dict:
    """把起草好的代码写进 `交付/<task_id>/`，返回落盘清单。

    files: {相对路径: 文件内容}
    """
    d = ensure_deliver_dir(task_id)
    written = []
    for rel, content in (files or {}).items():
        target = _assert_inside(d / rel, d)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append({
            "path": str(target),
            "rel": str(target.relative_to(d).as_posix()),
            "bytes": len(content.encode("utf-8")),
            "lines": content.count("\n") + 1,
        })
    if note:
        (d / "README_交付说明.md").write_text(note, encoding="utf-8")
    return {
        "task": task_id,
        "dir": str(d),
        "files": written,
        "count": len(written),
        "at": _now(),
    }


def git_commit(task_id: str, message: str) -> dict:
    """在 `交付/` 独立仓库里留痕。首次调用会自动 init。"""
    repo = DELIVER_ROOT
    repo.mkdir(parents=True, exist_ok=True)
    d = ensure_deliver_dir(task_id)

    def run(args, cwd):
        p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    try:
        if not (repo / ".git").exists():
            run(["git", "init", "-q"], repo)
            run(["git", "config", "user.name", "5号选手·数字人"], repo)
            run(["git", "config", "user.email", "dh5@bancai.local"], repo)
            gi = repo / ".gitignore"
            if not gi.exists():
                gi.write_text("# 数字人交付仓库：只留痕，不入库大文件\n*.mp4\n*.png\n*.jpg\n",
                              encoding="utf-8")

        rc1, out1 = run(["git", "add", "--", str(d.relative_to(repo))], repo)
        if rc1 != 0:
            return {"ok": False, "step": "add", "detail": out1.strip()[:400]}
        rc2, out2 = run(["git", "commit", "-m", message, "--allow-empty"], repo)
        rc3, out3 = run(["git", "log", "-1", "--pretty=%h|%ad|%s", "--date=format:%Y-%m-%d %H:%M:%S"], repo)
        rc4, out4 = run(["git", "rev-list", "--count", "HEAD"], repo)
        return {
            "ok": rc2 == 0,
            "commit": (out3.strip().splitlines() or [""])[0],
            "commits_total": (out4.strip() or "?"),
            "repo": str(repo),
            "message": message,
            "detail": (out2.strip()[:400] or out1.strip()[:400]),
            "at": _now(),
        }
    except FileNotFoundError:
        return {"ok": False, "error": "未找到 git 可执行文件"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ────────────────────────────────────────────── 自检
def selftest() -> int:
    print("=" * 60)
    print("数字人工具层自检")
    print("=" * 60)
    fails = []
    try:
        ov = overview(1)
        print(f"[OK] 概览      今日 {ov['detections']} 条检测 / {ov['sessions']} 个 session")
        print(f"     类别分布   {ov['classes']}")
        print(f"     数据来源   {ov['source']}")
    except Exception as e:
        fails.append("overview")
        print(f"[FAIL] 概览    {type(e).__name__}: {e}")

    try:
        hh = hourly(24)
        print(f"[OK] 小时分布 高峰 {hh['peak_hour']} 时（{hh['peak_count']} 条）")
    except Exception as e:
        fails.append("hourly")
        print(f"[FAIL] 小时分布 {type(e).__name__}: {e}")

    try:
        h = model_health(20)
        print(f"[OK] 模型健康 置信度 {h['confidence']['recent']} vs 基线 {h['confidence']['baseline']}"
              f"（下滑 {h['confidence']['drift']:.1%}）")
        print(f"     复核    异常分 {h['reviews']['anomaly_recent']} / 历史 {h['reviews']['anomaly_all']}"
              f" / 转人工率 {h['reviews']['escalate_rate']:.1%}")
        print(f"     时序风险 {h['risk']['avg']} (n={h['risk']['n']}, 超0.6={h['risk']['high_boards']})")
        print(f"     判定    degraded={h['degraded']}")
        for r in h["reasons"]:
            print(f"             · {r}")
        print(f"     当前模型 {h['model'].get('name')} map50={h['model'].get('map50')}")
    except Exception as e:
        fails.append("model_health")
        print(f"[FAIL] 模型健康 {type(e).__name__}: {e}")

    try:
        up = upstream_health()
        print(f"[OK] 8770     up={up['up']} {up.get('error', '')}")
    except Exception as e:
        fails.append("upstream")
        print(f"[FAIL] 8770    {type(e).__name__}: {e}")

    # 交付沙箱：越界必须被拒（在临时目录里测，不污染 交付/）
    import tempfile
    try:
        with tempfile.TemporaryDirectory() as td:
            global DELIVER_ROOT
            real_root = DELIVER_ROOT
            DELIVER_ROOT = Path(td)
            try:
                write_deliverable("_selftest", {"a.txt": "hello"})
                blocked = False
                try:
                    write_deliverable("_selftest", {"../../../hack.txt": "x"})
                except PermissionError:
                    blocked = True
                except Exception:
                    blocked = False
            finally:
                DELIVER_ROOT = real_root
        print(f"[{'OK' if blocked else 'FAIL'}] 交付沙箱  越界写入被拒={blocked}")
        if not blocked:
            fails.append("sandbox")
    except Exception as e:
        fails.append("deliver")
        print(f"[FAIL] 交付沙箱 {type(e).__name__}: {e}")

    try:
        g = git_commit("_selftest", "自检提交")
        print(f"[{'OK' if g.get('ok') else 'WARN'}] git 留痕   {g.get('commit', g)}")
    except Exception as e:
        print(f"[WARN] git 留痕 {type(e).__name__}: {e}")

    print("=" * 60)
    print("全部通过" if not fails else f"失败项：{fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(selftest())
