# -*- coding: utf-8 -*-
"""把商品图那件工装夹克拆成"版型图"，贴在小星身上 —— 不用商品图自己的光照，
只搬它的**结构**（灰拼色块、口袋、反光条、缝线），颜色和明暗全用他自己的。

产出：
  assets/jacket_maps.png   R=布料细节(128=不变) G=灰色拼接区 B=反光条 A=贴片遮罩
  顺带把藏青/灰的实测色写进 03_数据/outfit.json

为什么这样比直接贴照片真：照片的光照、透视和他是两套，硬贴一定"像贴纸"；
只搬结构、光照全用他视频里的，褶皱和明暗就自然跟着身体走。

用法：
  E:\\conda3\\envs\\yoloces\\python.exe 03_数据\\prepare_garment_fit.py [商品图路径]
"""
from __future__ import annotations

import io
import json
import os
import sys

import cv2
import numpy as np
from PIL import Image

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(PKG, '02_四屏舞台', 'assets')
DATA = os.path.join(PKG, '03_数据')
TMP = r'C:\Users\Administrator\AppData\Local\Temp\garment_fit'
VIDEO = os.path.join(PKG, '01_人物素材_复用', 'H3', 'idle_rgb_alpha.mp4')
DEFAULT_IMG = os.path.join(PKG, '01_人物素材_复用', '工装夹克_商品图.jpg')
W, H = 768, 1344
EXPOSURE = 0.82
os.makedirs(TMP, exist_ok=True)


def imread_u(path):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


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
    return ((~bg).astype(np.float32) * 255).astype(np.uint8)


def jacket_only(img, alpha, x_cut, y_cut):
    a = alpha.copy()
    a[:, int(x_cut):] = 0
    a[int(y_cut):, :] = 0
    a = cv2.morphologyEx(a, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    ys, xs = np.where(a > 127)
    sub = img[ys.min():ys.max() + 1, xs.min():xs.max() + 1].copy()
    sub_a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1].copy()
    sub = cv2.cvtColor(sub, cv2.COLOR_BGR2RGB)
    return sub, sub_a


