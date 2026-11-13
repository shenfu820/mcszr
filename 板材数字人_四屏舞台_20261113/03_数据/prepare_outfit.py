# -*- coding: utf-8 -*-
"""把 USDZ 里那件上衣的"料子"取出来，做成舞台能用的两张图：

  assets/jacket_swatch.png   真实布片（128×128，从 tex0 图集里挑的最平的一块深蓝）
  assets/jacket_grain.png    布纹起伏（灰度，128=不改变亮度），给着色器做平铺调制

同时把测出来的主色写进 outfit.json，供前端和文档引用。

用法：
  E:\\conda3\\envs\\yoloces\\python.exe 03_数据\\prepare_outfit.py [usdz路径]
"""
from __future__ import annotations

import io
import json
import os
import sys
import zipfile

import cv2
import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(PKG, '02_四屏舞台', 'assets')
DATA = os.path.join(PKG, '03_数据')
DEFAULT_USDZ = (r'F:\微信聊天\xwechat_files\wxid_y7652tbbf4d122_957b\temp\RWTemp'
                r'\2026-11\9e20f478899dc29eb19741386f9343c8\未命名对象 3.usdz')
PATCH = 128


def read_atlas(usdz_path):
    with zipfile.ZipFile(usdz_path) as z:
        names = [n for n in z.namelist() if n.lower().endswith('_tex0.png')]
        if not names:
            names = [n for n in z.namelist() if n.lower().endswith('.png')]
        if not names:
            raise SystemExit('usdz 里没有贴图')
        buf = z.read(names[0])
        info = {'entry': names[0], 'bytes': len(buf)}
    arr = np.frombuffer(buf, dtype=np.uint8)
    tex = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if tex is None:
        raise SystemExit('贴图解码失败')
    return tex, info


def main():
    usdz = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_USDZ
    os.makedirs(ASSETS, exist_ok=True)
    tex, info = read_atlas(usdz)
    h, w = tex.shape[:2]
    rgb = cv2.cvtColor(tex, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    R, G, B = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx, mn = rgb.max(axis=2), rgb.min(axis=2)
    sat = (mx - mn) / np.maximum(mx, 1e-6)
    navy = (B > R * 1.12) & (B > G * 1.05) & (mx > 0.12) & (mx < 0.62) & (sat > 0.25)
    if navy.sum() < 1000:
        raise SystemExit('图集里没找到足够多的深蓝布料像素')
    median = np.median(rgb[navy], axis=0)

    gray = cv2.cvtColor(tex, cv2.COLOR_BGR2GRAY).astype(np.float32)
    grad = np.sqrt(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3) ** 2 +
                   cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3) ** 2)
    orange = (R > 0.55) & (G < 0.45) & (B < 0.35)
    orange_d = cv2.dilate(orange.astype(np.uint8), np.ones((31, 31), np.uint8))

    best = None
    for y in range(0, h - PATCH, 32):
        for x in range(0, w - PATCH, 32):
            if navy[y:y + PATCH, x:x + PATCH].mean() < 0.97:
                continue
            if orange_d[y:y + PATCH, x:x + PATCH].any():
                continue
            s = grad[y:y + PATCH, x:x + PATCH].mean()
            if best is None or s < best[0]:
                best = (s, x, y)
    if best is None:
        raise SystemExit('没找到干净的布料块')
    _, px, py = best
    patch = tex[py:py + PATCH, px:px + PATCH]

    p_rgb = cv2.cvtColor(patch, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    lum = 0.2126 * p_rgb[..., 0] + 0.7152 * p_rgb[..., 1] + 0.0722 * p_rgb[..., 2]
    grain = lum / max(1e-6, float(lum.mean()))
    grain = np.clip(grain, 0.85, 1.15)
    grain8 = np.clip((grain - 1.0) * 3.0 + 0.5, 0, 1) * 255.0
    grain8 = grain8.astype(np.uint8)

    # cv2.imwrite 不支持中文路径，统一用 PIL 落盘
    from PIL import Image
    Image.fromarray(cv2.cvtColor(patch, cv2.COLOR_BGR2RGB)).save(
        os.path.join(ASSETS, 'jacket_swatch.png'))
    Image.fromarray(grain8, 'L').save(os.path.join(ASSETS, 'jacket_grain.png'))
    out = {
        'source_usdz': usdz,
        'atlas_entry': info['entry'],
        'atlas_size': [w, h],
        'jacket_navy_median_rgb': [round(float(v), 4) for v in median],
        'jacket_navy_hex': '#%02x%02x%02x' % tuple(int(round(v * 255)) for v in median),
        'patch_box': [int(px), int(py), PATCH],
        'grain_std': round(float(grain.std()), 4),
        'note': '颜色与布纹取样自 USDZ 的 tex0 图集；布纹图 128=不改变亮度，'
                '最大 ±8% 调制，避免把褶皱盖掉。',
    }
    with open(os.path.join(DATA, 'outfit.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print('atlas %s (%dx%d)' % (info['entry'], w, h))
    print('上衣主色 RGB(0-255):', (median * 255).round(1).tolist(), out['jacket_navy_hex'])
    print('布片取自', out['patch_box'], 'grain std=%.4f' % out['grain_std'])
    print('written ->', os.path.join(ASSETS, 'jacket_swatch.png'))
    print('written ->', os.path.join(ASSETS, 'jacket_grain.png'))
    print('written ->', os.path.join(DATA, 'outfit.json'))


if __name__ == '__main__':
    main()
