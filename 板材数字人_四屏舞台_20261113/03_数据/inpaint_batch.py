# -*- coding: utf-8 -*-
"""批量：把 idle / talk 两段人像视频逐帧"合成工装 + SD 重绘"，再编码成新的 alpha 视频。

自包含（不 import 其它脚本，避免 sys.stdout 被抢）；算法与 inpaint_refine.py 一致：
  合成 = 商品图贴片 × 他自己的局部明暗
  重绘 = SD1.5 inpainting，strength 0.58、36 步、固定 seed，遮罩含"脖子一圈"让 SD 自己长领子

输出（原始素材不动）：01_人物素材_复用/H3/<name>_rgb_alpha_work.mp4
用法：
  $env:SD_INPAINT_DIR='...snapshots\\master'
  python 03_数据\\inpaint_batch.py --video idle [--limit N] [--strength 0.58]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import cv2
import numpy as np
from PIL import Image

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(PKG, '02_四屏舞台', 'assets')
DATA = os.path.join(PKG, '03_数据')
H3 = os.path.join(PKG, '01_人物素材_复用', 'H3')
TMP = r'C:\Users\Administrator\AppData\Local\Temp\inpaint_batch'

MODEL = os.environ.get('SD_INPAINT_DIR') or 'runwayml/stable-diffusion-inpainting'
PROMPT = ('photo of a man wearing a navy blue workwear jacket with a light grey yoke panel across '
          'the shoulders and upper chest, two chest pockets with reflective strips, work uniform, '
          'natural fabric folds, same lighting as the photo, photorealistic, sharp fabric detail')
NEGATIVE = ('suit, blazer, lapels, tie, formal wear, illustration, cartoon, painting, deformed, '
            'extra limbs, blurry, plasticky')


def load_plate():
    p = os.path.join(ASSETS, 'jacket_fit.png')
    return np.array(Image.open(p).convert('RGBA')).astype(np.float32) / 255.0


def torso_masks(rgb, alpha):
    """body / skin / white / shoulder_y / hip_y —— 与 prepare_garment_fit 同口径。"""
    R, G, B = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    Y = 0.2126 * R + 0.7152 * G + 0.0722 * B
    body = alpha > 0.75
    skin = ((R - B) > 0.06) & (R > 0.16)
    white = (Y > 0.42) & (np.abs(R - B) < 0.10)
    ys = np.where(body.any(axis=1))[0]
    y0, y1 = int(ys.min()), int(ys.max())
    heights = y1 - y0 + 1
    widths = np.array([0 if not body[y].any() else int(np.ptp(np.where(body[y])[0]) + 1)
                       for y in range(y0, y1 + 1)])
    upper = widths[: int(heights * 0.35)]
    shoulder_y = int(y0 + np.argmax(upper >= upper.max() * 0.80))
    hip_y = int(shoulder_y + (y1 - shoulder_y) * 0.46)
    return {'body': body, 'skin': skin, 'white': white,
            'y0': y0, 'y1': y1, 'shoulder_y': shoulder_y, 'hip_y': hip_y}


def composite(rgb, plate, exposure=0.95):
    prgb, pa = plate[:, :, :3], plate[:, :, 3]
    Yp = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    blur = cv2.GaussianBlur(Yp, (0, 0), 9.0)
    fold = np.clip(Yp / np.maximum(blur, 1e-4), 0.62, 1.5) ** 0.85
    lit = np.clip(prgb * (exposure * fold)[..., None], 0, 1)
    return rgb * (1 - pa[..., None]) + lit * pa[..., None], pa


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--video', default='idle', choices=['idle', 'talk'])
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--strength', type=float, default=0.58)
    ap.add_argument('--steps', type=int, default=36)
    ap.add_argument('--tag', default='work')
    args = ap.parse_args()

    import torch
    from diffusers import StableDiffusionInpaintPipeline

    src = os.path.join(H3, '%s_rgb_alpha.mp4' % args.video)
    plate = load_plate()
    cap = cv2.VideoCapture(src)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n = total if args.limit <= 0 else min(total, args.limit)
    print('%s: %d frames @ %.1ffps %dx%d -> %d' % (args.video, total, fps, W, H, n), flush=True)

    pipe = StableDiffusionInpaintPipeline.from_pretrained(
        MODEL, torch_dtype=torch.float16, variant='fp16', safety_checker=None,
        requires_safety_checker=False, use_safetensors=True,
        local_files_only=os.path.isdir(MODEL)).to('cuda')

    outdir = os.path.join(TMP, args.video)
    os.makedirs(outdir, exist_ok=True)
    t0 = time.time()
    half = W // 2
    for i in range(n):
        ok, f = cap.read()
        if not ok:
            break
        rgb = cv2.cvtColor(f[:, :half], cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        alpha = f[:, half:][:, :, 0]
        comp, pa = composite(rgb, plate)
        mk = torso_masks(rgb, alpha.astype(np.float32) / 255.0)

        neck_band = np.zeros(rgb.shape[:2], np.uint8)
        y_chin = int(mk['y0'] + (mk['hip_y'] - mk['y0']) * 0.15)
        neck_band[y_chin:mk['shoulder_y'] + 14] = 255
        neck_band[~mk['body']] = 0
        neck_band[mk['skin']] = 0
        m = (pa > 0.20).astype(np.uint8) * 255
        m = np.maximum(m, neck_band)
        m = cv2.dilate(m, np.ones((15, 15), np.uint8))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))

        ys, xs = np.where(m > 0)
        if len(xs) == 0:
            res_rgb = (np.clip(comp, 0, 1) * 255).astype(np.uint8)
        else:
            pad = 56
            x0 = max(0, xs.min() - pad); x1 = min(rgb.shape[1], xs.max() + pad)
            y0 = max(0, ys.min() - pad - 30); y1 = min(rgb.shape[0], ys.max() + pad)
            crop = (np.clip(comp[y0:y1, x0:x1], 0, 1) * 255).astype(np.uint8)
            mcrop = m[y0:y1, x0:x1]
            ch, cw = crop.shape[:2]
            scale = 760.0 / ch
            nw, nh = int(round(cw * scale / 8) * 8), 760
            img_sd = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA)
            m_sd = cv2.resize(mcrop, (nw, nh), interpolation=cv2.INTER_NEAREST)
            out = pipe(prompt=PROMPT, negative_prompt=NEGATIVE,
                       image=Image.fromarray(img_sd), mask_image=Image.fromarray(m_sd),
                       height=nh, width=nw, num_inference_steps=args.steps,
                       strength=args.strength, guidance_scale=8.0,
                       generator=torch.Generator(device='cuda').manual_seed(20261113)).images[0]
            big = cv2.resize(np.array(out), (cw, ch), interpolation=cv2.INTER_LANCZOS4)
            region = (np.clip(comp, 0, 1) * 255).astype(np.uint8).astype(np.float32) / 255.0
            region[y0:y1, x0:x1] = np.clip(big.astype(np.float32) / 255.0, 0, 1)
            m_full = np.zeros(rgb.shape[:2], np.uint8)
            m_full[y0:y1, x0:x1] = mcrop
            m_soft = cv2.GaussianBlur(m_full, (0, 0), 2.0).astype(np.float32) / 255.0
            final = np.clip(comp, 0, 1) * (1 - m_soft[..., None]) + region * m_soft[..., None]
            res_rgb = (np.clip(final, 0, 1) * 255).astype(np.uint8)

        canvas = np.zeros((H, W, 3), np.uint8)
        canvas[:, :half] = cv2.cvtColor(res_rgb, cv2.COLOR_RGB2BGR)
        canvas[:, half:] = np.dstack([alpha, alpha, alpha])
        cv2.imwrite(os.path.join(outdir, 'f_%05d.png' % i), canvas)
        if i % 5 == 0 or i == n - 1:
            el = time.time() - t0
            print('  %d/%d  %.1fs  eta %.1f min' % (i + 1, n, el, (el / (i + 1)) * (n - i - 1) / 60),
                  flush=True)
    cap.release()

    dst = os.path.join(H3, '%s_rgb_alpha_%s.mp4' % (args.video, args.tag))
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [ff, '-y', '-framerate', '%.6f' % fps, '-i', os.path.join(outdir, 'f_%05d.png'),
               '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '17', '-preset', 'medium', dst]
        print('encode ->', dst, flush=True)
        subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    except Exception as e:                        # noqa: BLE001
        print('ffmpeg 不可用：', e, flush=True)
    if os.path.exists(dst):
        print('done: %s %.2f MB %.1f min' % (dst, os.path.getsize(dst) / 1048576,
                                             (time.time() - t0) / 60), flush=True)
        json.dump({'video': args.video, 'frames': n, 'strength': args.strength,
                   'steps': args.steps, 'model': MODEL, 'seed': 20261113, 'out': dst,
                   'seconds': round(time.time() - t0, 1)},
                  open(os.path.join(DATA, 'inpaint_batch_%s.json' % args.video), 'w',
                       encoding='utf-8'), ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
