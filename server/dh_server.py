# -*- coding: utf-8 -*-
"""
数字人主服务（端口 8772）
==========================
一个进程同时干四件事：
  1. 静态托管：avatar/（3D 形象）、voice/（预渲染语音）、models/（.vrm）
  2. 事件总线：/api/events 走 SSE，前端订阅后由后端驱动“说哪句、什么状态”
  3. 剧本引擎：把 script.json 的 cue 按需播报，可选自动巡检模型健康
  4. Agent 入口：/api/agent/chat 走真实工具（查库/交付代码/git 留痕/报警）

为什么用标准库而不是 FastAPI/Flask：与主系统 chat_hub.py 同口径 ——
现场断网、无外网 pip 也能起；一个 .venv 里只装 edge-tts（仅离线预渲染时才用）。

命令：
    .venv\\Scripts\\python.exe server\\dh_server.py
    .venv\\Scripts\\python.exe server\\dh_server.py --port 8772 --no-watch
    .venv\\Scripts\\python.exe server\\dh_server.py --check      # 自检后退出
"""
from __future__ import annotations

import argparse
import collections
import json
import mimetypes
import os
import queue
import sys
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_FILE = ROOT / "script" / "script.json"
VOICE_DIR = ROOT / "voice"
MANIFEST = VOICE_DIR / "manifest.json"
AVATAR_DIR = ROOT / "avatar"
MODELS_DIR = ROOT / "models"
CONSOLE_FILE = ROOT / "console" / "index.html"

sys.path.insert(0, str(ROOT / "server"))
import tools  # noqa: E402
from gpu_runner import RUNNER  # noqa: E402  GPU 作业调度（真起 yoloces 进程）

VERSION = "0.2.0"
RECENT_SAY = collections.deque(maxlen=8)   # 最近自己说过的话，用来挡住「听到自己」
DEFAULT_PORT = 8772


