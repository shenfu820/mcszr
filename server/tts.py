# -*- coding: utf-8 -*-
"""
数字人 · 动态语音合成（TTS）
============================
对话里现生成的句子没法预渲染，这里的取舍是「现场优先」：

  主路：Windows 内置 SAPI（Microsoft Huihui，zh-CN），**完全离线、约 0.3s 出声**，
        音色偏机器但绝不哑。
  备路：edge-tts（有网时音质更好，好听过 SAPI，但断网就废）。
        通过 config/llm.json 里的 "tts": "edge" 打开。

实现注意：中文不能走命令行参数传（Windows 控制台编码会吃字），
所以先把文本写成 UTF-8 文件，再让 PowerShell 读文件合成。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "voice" / "dyn"
CFG_FILE = ROOT / "config" / "llm.json"

SAPI_VOICE = "Microsoft Huihui Desktop"


def _cfg() -> dict:
    try:
        return json.loads(CFG_FILE.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _rel(path: Path) -> str:
    """对外给 URL 用的相对路径（相对 数字人/，主服务把 /voice/ 映射到 voice/）。"""
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def sapi_say(text: str, out: Path, rate: int = 0) -> bool:
    txt = out.with_suffix(".txt")
    txt.write_text(text, encoding="utf-8-sig")
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"try {{ $s.SelectVoice('{SAPI_VOICE}') }} catch {{}}; "
        f"$s.Rate = {int(rate)}; "
        f"$s.SetOutputToWaveFile('{out}'); "
        f"$t = Get-Content -LiteralPath '{txt}' -Encoding UTF8 -Raw; "
        "$s.Speak($t); $s.Dispose()"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                           capture_output=True, text=True, timeout=25,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ok = out.exists() and out.stat().st_size > 2000
        if not ok and r.returncode != 0:
            sys.stderr.write(f"[tts] SAPI 失败 rc={r.returncode} {r.stderr[:200]}\n")
        return ok
    except Exception as e:
        sys.stderr.write(f"[tts] SAPI 异常 {type(e).__name__}: {e}\n")
        return False


def edge_say(text: str, out: Path) -> bool:
    """可选：音质更好但需要网络。只在配置里显式打开时才用。"""
    try:
        import asyncio
        import edge_tts  # 只在 .venv 里有

        async def _run():
            c = edge_tts.Communicate(text, "zh-CN-YunxiNeural", rate="+6%")
            await c.save(str(out))
        asyncio.run(_run())
        return out.exists() and out.stat().st_size > 2000
    except Exception as e:
        sys.stderr.write(f"[tts] edge-tts 不可用 {type(e).__name__}: {e}\n")
        return False


def synth(text: str) -> dict:
    """合成一句话，返回 {ok, url, file, engine, ms, cache}。"""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "空文本"}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cfg = _cfg()
    engine = (cfg.get("tts") or "sapi").lower()
    ext = ".mp3" if engine == "edge" else ".wav"   # edge-tts 出来是 mp3，扩展名不能骗浏览器
    key = hashlib.md5((engine + "|" + text).encode("utf-8")).hexdigest()[:16]
    out = CACHE_DIR / f"{key}{ext}"
    if out.exists() and out.stat().st_size > 2000:
        return {"ok": True, "url": "/" + _rel(out), "file": str(out),
                "engine": engine, "ms": 0, "cache": True}

    t0 = time.time()
    ok = edge_say(text, out) if engine == "edge" else sapi_say(text, out)
    if not ok and engine != "sapi":          # 断网/失败就退回离线 SAPI，别哑
        out = CACHE_DIR / f"{key}.wav"
        ok = sapi_say(text, out)
        engine = "sapi(fallback)"
    ms = int((time.time() - t0) * 1000)
    if not ok:
        return {"ok": False, "error": "两条 TTS 路都失败", "ms": ms}
    return {"ok": True, "url": "/" + _rel(out), "file": str(out),
            "engine": engine, "ms": ms, "cache": False}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8-sig", errors="replace")
    t = sys.argv[1] if len(sys.argv) > 1 else "好的，我已经收到指令，正在把模型调起来。"
    print(json.dumps(synth(t), ensure_ascii=False))
