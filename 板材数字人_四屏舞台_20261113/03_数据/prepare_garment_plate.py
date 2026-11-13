# -*- coding: utf-8 -*-
"""商品图夹克 -> 舞台贴片（jacket_plate.png），并在 Python 里先合成一张预览，
参数调好了再进着色器，避免来回截图。"""
from __future__ import annotations

import io
import json
import os
import sys

import cv2
import numpy as np
from PIL import Image, ImageFilter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(PKG, '02_四屏舞台', 'assets')
DATA = os.path.join(PKG, '03_数据')
TMP = r'C:\Users\Administrator\AppData\Local\Temp\garment_work'
VIDEO = os.path.join(PKG, '01_人物素材_复用', 'H3', 'idle_rgb_alpha.mp4')
DEFAULT_IMG = (r'C:\Users\Administrator\.dsh\attachments\v1\objects\ec'
               r'\ec803afdea0d5b00cf1fc95d22c42ad53fcfdb673ccee27a01659b2294028415')
W, H = 768, 1344
os.makedirs(TMP, exist_ok=True)


def imread_u(path):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def imwrite_u(path, bgr):
    ok, buf = cv2.imencode(os.path.splitext(path)[1] or '.png', bgr)
    if ok:
        buf.tofile(path)


def cutout(img):
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.int16)
    near_white = (rgb.min(axis=2) > 232).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(near_white, 8)
    h, w = near_white.shape
    bg = np.zeros((h, w), bool)
    for i in range(1, n):
        x, y, ww, hh, _a = stats[i]
        if x <= 1 or y <= 1 or x + ww >= w - 1 or y + hh >= h - 1:
            bg |= (lab == i)
    alpha = ((~bg).astype(np.float32) * 255).astype(np.uint8)
    return cv2.morphologyEx(alpha, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))


