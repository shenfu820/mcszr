# -*- coding: utf-8 -*-
"""
数字人 · 离线语音识别服务（ASR）
================================
跑在 `voice` 环境（funasr 1.4.24 + torchaudio + soundfile，全离线）：
    E:\\conda3\\envs\\voice\\python.exe server\\asr_service.py --port 8773

为什么单开一个进程：
  - funasr 只装在 voice 环境，8772 主服务跑在 .venv，两边解释器不同；
  - 模型首次加载要 ~2 分钟（下载 990MB + 1.13GB 两个模型），
    所以必须**启动时就热好**，不能等第一句话再说，否则现场第一句会卡死。

接口（只在本机 127.0.0.1 上开）：
  GET  /health         -> {ok, ready, model, warm_secs, calls}
  POST /asr            body 为 16k/mono WAV 字节（浏览器录的），返 {ok, text, ms, rtf}
  POST /asr?fmt=path   body 为 JSON {"path": "..."}，返同样结构

识别不到内容时返回空 text（不是报错），上层据此决定“没听清，再说一遍”。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np

STATE = {"ready": False, "model": "paraformer-zh+fsmn-vad+ct-punc", "warm_secs": None,
         "calls": 0, "t0": time.time(), "last": None, "error": None}

# 板材场景的领域热词，专治「检出」被听成「剪出」这类同音错误
HOTWORD = "板材 缺陷 检出 检测 干节 健全节 边界 小节 裂纹 波纹 预处理 降噪 增强 归一化 " \
          "标注 数据集 训练 评估 模型 自学习 复核 转人工 大屏 可视化 GPU 显存 检测效率 准确率"

MODEL = None


def load_model():
    from funasr import AutoModel
    global MODEL
    t = time.time()
    MODEL = AutoModel(model="paraformer-zh", vad_model="fsmn-vad", punc_model="ct-punc",
                      device="cpu", disable_update=True)
    STATE["warm_secs"] = round(time.time() - t, 1)
    STATE["ready"] = True
    print(f"[asr] 模型就绪，用时 {STATE['warm_secs']}s", flush=True)


def transcribe_wav_bytes(raw: bytes) -> dict:
    """WAV 字节 -> 文本。非 16k 会重采样，浏览器录的 44.1k/48k 都能吃。"""
    import soundfile as sf
    t0 = time.time()
    audio, sr = sf.read(io.BytesIO(raw), dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(int(sr), 16000)
        audio = resample_poly(audio, 16000 // g, int(sr) // g).astype(np.float32)
        sr = 16000
    dur = len(audio) / sr
    res = MODEL.generate(input=audio, fs=16000, batch_size_s=60, hotword=HOTWORD)
    text = (res[0].get("text", "") if res else "") or ""
    ms = round((time.time() - t0) * 1000)
    return {"ok": True, "text": text.strip(), "ms": ms, "audio_secs": round(dur, 2),
            "rtf": round(ms / 1000 / max(0.01, dur), 3)}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/health":
            return self._json({"ok": True, **{k: STATE[k] for k in
                                             ("ready", "model", "warm_secs", "calls", "last", "error")},
                               "uptime": round(time.time() - STATE["t0"], 1)})
        return self._json({"ok": False, "error": "用 POST /asr"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/asr":
            return self._json({"ok": False, "error": "未知路径"}, 404)
        if not STATE["ready"]:
            return self._json({"ok": False, "error": "模型还在热身，稍等",
                               "warm_secs": STATE["warm_secs"]}, 503)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            fmt = parse_qs(u.query).get("fmt", ["wav"])[0]
            if fmt == "path":
                path = json.loads(raw.decode("utf-8"))["path"]
                with open(path, "rb") as f:
                    raw = f.read()
            if len(raw) < 1000:
                return self._json({"ok": False, "error": f"音频太短（{len(raw)} 字节）"})
            out = transcribe_wav_bytes(raw)
            STATE["calls"] += 1
            STATE["last"] = {"text": out["text"], "ms": out["ms"], "at": time.strftime("%H:%M:%S")}
            return self._json(out)
        except Exception as e:
            STATE["error"] = f"{type(e).__name__}: {e}"
            return self._json({"ok": False, "error": STATE["error"],
                               "tb": traceback.format_exc()[-600:]}, 500)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8773)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    print(f"[asr] 启动，端口 {args.port}，开始加载模型（首次约 2 分钟）……", flush=True)
    try:
        load_model()
    except Exception as e:
        STATE["error"] = f"{type(e).__name__}: {e}"
        print("[asr] 模型加载失败：", STATE["error"], flush=True)
        traceback.print_exc()
        return 2
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[asr] READY http://{args.host}:{args.port}/health", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
