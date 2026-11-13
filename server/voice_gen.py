# -*- coding: utf-8 -*-
"""
数字人语音预渲染
================
比赛现场不联网、不赌实时合成：这里一次性把剧本里每条台词渲染成 mp3，
连同时长一起写进 voice/manifest.json。前端只播文件。

用法（用项目内 .venv，装过 edge-tts）：
    .venv\\Scripts\\python.exe server\\voice_gen.py            # 缺什么补什么
    .venv\\Scripts\\python.exe server\\voice_gen.py --force    # 全部重生成
    .venv\\Scripts\\python.exe server\\voice_gen.py --list     # 只看有哪些音色
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "script" / "script.json"
VOICE_DIR = ROOT / "voice"
MANIFEST = VOICE_DIR / "manifest.json"


def load_script() -> dict:
    return json.loads(SCRIPT.read_text(encoding="utf-8"))


async def synth(text: str, voice: str, rate: str, pitch: str, out: Path) -> float:
    """合成一条语音，返回估算时长（秒）。

    edge-tts 默认输出 audio-24khz-48kbitrate-mono-mp3 = 48000 bit/s，
    因此时长 ≈ 字节数 * 8 / 48000。WordBoundary 事件在部分版本不稳定，
    这里以字节数为准做推导（标注为估算值，前端只把它当字幕兜底下限）。
    """
    import edge_tts

    comm = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
    buf = bytearray()
    async for chunk in comm.stream():
        if chunk.get("type") == "audio":
            buf.extend(chunk["data"])
    out.write_bytes(bytes(buf))
    return len(buf) * 8 / 48000.0


async def list_voices(filter_prefix: str = "zh-CN") -> None:
    import edge_tts

    voices = await edge_tts.list_voices()
    for v in voices:
        if v["ShortName"].startswith(filter_prefix):
            print(f"{v['ShortName']:<34} {v['Gender']:<7} {v.get('FriendlyName', '')}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="已存在的也重生成")
    ap.add_argument("--list", action="store_true", help="列出中文音色后退出")
    ap.add_argument("--only", default="", help="只生成某个 cue id")
    args = ap.parse_args()

    if args.list:
        asyncio.run(list_voices())
        return 0

    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    data = load_script()
    persona = data.get("persona", {})
    voice = persona.get("voice", "zh-CN-XiaoxiaoNeural")
    rate = persona.get("rate", "+0%")
    pitch = persona.get("pitch", "+0Hz")

    manifest: dict = {}
    if MANIFEST.exists():
        try:
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    # 音色/语速以 script.json 为准，每次都覆盖（用 setdefault 会导致换音色不生效）
    manifest["voice"] = voice
    manifest["rate"] = rate
    manifest["pitch"] = pitch
    manifest.setdefault("clips", {})

    cues = data.get("cues", [])
    ok, skip, fail = 0, 0, 0
    for cue in cues:
        cid = cue["id"]
        if args.only and cid != args.only:
            continue
        text = cue.get("text", "").strip()
        if not text:
            continue
        out = VOICE_DIR / f"{cid}.mp3"
        if out.exists() and not args.force:
            fr = out.stat().st_size
            if fr > 2000:
                print(f"  skip  {cid:<20} {fr/1024:.1f} KB")
                manifest["clips"].setdefault(cid, {"file": f"{cid}.mp3", "text": text})
                skip += 1
                continue
        try:
            dur = asyncio.run(synth(text, voice, rate, pitch, out))
            size = out.stat().st_size
            manifest["clips"][cid] = {
                "file": f"{cid}.mp3",
                "text": text,
                "dur": round(dur, 2),
                "dur_est": True,
                "bytes": size,
                "scene": cue.get("scene", ""),
            }
            print(f"  ok    {cid:<20} {size/1024:6.1f} KB  {dur:5.2f}s  {text[:24]}…")
            ok += 1
        except Exception as e:
            print(f"  FAIL  {cid:<20} {type(e).__name__}: {e}")
            fail += 1

    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(1 for _ in VOICE_DIR.glob("*.mp3"))
    print(f"\n生成 {ok} 条，跳过 {skip} 条，失败 {fail} 条；voice/ 共 {total} 个 mp3")
    print(f"清单：{MANIFEST}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