def jacket_only(img, alpha, x_cut, y_cut):
    """把右边的裤子和底部的商品文字切掉（它们和衣服连在一起），再羽化切口。"""
    a = alpha.copy()
    a[:, int(x_cut):] = 0
    a[int(y_cut):, :] = 0
    a = cv2.morphologyEx(a, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    ys, xs = np.where(a > 127)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    sub = img[y0:y1 + 1, x0:x1 + 1].copy()
    sub_a = a[y0:y1 + 1, x0:x1 + 1].copy()
    return sub, sub_a


def person_frame():
    cap = cv2.VideoCapture(VIDEO)
    ok, frame = cap.read()
    cap.release()
    h, w = frame.shape[:2]
    rgb = cv2.cvtColor(frame[:, : w // 2], cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    alpha = frame[:, w // 2:][:, :, 0].astype(np.float32) / 255.0
    return rgb, alpha


def suit_band(rgb, alpha):
    """量小星西装上衣的范围：先用 alpha 定肩线/胯线，再在腰线以上找深蓝像素的横向范围。"""
    R, G, B = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    Y = 0.2126 * R + 0.7152 * G + 0.0722 * B
    suit = (B > R * 1.08) & (Y < 0.22) & (alpha > 0.85)

    a = alpha > 0.6
    rows = np.where(a.any(axis=1))[0]
    y0, y1 = int(rows.min()), int(rows.max())
    heights = y1 - y0 + 1
    widths = np.array([(lambda c: 0 if not len(c) else int(c.max() - c.min() + 1))(np.where(a[y])[0])
                       for y in range(y0, y1 + 1)])
    upper = widths[:int(heights * 0.35)]
    shoulder_y = y0 + int(np.argmax(upper >= upper.max() * 0.80))
    hip_y = shoulder_y + int(round((y1 - shoulder_y) * 0.44))

    band = suit[shoulder_y + 10: hip_y + 1]
    xs_b = np.where(band.sum(axis=0) > 2)[0]
    info = {
        'person_y0': y0, 'person_y1': y1,
        'shoulder_y': int(shoulder_y), 'hip_y': int(hip_y),
        'x0': int(xs_b.min()) if len(xs_b) else int(np.where(a[shoulder_y:hip_y].any(axis=0))[0].min()),
        'x1': int(xs_b.max()) if len(xs_b) else int(np.where(a[shoulder_y:hip_y].any(axis=0))[0].max()),
        'suit_pixels': int(suit.sum()),
    }
    return suit, info


def compose_preview(rgb, alpha, plate, suit, exposure=0.62, tag=''):
    """合成预览：商品图颜色 × 曝光系数 × 小星自己的褶皱，别做绝对亮度匹配（会压成黑块）。"""
    pl = plate.astype(np.float32) / 255.0
    prgb, pa = pl[:, :, :3], pl[:, :, 3]
    Yperson = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    band = suit & (pa > 0.5)
    Ys = float(np.median(Yperson[band])) if band.any() else 0.03
    fold = np.clip(Yperson / max(Ys, 1e-5), 0.55, 1.6) ** 0.75
    shaded = np.clip(prgb * (exposure * fold)[..., None], 0, 1)
    out = rgb * (1 - pa[..., None]) + shaded * pa[..., None]
    a = alpha[..., None]
    comp = out * a + np.array([0.05, 0.055, 0.06]) * (1 - a)
    img = Image.fromarray((np.clip(comp, 0, 1) * 255).astype(np.uint8))
    img.thumbnail((600, 1200))
    name = 'preview%s.png' % tag
    img.save(os.path.join(TMP, name))
    print('exposure=%.2f Ys=%.4f -> %s' % (exposure, Ys, name))


def main():
    img_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IMG
    x_cut_ratio = float(sys.argv[2]) if len(sys.argv) > 2 else 0.75
    y_cut_ratio = float(sys.argv[3]) if len(sys.argv) > 3 else 0.86
    img = imread_u(img_path)
    print('garment image', img.shape)
    alpha = cutout(img)
    x_cut = int(img.shape[1] * x_cut_ratio)
    y_cut = int(img.shape[0] * y_cut_ratio)
    jac, jac_a = jacket_only(img, alpha, x_cut, y_cut)
    print('jacket crop', jac.shape, 'alpha cover %.1f%%' % (100.0 * (jac_a > 127).mean()))
    imwrite_u(os.path.join(TMP, 'jacket_crop.png'), jac)

    rgb, palpha = person_frame()
    suit, info = suit_band(rgb, palpha)
    print('person suit band', info)

    # 目标框：横向取西装宽度，纵向取肩线到胯线
    inset = 10
    tx0, tx1 = info['x0'] + inset, info['x1'] - inset
    ty0 = info['shoulder_y'] - int((info['hip_y'] - info['shoulder_y']) * 0.16)   # 领口要盖住脖子下缘
    ty1 = info['hip_y'] + int((info['hip_y'] - info['shoulder_y']) * 0.04)
    tw, th = tx1 - tx0 + 1, ty1 - ty0 + 1
    jh, jw = jac_a.shape[:2]
    jac_r = cv2.resize(jac, (tw, th), interpolation=cv2.INTER_LANCZOS4)
    blur = cv2.GaussianBlur(jac_r, (0, 0), 1.2)
    jac_r = cv2.addWeighted(jac_r, 1.22, blur, -0.22, 0)
    a_r = cv2.resize(jac_a, (tw, th), interpolation=cv2.INTER_LANCZOS4)
    a_r = cv2.erode(a_r, np.ones((3, 3), np.uint8), iterations=4)   # 收 4px，切边毛刺/暗边不会漏到西装上

    plate = np.zeros((H, W, 4), np.uint8)
    jac_r = cv2.cvtColor(jac_r, cv2.COLOR_BGR2RGB)
    plate[ty0:ty1 + 1, tx0:tx1 + 1, :3] = jac_r
    plate[ty0:ty1 + 1, tx0:tx1 + 1, 3] = a_r

    # 领口：把贴片在自己脖子的位置挖空，让脖子和下巴露出来（否则像戴了个高领）
    neck_cx = int(info.get('center_x', (tx0 + tx1) / 2)) if isinstance(info.get('center_x'), (int, float)) \
        else int((tx0 + tx1) / 2)
    neck_cx = int((tx0 + tx1) / 2)
    neck_cy = ty0 + int((ty1 - ty0) * 0.06)
    neck_rx = int((tx1 - tx0) * 0.13)
    neck_ry = int((ty1 - ty0) * 0.10)
    yy, xx = np.mgrid[0:H, 0:W]
    neck = (((xx - neck_cx) / max(1, neck_rx)) ** 2 + ((yy - neck_cy) / max(1, neck_ry)) ** 2) <= 1.0
    plate[neck, 3] = 0
    print('neck hole at (%d,%d) rx=%d ry=%d' % (neck_cx, neck_cy, neck_rx, neck_ry))

    a_img = Image.fromarray(plate[:, :, 3], 'L').filter(ImageFilter.GaussianBlur(1.0))
    plate[:, :, 3] = np.array(a_img)

    out_png = os.path.join(ASSETS, 'jacket_plate.png')
    Image.fromarray(plate, 'RGBA').save(out_png)
    print('plate ->', out_png, os.path.getsize(out_png))
    print('target box x[%d..%d] y[%d..%d]  jacket %dx%d -> %dx%d' % (
        tx0, tx1, ty0, ty1, jw, jh, tw, th))

    for exp in (0.55, 0.75, 0.95):
        compose_preview(rgb, palpha, plate, suit, exp, tag='_%02d' % int(exp * 100))

    op = plate[:, :, 3] > 200
    px = plate[:, :, :3][op].astype(np.float32) / 255.0
    if len(px):
        mx, mn = px.max(axis=1), px.min(axis=1)
        sat = (mx - mn) / np.maximum(mx, 1e-6)
        navy = px[(px[:, 2] > px[:, 0] * 1.05) & (sat > 0.18)]
        grey = px[(sat < 0.12) & (mx > 0.30)]
        p = os.path.join(DATA, 'outfit.json')
        old = {}
        if os.path.exists(p):
            try:
                old = json.load(open(p, encoding='utf-8'))
            except Exception:                     # noqa: BLE001
                old = {}
        old.update({
            'garment_source': img_path,
            'plate': 'assets/jacket_plate.png',
            'plate_box': [int(tx0), int(ty0), int(tx1), int(ty1)],
            'navy_rgb': [round(float(v), 4) for v in (np.median(navy, axis=0) if len(navy) else [0.1, 0.13, 0.2])],
            'grey_rgb': [round(float(v), 4) for v in (np.median(grey, axis=0) if len(grey) else [0.6, 0.6, 0.6])],
            'note': '商品图 352×423，抠白底→切掉裤子→按小星西装上衣的范围（肩线到胯线）拉伸贴上，'
                    '合成时用商品图明暗归一 + 小星自己的褶皱调制。',
        })
        json.dump(old, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print('navy', old['navy_rgb'], 'grey', old['grey_rgb'])


if __name__ == '__main__':
    main()