# ══════════════════════════════════════════════ 事件总线
class Bus:
    def __init__(self) -> None:
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._seq = 0

    def sub(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=300)
        with self._lock:
            self._subs.append(q)
        return q

    def unsub(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    def pub(self, msg: dict) -> int:
        with self._lock:
            self._seq += 1
            msg = {**msg, "_seq": self._seq, "_ts": datetime.now().strftime("%H:%M:%S")}
            dead = []
            for q in self._subs:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                try:
                    self._subs.remove(q)
                except ValueError:
                    pass
            return len(self._subs)


BUS = Bus()


# ══════════════════════════════════════════════ 剧本
def load_script() -> dict:
    return json.loads(SCRIPT_FILE.read_text(encoding="utf-8"))


def load_manifest() -> dict:
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def cue_payload(cue: dict, manifest: dict) -> dict:
    """把剧本里的 cue 变成前端能直接吃的一条 say 消息。"""
    cid = cue["id"]
    clips = (manifest.get("clips") or {})
    audio = ""
    if cid in clips:
        f = VOICE_DIR / clips[cid]["file"]
        if f.exists():
            audio = f"/voice/{clips[cid]['file']}"
    return {
        "type": "say",
        "id": cid,
        "text": cue.get("text", ""),
        "who": cue.get("who", "小星"),
        "audio": audio,
        "state": cue.get("state", "speak"),
        "mood": cue.get("mood", "relaxed"),
        "scene": cue.get("scene", ""),
    }


def _chain_after_agent(cue: dict) -> None:
    """cue 上挂了 agent（例如 received -> preprocess）：后台真做事，做完播下一条台词。

    这样台上就是：同事念完六要素 → 小星"收到。"→（真把代码写进交付目录 + git 留痕）
    → 小星"1号同事，图像预处理代码已提交到你的项目文件中，请确认。"
    """
    cues = load_script().get("cues", [])
    task = (cue or {}).get("agent")
    idx = next((i for i, c in enumerate(cues) if c.get("id") == (cue or {}).get("id")), -1)
    if not task or idx < 0:
        return

    def work():
        info = {}
        try:
            import agent as _agent
            info = _agent.deliver_task(task) if task in _agent.TASKS else {"error": f"未知任务 {task}"}
        except Exception as e:
            info = {"error": f"{type(e).__name__}: {e}"}
        time.sleep(1.4)                      # 让"收到。"先说完
        if idx + 1 < len(cues):
            nxt = cues[idx + 1]
            BUS.pub(cue_payload(nxt, load_manifest()))
            print(f"[cue] 交付完成，自动接下一句：{nxt.get('id')}", flush=True)
            RECENT_SAY.append((time.time(), nxt.get("text", "")))
        print(f"[cue] {cue.get('id')} agent={task} -> "
              f"{info.get('dir', info.get('error', ''))}", flush=True)

    threading.Thread(target=work, daemon=True).start()


# ── 语音点名的剧本段落：说这些话，小星就用那一段原声播出来
VOICE_CUE_RULES = [
    (r"自我介绍|介绍一下你自己|介绍一下自己|你是谁|你叫什么|你谁啊|你是几号", "self_intro"),
]


def match_voice_cue(text: str) -> dict | None:
    """语音里点名了某段台词，就把它整条（含预渲染 mp3）返回，交给调用方播。"""
    import re as _re
    for pat, cid in VOICE_CUE_RULES:
        if _re.search(pat, text or "", _re.I):
            data = load_script()
            cue = next((c for c in data.get("cues", []) if c.get("id") == cid), None)
            if cue:
                return cue_payload(cue, load_manifest())
    return None


# ══════════════════════════════════════════════ 模型健康巡检
class HealthWatcher(threading.Thread):
    """后台巡检模型性能。真实数据判定 → 真报警，不演假戏。"""

    def __init__(self, interval: float = 30.0) -> None:
        super().__init__(daemon=True)
        self.interval = interval
        self.stop_flag = threading.Event()
        self.last: dict | None = None
        self.alerted = False
        self.recent_n = 20

    def check_once(self, publish: bool = True) -> dict:
        try:
            h = tools.model_health(self.recent_n)
        except Exception as e:
            h = {"error": f"{type(e).__name__}: {e}", "degraded": False,
                 "at": datetime.now().isoformat()}
        self.last = h
        self.alerted = bool(h.get("degraded"))
        if publish:
            # 只广播健康度，让前端更新状态条。
            # **不自动弹报警横幅**：报警由控制台的「报警演示」按钮或
            # 剧本里的 alert_abnormal 台词触发，否则真实数据一动就红屏，
            # 会把正片演示打断（状态条仍如实显示异常）。
            BUS.pub({"type": "health", "health": h})
        return h

    def run(self) -> None:
        while not self.stop_flag.is_set():
            self.check_once(True)
            self.stop_flag.wait(self.interval)


WATCHER = HealthWatcher()


# ══════════════════════════════════════════════ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = f"DigitalHuman/{VERSION}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静音访问日志
        pass

    # ───────────── 基础响应
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _err(self, msg: str, code: int = 400) -> None:
        self._json({"ok": False, "error": msg}, code)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # ───────────── 路由
    def do_GET(self) -> None:
        u = urlparse(self.path)
        p = unquote(u.path)
        q = parse_qs(u.query)
        try:
            if p in ("/", "/index.html"):
                return self._redirect("/avatar/")
            if p == "/console" or p == "/console/":
                return self._serve_file(CONSOLE_FILE)
            if p.startswith("/api/"):
                return self._api_get(p, q)
            return self._serve_static(p)
        except Exception:
            tb = traceback.format_exc()
            sys.stderr.write(tb)
            self._err(tb, 500)

    def do_POST(self) -> None:
        u = urlparse(self.path)
        p = unquote(u.path)
        try:
            if p in ("/api/asr", "/api/voice"):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n > 0 else b""
                return self._api_post_audio(p, raw)
            body = self._body()
            return self._api_post(p, body)
        except Exception:
            tb = traceback.format_exc()
            sys.stderr.write(tb)
            self._err(tb, 500)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ───────────── 静态
    def _redirect(self, to: str) -> None:
        self.send_response(302)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _serve_static(self, p: str) -> None:
        rel = p.lstrip("/")
        parts = rel.split("/", 1)
        if len(parts) == 2:
            base_name, sub = parts
            base_map = {"avatar": AVATAR_DIR, "voice": VOICE_DIR,
                        "models": MODELS_DIR, "交付": tools.DELIVER_ROOT,
                        "xx": ROOT / "板材数字人_四屏舞台_20261113"}   # 小星的素材与播放器
            if base_name in base_map:
                if sub in ("", "/"):
                    sub = "index.html"
                target = (base_map[base_name] / sub).resolve()
                # 防目录穿越：解析后必须仍在根内
                root = base_map[base_name].resolve()
                if not (target == root or root in target.parents):
                    return self._send(403, b"forbidden", "text/plain; charset=utf-8")
                return self._serve_file(target)
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def _serve_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if path.suffix == ".vrm":
            ctype = "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        data = path.read_bytes()
        self._send(200, data, ctype)

    # ───────────── GET API
    def _api_get(self, p: str, q: dict) -> None:
        if p == "/api/health":
            return self._json({
                "ok": True, "service": "digital-human", "version": VERSION,
                "port": self.server.server_address[1],
                "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "subscribers": len(BUS._subs),
                "voice_clips": len(list(VOICE_DIR.glob("*.mp3"))),
                "model_file": [f.name for f in MODELS_DIR.glob("*.vrm")],
            })

        if p == "/api/events":
            return self._sse()

        if p == "/api/script":
            data = load_script()
            man = load_manifest()
            clips = man.get("clips") or {}
            cues = []
            for c in data.get("cues", []):
                cues.append({
                    "id": c["id"], "scene": c.get("scene", ""),
                    "who": c.get("who", ""), "text": c.get("text", ""),
                    "trigger": c.get("trigger", ""), "action": c.get("action", ""),
                    "has_voice": c["id"] in clips,
                    "agent": c.get("agent"),
                })
            return self._json({"ok": True, "persona": data.get("persona", {}),
                               "cues": cues, "count": len(cues)})

        if p == "/api/state":
            return self._json({"ok": True, "health": WATCHER.last,
                               "alerted": WATCHER.alerted})

        if p == "/api/overview":
            days = int((q.get("days") or ["1"])[0])
            return self._json({"ok": True, "data": tools.overview(days)})

        if p == "/api/hourly":
            hours = int((q.get("hours") or ["24"])[0])
            return self._json({"ok": True, "data": tools.hourly(hours)})

        if p == "/api/model/health":
            return self._json({"ok": True, "data": WATCHER.check_once(True)})

        if p == "/api/detections":
            limit = int((q.get("limit") or ["10"])[0])
            return self._json({"ok": True, "data": tools.recent_detections(limit)})

        if p == "/api/reviews/pending":
            limit = int((q.get("limit") or ["10"])[0])
            return self._json({"ok": True, "data": tools.pending_reviews(limit)})

        if p == "/api/upstream":
            return self._json({"ok": True, "data": tools.upstream_health()})

        # ── 身边的运维屏：GPU 实时采样 + 模型性能趋势 + 当前作业
        if p == "/api/ops":
            import ops_monitor
            import asr_client
            job = RUNNER.status()
            return self._json({"ok": True, "data": ops_monitor.state(job=job, asr=asr_client.health())})

        if p == "/api/job":
            return self._json({"ok": True, "data": RUNNER.status()})

        # ── 五块屏的数据源：8790 四屏舞台的实时聚合（转发，避免跨域）
        if p == "/api/stage":
            import urllib.request as _u
            try:
                with _u.urlopen("http://127.0.0.1:8790/api/stage", timeout=4) as r:
                    return self._json(json.loads(r.read().decode("utf-8")))
            except Exception as e:
                return self._json({"code": 503, "msg": f"8790 未启动或抓取失败：{type(e).__name__}: {e}",
                                   "data": None})

        if p == "/api/asr/health":
            import asr_client
            return self._json({"ok": True, "data": asr_client.health()})

        if p == "/api/tools":
            import agent  # 延迟导入，避免没配 key 时启动失败
            return self._json({"ok": True, "data": agent.catalog()})

        if p == "/api/deliver/list":
            base = tools.DELIVER_ROOT
            items = []
            if base.exists():
                for d in sorted(base.iterdir()):
                    if d.is_dir() and not d.name.startswith("_"):
                        files = [f.name for f in d.rglob("*") if f.is_file()]
                        items.append({"task": d.name, "files": files})
            return self._json({"ok": True, "items": items})

        return self._err(f"未知接口 GET {p}", 404)

    # ───────────── SSE
    def _sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        q = BUS.sub()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            # 立刻推一条当前状态，前端不用等下一轮
            if WATCHER.last:
                self._sse_send({"type": "health", "health": WATCHER.last})
            last_ping = time.time()
            while True:
                try:
                    msg = q.get(timeout=1.0)
                    self._sse_send(msg)
                except queue.Empty:
                    if time.time() - last_ping > 15:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last_ping = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            BUS.unsub(q)

    def _sse_send(self, msg: dict) -> None:
        data = json.dumps(msg, ensure_ascii=False, default=str)
        self.wfile.write(f"event: dh\ndata: {data}\n\n".encode("utf-8"))
        self.wfile.flush()

    # ───────────── POST API
    def _api_post(self, p: str, body: dict) -> None:
        if p == "/api/cue":
            cid = (body.get("id") or "").strip()
            data = load_script()
            cue = next((c for c in data.get("cues", []) if c["id"] == cid), None)
            if not cue:
                return self._err(f"没有这个 cue：{cid}", 404)
            msg = cue_payload(cue, load_manifest())
            n = BUS.pub(msg)
            return self._json({"ok": True, "sent": msg, "subscribers": n})

        if p == "/api/cue/next":
            ids = body.get("ids") or []
            data = load_script()
            all_cues = [c["id"] for c in data.get("cues", [])]
            cur = body.get("current") or ""
            if ids:
                seq = [i for i in ids if i in all_cues]
            else:
                seq = all_cues
            try:
                idx = seq.index(cur) + 1 if cur in seq else 0
            except ValueError:
                idx = 0
            if idx >= len(seq):
                return self._json({"ok": True, "done": True})
            cid = seq[idx]
            cue = next(c for c in data["cues"] if c["id"] == cid)
            msg = cue_payload(cue, load_manifest())
            BUS.pub(msg)
            return self._json({"ok": True, "sent": msg, "index": idx, "total": len(seq)})

        if p == "/api/say":
            text = (body.get("text") or "").strip()
            if not text:
                return self._err("text 为空")
            msg = {
                "type": "say", "text": text, "who": body.get("who", "小星"),
                "audio": body.get("audio", ""), "state": body.get("state", "speak"),
                "mood": body.get("mood", "relaxed"),
            }
            n = BUS.pub(msg)
            return self._json({"ok": True, "sent": msg, "subscribers": n})

        if p == "/api/state":
            st = body.get("state") or "idle"
            BUS.pub({"type": "state", "state": st})
            return self._json({"ok": True, "state": st})

        if p == "/api/alarm":
            on = bool(body.get("on"))
            why = body.get("why") or ""
            BUS.pub({"type": "alarm", "on": on, "why": why})
            return self._json({"ok": True, "on": on})

        if p == "/api/monitor/check":
            h = WATCHER.check_once(True)
            return self._json({"ok": True, "data": h})

        if p == "/api/agent/chat":
            import agent
            msg = (body.get("message") or "").strip()
            want_files = bool(body.get("deliver", False))
            res = agent.chat(msg, deliver=want_files, history=body.get("history"))
            # 自由回答没有预渲染 mp3，现场现合成一句（离线 SAPI，~0.3s）
            if res.get("say") and not res.get("audio"):
                try:
                    import tts
                    t = tts.synth(res["say"])
                    if t.get("ok"):
                        res["audio"] = t["url"]
                        res["tts"] = {k: t.get(k) for k in ("engine", "ms", "cache")}
                except Exception as e:
                    res["tts_error"] = f"{type(e).__name__}: {e}"
            # Agent 说话也走事件总线，前端同步口型
            if res.get("say"):
                BUS.pub({
                    "type": "say", "text": res["say"], "who": "小星",
                    "audio": res.get("audio", ""), "state": res.get("state", "speak"),
                    "mood": res.get("mood", "relaxed"),
                })
            return self._json({"ok": True, "data": res})

        # ── 直接起/停一个真实 GPU 作业（对话之外的手动开关，现场兜底用）
        if p == "/api/job/start":
            mode = (body.get("mode") or "train").strip()
            r = RUNNER.start(mode, **{k: body[k] for k in
                                      ("epochs", "imgsz", "batch", "fraction") if body.get(k)})
            return self._json(r if r.get("ok") else {"ok": False, "error": r.get("error"),
                                                     "job": r.get("job")})

        if p == "/api/job/stop":
            return self._json(RUNNER.stop())

        return self._err(f"未知接口 POST {p}", 404)

    # ───────────── 语音：WAV 进，文字/回答出
    def _api_post_audio(self, p: str, raw: bytes) -> None:
        if len(raw) < 1000:
            return self._err(f"音频太短（{len(raw)} 字节），没听到内容", 400)
        import asr_client
        t0 = time.time()
        asr = asr_client.transcribe(raw)
        if not asr.get("ok"):
            return self._json({"ok": False, "stage": "asr", "error": asr.get("error"),
                               "asr": asr}, 502)
        text = (asr.get("text") or "").strip()
        if p == "/api/asr":
            return self._json({"ok": True, "text": text, "asr": asr,
                               "ms": int((time.time() - t0) * 1000)})
        if not text:
            # 听不清就**不出声**：现场最怕它对外界杂音乱回一句。只把状态回给前端。
            return self._json({"ok": True, "heard": "", "silent": True,
                               "say": "", "audio": "", "asr": asr})

        # ── 回声抑制：识别文本和自己最近 40 秒内说过的话重合，说明是它自己在说话
        def _norm(x):
            return "".join(ch for ch in (x or "") if ch.isalnum())

        _NUM_CN = set("零一二三四五六七八九十百千万亿两点")

        def _skel(x):
            # 只留汉字，并且把汉字数字一起剔掉：
            # ASR 会把 239 念成"二百三十九"，不剔的话骨架永远对不上
            return "".join(ch for ch in (x or "")
                           if "\u4e00" <= ch <= "\u9fff" and ch not in _NUM_CN)

        def _is_echo(heard: str) -> bool:
            hn, hs = _norm(heard), _skel(heard)
            now_ts = time.time()
            for ts, said in list(RECENT_SAY):
                if now_ts - ts > 40:
                    continue
                if len(hn) >= 4 and hn in _norm(said):
                    return True
                if len(hs) >= 6 and hs in _skel(said):
                    return True
            return False

        if _is_echo(text):
            return self._json({"ok": True, "heard": text, "silent": True, "say": "",
                               "audio": "", "asr": asr,
                               "hint": "听到的是我自己刚说的话，已忽略"})

        cue = match_voice_cue(text)
        if not cue:
            # 同事按剧本念了"上一句"→ 接住"下一句"（相似度 + 顺序先验 + 关键词）
            try:
                import script_match
                hit = script_match.match(text) or script_match.advance(text)
            except Exception as e:
                hit = None
                print("[cue] script_match 异常:", e)
            if hit:
                raw = hit["cue"]
                cue = cue_payload(raw, load_manifest())
                cue["agent"] = raw.get("agent")      # cue_payload 不搬这个字段，链式要用
                cue["_score"] = hit["score"]
                cue["_why"] = hit["why"]
        if cue:
            RECENT_SAY.append((time.time(), cue.get("text", "")))
            if cue.get("agent"):
                _chain_after_agent(cue)
            return self._json({"ok": True, "heard": text, "asr": asr, "cue": cue.get("id"),
                               "audio": cue.get("audio", ""),
                               "data": {"say": cue.get("text", ""), "audio": cue.get("audio", ""),
                                        "state": "speak", "channel": "cue",
                                        "match": {"score": cue.get("_score"), "why": cue.get("_why")},
                                        "steps": [{"tool": "play_cue", "ok": True,
                                                   "brief": "剧本接话：" + (cue.get("id") or "")
                                                            + (f" (score {cue['_score']})" if cue.get("_score") else "")}]},
                               "ms": int((time.time() - t0) * 1000)})

        import agent
        # 唤醒词可有可无：说了"小星"就先摘掉，正文照样处理
        core = text
        for w in ("小星，", "小星,", "小星", "小新，", "小新,", "小新"):
            if core.startswith(w):
                core = core[len(w):].lstrip("，,。 ").strip()
                break
        res = agent.chat(core)
        # 没听出意图（多半是环境噪声被识别成半句话）→ 不出声，只在字幕上提示，
        # 现场绝不能因为它乱回一句而打断讲解。
        if res.get("intent") == "unknown" or (res.get("channel") == "offline"
                                             and (res.get("say") or "").startswith("这条我没听出")):
            return self._json({"ok": True, "heard": text, "silent": True, "say": "",
                               "audio": "", "asr": asr, "hint": "没听出意图，已静默跳过"})
        if res.get("say") and not res.get("audio"):
            try:
                import tts
                t = tts.synth(res["say"])
                if t.get("ok"):
                    res["audio"] = t["url"]
                    res["tts"] = {k: t.get(k) for k in ("engine", "ms", "cache")}
            except Exception as e:
                res["tts_error"] = f"{type(e).__name__}: {e}"
        # 这里不能再 BUS.pub 一次：小星窗口/控制台自己会播这声，
        # 广播等于同一句播两遍，第二遍会被麦克风听回去，形成回声自激。
        if res.get("say"):
            RECENT_SAY.append((time.time(), res["say"]))
        return self._json({"ok": True, "heard": text, "asr": asr, "data": res,
                           "audio": res.get("audio", ""),
                           "ms": int((time.time() - t0) * 1000)})


class QuietServer(ThreadingHTTPServer):
    """客户端断开（刷新页面/关窗口）不算错误，别刷一屏 traceback。"""

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


# ══════════════════════════════════════════════ 启动
def run_check(port: int) -> int:
    print("=" * 64)
    print(f"数字人自检（v{VERSION}）")
    print("=" * 64)
    fails = []

    def ok(name, cond, detail=""):
        print(f"[{'OK' if cond else 'FAIL'}] {name:<18} {detail}")
        if not cond:
            fails.append(name)

    ok("剧本文件", SCRIPT_FILE.exists(), str(SCRIPT_FILE))
    data = load_script() if SCRIPT_FILE.exists() else {}
    cues = data.get("cues", [])
    ok("cue 数量", len(cues) >= 19, f"{len(cues)} 条")
    man = load_manifest()
    clips = (man.get("clips") or {})
    missing = [c["id"] for c in cues if c["id"] not in clips or not (VOICE_DIR / clips.get(c["id"], {}).get("file", "x")).exists()]
    ok("语音齐全", not missing, f"缺 {missing}" if missing else f"{len(clips)} 个 mp3")
    vrm = list(MODELS_DIR.glob("*.vrm"))
    fbx = list(MODELS_DIR.rglob("*.fbx"))
    default_fbx = MODELS_DIR / "rocketbox" / "m08" / "m08_facial.fbx"
    ok("3D 模型", bool(vrm or fbx),
       f"默认={'rocketbox/m08 (FBX)' if default_fbx.exists() else 'VRM'}；"
       f"vrm={[v.name for v in vrm]} fbx={len(fbx)}个")
    tex = list((MODELS_DIR / "rocketbox" / "m08").glob("*.png")) if default_fbx.exists() else []
    ok("模型贴图", not default_fbx.exists() or len(tex) >= 3, f"{len(tex)} 张 png")

    # 默认形象是 2.5D 照片人像，这两样缺一个都起不来
    p_portrait = AVATAR_DIR / "assets" / "portrait.png"
    p_face = AVATAR_DIR / "assets" / "face.json"
    ok("2.5D 人像", p_portrait.exists(),
       f"{p_portrait.stat().st_size/1024:.0f} KB" if p_portrait.exists() else "缺 portrait.png")
    ok("五官坐标", p_face.exists(), "face.json" if p_face.exists() else "缺 face.json（跑 locate.py 生成）")
    bundle = AVATAR_DIR / "dist" / "avatar.bundle.js"
    ok("前端产物", bundle.exists(), f"{bundle.stat().st_size/1024:.0f} KB" if bundle.exists() else "缺失")
    ok("控制台页", CONSOLE_FILE.exists(), str(CONSOLE_FILE))
    ok("检测库", tools.DB_PATH.exists(), str(tools.DB_PATH))
    try:
        ov = tools.overview(1)
        ok("概览可用", True, f"今日 {ov['detections']} 条")
    except Exception as e:
        ok("概览可用", False, f"{type(e).__name__}: {e}")
    try:
        h = WATCHER.check_once(False)
        ok("健康巡检", "error" not in h, f"degraded={h.get('degraded')}")
    except Exception as e:
        ok("健康巡检", False, f"{type(e).__name__}: {e}")
    try:
        import agent
        ok("Agent 通道", True, f"llm={'已配 key' if agent.has_key() else '未配 key（可用离线规则模式）'}")
    except Exception as e:
        ok("Agent 通道", False, f"{type(e).__name__}: {e}")

    print("=" * 64)
    print("自检通过" if not fails else f"失败：{fails}")
    return 0 if not fails else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-watch", action="store_true", help="不启动模型健康后台巡检")
    ap.add_argument("--check", action="store_true", help="只自检不起服务")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if args.check:
        return run_check(args.port)

    if not args.no_watch:
        WATCHER.start()

    srv = QuietServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    print("=" * 64)
    print(f"  数字人服务已启动  v{VERSION}")
    print(f"  形象窗口   http://{args.host}:{args.port}/avatar/")
    print(f"  控制台     http://{args.host}:{args.port}/console/")
    print(f"  事件流     http://{args.host}:{args.port}/api/events")
    print(f"  模型巡检   {'开启' if not args.no_watch else '关闭'}（30s 一次，走真实库）")
    print("=" * 64)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        WATCHER.stop_flag.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