def person_frame():
    cap = cv2.VideoCapture(VIDEO)
    ok, frame = cap.read()
    cap.release()
    h, w = frame.shape[:2]
    rgb = cv2.cvtColor(frame[:, : w // 2], cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    alpha = frame[:, w // 2:][:, :, 0].astype(np.float32) / 255.0
    return rgb, alpha


def masks(rgb, alpha):
    R, G, B = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    Y = 0.2126 * R + 0.7152 * G + 0.0722 * B
    body = alpha > 0.75
    skin = ((R - B) > 0.06) & (R > 0.16)
    white = (Y > 0.42) & (np.abs(R - B) < 0.10)
    suit = body & (~skin) & (~white) & (Y < 0.35)
    ys = np.where(body.any(axis=1))[0]
    y0, y1 = int(ys.min()), int(ys.max())
    heights = y1 - y0 + 1
    widths = np.array([0 if not body[y].any() else int(np.ptp(np.where(body[y])[0]) + 1)
                       for y in range(y0, y1 + 1)])
    upper = widths[: int(heights * 0.35)]
    shoulder_y = int(y0 + np.argmax(upper >= upper.max() * 0.80))
    hip_y = int(shoulder_y + (y1 - shoulder_y) * 0.46)
    return {'suit': suit, 'skin': skin, 'white': white, 'body': body,
            'y0': y0, 'y1': y1, 'shoulder_y': shoulder_y, 'hip_y': hip_y}


def garment_maps(jac, jac_a):
    """从夹克照片里拆出：细节起伏 / 灰拼接区 / 反光条。"""
    p = jac.astype(np.float32) / 255.0
    Y = 0.2126 * p[..., 0] + 0.7152 * p[..., 1] + 0.0722 * p[..., 2]
    blur = cv2.GaussianBlur(Y, (0, 0), 6.0)
    detail = np.clip(Y / np.maximum(blur, 1e-4), 0.82, 1.28)
    detail8 = ((detail - 0.82) / 0.46 * 255.0).clip(0, 255).astype(np.uint8)
    mx = p.max(axis=2)
    mn = p.min(axis=2)
    sat = (mx - mn) / np.maximum(mx, 1e-6)
    grey = ((sat < 0.13) & (Y > 0.40) & (Y < 0.72)).astype(np.uint8) * 255
    grey = cv2.morphologyEx(grey, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    grey = cv2.morphologyEx(grey, cv2.MORPH_CLOSE, np.ones((17, 17), np.uint8))
    grey = cv2.GaussianBlur(grey, (0, 0), 2.5)
    stripe = ((sat < 0.10) & (Y > 0.70)).astype(np.uint8) * 255
    stripe = cv2.morphologyEx(stripe, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    valid = jac_a > 200
    navy_px = p[valid & (p[..., 2] > p[..., 0] * 1.05) & (sat > 0.18) & (Y < 0.45)]
    grey_px = p[valid & (sat < 0.10) & (Y > 0.45) & (Y < 0.80)]
    return (np.dstack([detail8, grey, stripe]),
            [round(float(v), 4) for v in (np.median(navy_px, axis=0) if len(navy_px) else [0.1, 0.13, 0.2])],
            [round(float(v), 4) for v in (np.median(grey_px, axis=0) if len(grey_px) else [0.62, 0.62, 0.66])])


def warp_to_body(mk, maps, jac_a, jac=None):
    """逐行把小星上半身的左右边界喂给版型图，得到"穿在他身上"的遮罩与结构。"""
    suit = mk['suit']
    top = mk['shoulder_y'] - 4          # 从肩线开始贴，商品图那截立领不搬（否则像戴了个围兜）
    bottom = mk['hip_y'] - 14
    jh = jac_a.shape[0]
    out = np.zeros((H, W, 4), np.uint8)
    photo = np.zeros((H, W, 4), np.uint8)
    j_rows = []
    for i in range(jh):
        cols = np.where(jac_a[i] > 96)[0]
        j_rows.append((int(cols.min()), int(cols.max())) if len(cols) else None)

    body = mk['body']
    for y in range(top, bottom + 1):
        row_body = np.where(body[y])[0]          # 用整个人形（含袖子、手）定左右边界，别用西装掩码
        if len(row_body) < 6:
            continue
        xl, xr = int(row_body.min()) - 2, int(row_body.max()) + 2
        u = (y - top) / max(1, bottom - top)
        jy0 = int(jh * 0.06)               # 跳过商品图最上面那圈领口
        jy = jy0 + int(round(u * (jh - 1 - jy0)))
        jr = j_rows[jy]
        if jr is None:
            continue
        jx0, jx1 = jr
        if jx1 - jx0 < 4:
            continue
        tw = xr - xl + 1
        src = maps[jy, jx0:jx1 + 1]
        line = cv2.resize(src[None, :, :], (tw, 1), interpolation=cv2.INTER_AREA)[0]
        out[y, xl:xr + 1, :3] = line
        out[y, xl:xr + 1, 3] = 255
        if jac is not None:
            src_p = jac[jy, jx0:jx1 + 1]
            line_p = cv2.resize(src_p[None, :, :], (tw, 1), interpolation=cv2.INTER_AREA)[0]
            photo[y, xl:xr + 1, :3] = line_p
            photo[y, xl:xr + 1, 3] = 255

    keep = mk['body'] & (~mk['skin'])           # 人形范围内都算衣服区；手/脸另算
    keep = cv2.dilate(keep.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    a = out[:, :, 3].astype(np.float32) / 255.0 * keep.astype(np.float32)
    a *= (1.0 - mk['skin'].astype(np.float32))
    ramp = np.ones((H, 1), np.float32)
    for k in range(12):
        yy = bottom - k
        if 0 <= yy < H:
            ramp[yy] = (k + 1) / 13.0
    a *= ramp
    out[:, :, 3] = (cv2.GaussianBlur((np.clip(a, 0, 1) * 255).astype(np.uint8), (0, 0), 1.0))
    out[:, :, 0] = cv2.GaussianBlur(out[:, :, 0], (0, 0), 0.6)
    out[:, :, 1] = cv2.GaussianBlur(out[:, :, 1], (0, 0), 0.8)
    out[:, :, 2] = cv2.GaussianBlur(out[:, :, 2], (0, 0), 0.8)
    photo[:, :, 3] = out[:, :, 3]
    photo[:, :, :3] = cv2.GaussianBlur(photo[:, :, :3], (0, 0), 0.5)
    return out, photo


def preview(rgb, alpha, mk, maps_plate, navy, grey, exposure=EXPOSURE, tag=''):
    m = maps_plate.astype(np.float32) / 255.0
    detail = 0.82 + m[..., 0] * 0.46
    yoke = np.clip((m[..., 1] - 0.35) / 0.3, 0, 1)[..., None]
    stripe = np.clip((m[..., 2] - 0.35) / 0.3, 0, 1)[..., None]
    base = np.array(navy, np.float32) * (1 - yoke) + np.array(grey, np.float32) * yoke
    base = base * (1 - stripe * 0.35) + np.array([0.80, 0.80, 0.82], np.float32) * stripe * 0.35
    Yp = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    blur = cv2.GaussianBlur(Yp, (0, 0), 9.0)
    fold = np.clip(Yp / np.maximum(blur, 1e-4), 0.62, 1.5) ** 0.85
    lit = np.clip(base * (detail * exposure * fold)[..., None], 0, 1)
    a = m[..., 3:4]
    out = rgb * (1 - a) + lit * a
    img = Image.fromarray((np.clip(out * alpha[..., None] + 0.05 * (1 - alpha[..., None]), 0, 1) * 255).astype(np.uint8))
    img.thumbnail((620, 1240))
    name = 'maps%s.png' % tag
    img.save(os.path.join(TMP, name))
    print('preview ->', os.path.join(TMP, name))


def main():
    img_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IMG
    img = imread_u(img_path)
    alpha = cutout(img)
    jac, jac_a = jacket_only(img, alpha, int(img.shape[1] * 0.75), int(img.shape[0] * 0.86))
    print('garment', img.shape, '-> jacket', jac.shape)
    maps, navy, grey = garment_maps(jac, jac_a)
    print('navy', navy, 'grey', grey)

    rgb, palpha = person_frame()
    mk = masks(rgb, palpha)
    print('suit px %d shoulder_y %d hip_y %d' % (mk['suit'].sum(), mk['shoulder_y'], mk['hip_y']))

    plate, photo = warp_to_body(mk, maps, jac_a, jac)
    out_png = os.path.join(ASSETS, 'jacket_maps.png')
    Image.fromarray(plate, 'RGBA').save(out_png)
    Image.fromarray(photo, 'RGBA').save(os.path.join(ASSETS, 'jacket_fit.png'))
    print('photo plate ->', os.path.join(ASSETS, 'jacket_fit.png'))
    print('maps plate ->', out_png, os.path.getsize(out_png),
          'cover %.2f%%' % (100.0 * (plate[:, :, 3] > 40).mean()))
    for e in (0.75, 0.9):
        preview(rgb, palpha, mk, plate, navy, grey, e, tag='_%02d' % int(e * 100))

    p = os.path.join(DATA, 'outfit.json')
    old = {}
    if os.path.exists(p):
        try:
            old = json.load(open(p, encoding='utf-8'))
        except Exception:                        # noqa: BLE001
            old = {}
    old.update({'maps_plate': 'assets/jacket_maps.png', 'garment_source': img_path,
                'navy_rgb': navy, 'grey_rgb': grey, 'exposure': EXPOSURE,
                'align': {'shoulder_y': mk['shoulder_y'], 'hip_y': mk['hip_y'],
                          'y0': mk['y0'], 'y1': mk['y1']}})
    json.dump(old, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print('outfit.json updated')


if __name__ == '__main__':
    main()
