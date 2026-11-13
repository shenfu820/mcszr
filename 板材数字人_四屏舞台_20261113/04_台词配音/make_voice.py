# -*- coding: utf-8 -*-
"""台词配音预渲染（edge-tts，只在生成时联网；现场只读本地 mp3）。

读 04_台词配音/script.json，为每条台词渲染 voice/<id>.mp3。
已有文件的默认跳过，--force 覆盖。

运行（需要装了 edge-tts 的解释器，例如数字人工程的 .venv）：
    C:\\Users\\Administrator\\Desktop\\数字人\\.venv\\Scripts\\python.exe 04_台词配音\\make_voice.py
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, 'script.json')
VOICE_DIR = os.path.join(HERE, 'voice')


async def render(text, voice, out, rate='+3%'):
    import edge_tts
    comm = edge_tts.Communicate(text, voice, rate=rate)
    await comm.save(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args()

    with open(SCRIPT, 'r', encoding='utf-8') as f:
        data = json.load(f)
    voice = (data.get('persona') or {}).get('voice') or 'zh-CN-YunxiNeural'
    os.makedirs(VOICE_DIR, exist_ok=True)

    made = skipped = 0
    for line in data['lines']:
        out = os.path.join(VOICE_DIR, line['id'] + '.mp3')
        if os.path.exists(out) and not args.force:
            skipped += 1
            continue
        asyncio.run(render(line['text'], voice, out))
        made += 1
        print('  %-9s %7d bytes  %s' % (line['id'], os.path.getsize(out), line['text'][:30] + '…'))
    print('voice:', VOICE_DIR, '| new=%d skip=%d voice=%s' % (made, skipped, voice))


if __name__ == '__main__':
    main()
