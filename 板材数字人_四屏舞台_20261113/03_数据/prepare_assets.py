# -*- coding: utf-8 -*-
"""人物素材复用：把"小星"的现用素材复制进本舞台包，并留 SHA256 来源清单。

只读源包（01_现用人物素材），不修改源包任何文件。
同时生成 pose_still_rgba.png（idle 首帧 + 右半 Alpha 合成），作为浏览器解码失败时的兜底图。

运行：E:\\conda3\\envs\\yoloces\\python.exe 03_数据\\prepare_assets.py
"""
import hashlib
import io
import json
import os
import shutil
import sys

import cv2
import numpy as np
from PIL import Image

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_PKG = r'C:\Users\Administrator\Desktop\数字人\小星数字人_建模与制作素材_20261113'
SRC_H3 = os.path.join(SRC_PKG, '01_现用人物素材', 'H3')
SRC_ANCHOR = os.path.join(SRC_PKG, '01_现用人物素材', '现用形象定妆图_anchor_J.png')
DST = os.path.join(PKG, '01_人物素材_复用')


def sha256(path, buf=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(buf)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def main():
    os.makedirs(os.path.join(DST, 'H3'), exist_ok=True)
    items = []
    for name in ('idle_rgb_alpha.mp4', 'talk_rgb_alpha.mp4', 'plate.json'):
        s = os.path.join(SRC_H3, name)
        d = os.path.join(DST, 'H3', name)
        shutil.copy2(s, d)
        items.append({'name': 'H3/' + name, 'src': s, 'bytes': os.path.getsize(d),
                      'sha256_src': sha256(s), 'sha256_dst': sha256(d)})
    if os.path.exists(SRC_ANCHOR):
        d = os.path.join(DST, '现用形象定妆图_anchor_J.png')
        shutil.copy2(SRC_ANCHOR, d)
        items.append({'name': os.path.basename(d), 'src': SRC_ANCHOR,
                      'bytes': os.path.getsize(d),
                      'sha256_src': sha256(SRC_ANCHOR), 'sha256_dst': sha256(d)})

    # idle 首帧 + 右半 alpha -> RGBA 兜底图（只读源视频）
    cap = cv2.VideoCapture(os.path.join(DST, 'H3', 'idle_rgb_alpha.mp4'))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit('读取 idle_rgb_alpha.mp4 失败')
    h, w = frame.shape[:2]
    left = frame[:, :w // 2]
    right = frame[:, w // 2:]
    rgba = np.dstack([left[:, :, 2], left[:, :, 1], left[:, :, 0], right[:, :, 0]]).astype(np.uint8)
    still = os.path.join(DST, 'pose_still_rgba.png')
    Image.fromarray(rgba, 'RGBA').save(still)
    items.append({'name': 'pose_still_rgba.png', 'src': 'idle_rgb_alpha.mp4 首帧 + 右半 Alpha 合成',
                  'bytes': os.path.getsize(still), 'size': [int(w // 2), int(h)]})

    manifest = {
        'note': '人物素材复用：源为「小星数字人_建模与制作素材_20261113/01_现用人物素材」，'
                '本包只复制，不改源包；视频为 1536×1344（左半 RGB / 右半灰度 Alpha），'
                '人物实际画幅 768×1344。',
        'generated_at': __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'items': items,
    }
    p = os.path.join(DST, '来源与SHA256.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print('person assets ->', DST)
    for it in items:
        print('  %-28s %10d bytes' % (it['name'], it['bytes']))
    print('manifest ->', p)


if __name__ == '__main__':
    main()
