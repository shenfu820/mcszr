# -*- coding: utf-8 -*-
"""
数字人 · 语音识别客户端（8772 主服务侧）
=======================================
ASR 在 voice 环境单独跑一个进程（见 asr_service.py），这里只做 HTTP 调用 + 健康检查。
服务没起的时候要**明确报错**，不能让上层误以为「听懂了但没内容」。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

ASR_URL = os.environ.get("DH_ASR_URL", "http://127.0.0.1:8773")
TIMEOUT = float(os.environ.get("DH_ASR_TIMEOUT", "25"))

# 同音纠错表：Paraformer 在板材语境下稳定犯的几个错（实测「检出」→「剪出」）。
# 这是确定性的词表替换，不是模型猜的，所以可以写明来源。
CORRECTIONS = [
    ("剪出", "检出"), ("捡出", "检出"), ("减出", "检出"),
    ("波文", "波纹"), ("波蚊", "波纹"), ("木文", "木纹"),
    ("大频", "大屏"), ("看版", "看板"),
    ("干结", "干节"), ("健全结", "健全节"), ("边界节", "边界"),
]


def _fix(text: str) -> str:
    out = text
    for a, b in CORRECTIONS:
        out = out.replace(a, b)
    return out


def _post(path: str, raw: bytes, timeout: float = TIMEOUT) -> dict:
    req = urllib.request.Request(
        ASR_URL.rstrip("/") + path, data=raw, method="POST",
        headers={"Content-Type": "application/octet-stream"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(path: str, timeout: float = 3) -> dict:
    with urllib.request.urlopen(ASR_URL.rstrip("/") + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def health() -> dict:
    try:
        h = _get("/health")
        return {"state": "ready" if h.get("ready") else "warming", **h, "url": ASR_URL}
    except Exception as e:
        return {"state": "down", "ok": False, "url": ASR_URL,
                "error": f"{type(e).__name__}: {e}",
                "hint": "ASR 未启动：跑 启动数字人.bat，或用 voice 环境单起 asr_service.py"}


def transcribe(wav_bytes: bytes) -> dict:
    try:
        out = _post("/asr", wav_bytes)
        out["asr_url"] = ASR_URL
        if out.get("text"):
            fixed = _fix(out["text"])
            if fixed != out["text"]:
                out["raw_text"] = out["text"]
                out["text"] = fixed
        return out
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        return {"ok": False, "error": f"ASR HTTP {e.code}: {detail}", "asr_url": ASR_URL}
    except Exception as e:
        return {"ok": False, "error": f"ASR 连不上（{type(e).__name__}: {e}）",
                "asr_url": ASR_URL}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(health(), ensure_ascii=False, indent=2))
    if len(sys.argv) > 1:
        raw = open(sys.argv[1], "rb").read()
        print(json.dumps(transcribe(raw), ensure_ascii=False, indent=2))
