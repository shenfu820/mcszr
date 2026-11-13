# -*- coding: utf-8 -*-
"""用 SD1.5 inpainting 给 idle 首帧"真换一件工装夹克"，先出一张验证图。

流程：抠出西装区域当遮罩 → 裁出上半身 → SD inpainting 重绘 → 贴回原画幅 → 出对比图。
模型：runwayml/stable-diffusion-inpainting（fp16，走 hf-mirror）。

运行（必须用 .venv-sd 里的 python）：
  $env:HF_ENDPOINT='https://hf-mirror.com'
  C:\\Users\\Administrator\\Desktop\\数字人\\.venv-sd\\Scripts\\python.exe 03_数据\\inpaint_outfit.py
"""
from __future__ import annotations

import io
import os
import sys
import time

import cv2
import numpy as np
from PIL import Image

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIDEO = os.path.join(PKG, '01_人物素材_复用', 'H3', 'idle_rgb_alpha.mp4')
OUT = os.path.join(PKG, '06_验收截图')
TMP = r'C:\Users\Administrator\AppData\Local\Temp\inpaint_work'
os.makedirs(TMP, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

MODEL = os.environ.get('SD_INPAINT_DIR') or 'runwayml/stable-diffusion-inpainting'
PROMPT = ('photo of a man wearing a closed navy blue workwear jacket, light grey yoke panel across '
          'the shoulders and upper chest, zipper front, two chest pockets with flaps and reflective '
          'strips, work uniform, natural fabric folds, soft studio light, photorealistic, sharp detail')
NEGATIVE = ('suit, blazer, lapels, tie, necktie, formal wear, open jacket, white t-shirt, '
            'bare chest, cartoon, illustration, deformed, extra fingers, extra limbs, blurry')


def frame_rgba():
    cap = cv2.VideoCapture(VIDEO)
    ok, f = cap.read()
    cap.release()
    h, w = f.shape[:2]
    rgb = cv2.cvtColor(f[:, : w // 2], cv2.COLOR_BGR2RGB)
    alpha = f[:, w // 2:][:, :, 0]
    return rgb, alpha


def suit_mask(rgb, alpha):
    a = alpha.astype(np.float32) / 255.0
    R, G, B = [rgb[..., i].astype(np.float32) / 255.0 for i in range(3)]
    Y = 0.2126 * R + 0.7152 * G + 0.0722 * B
    body = a > 0.7
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
    m = np.zeros_like(body, np.uint8)
    m[shoulder_y - int((hip_y - shoulder_y) * 0.20): hip_y + 6] = 255
    shirt_zone = white & (m > 0) & body
    suit = (body & (~skin) & ((Y < 0.40) | shirt_zone) & (m > 0))
    suit = cv2.morphologyEx(suit.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    suit = cv2.dilate(suit, np.ones((11, 11), np.uint8))
    # 不要把脸包进去：头顶以下 18% 以上部分清掉
    suit[:int(y0 + heights * 0.16)] = 0
    info = {'shoulder_y': shoulder_y, 'hip_y': hip_y, 'y0': y0, 'y1': y1}
    return suit, info


def main():
    import torch
    from diffusers import StableDiffusionInpaintPipeline

    t0 = time.time()
    rgb, alpha = frame_rgba()
    mask, info = suit_mask(rgb, alpha)
    ys, xs = np.where(mask > 0)
    print('mask px %d  bbox x[%d..%d] y[%d..%d]' % (len(xs), xs.min(), xs.max(), ys.min(), ys.max()))

    # 裁一块带肩、带上臂的上下文区域，再缩放到 SD 尺寸（8 的倍数）
    pad = 70
    x0 = max(0, xs.min() - pad); x1 = min(rgb.shape[1], xs.max() + pad)
    y0 = max(0, ys.min() - pad - 40); y1 = min(rgb.shape[0], ys.max() + pad)
    crop = rgb[y0:y1, x0:x1]
    mcrop = mask[y0:y1, x0:x1]
    ch, cw = crop.shape[:2]
    scale = 720.0 / ch
    nw, nh = int(round(cw * scale / 8) * 8), 720
    img512 = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA)
    m512 = cv2.resize(mcrop, (nw, nh), interpolation=cv2.INTER_NEAREST)
    print('crop %dx%d -> %dx%d' % (cw, ch, nw, nh))

    Image.fromarray(img512).save(os.path.join(TMP, 'input.png'))
    Image.fromarray(np.dstack([img512, m512])).save(os.path.join(TMP, 'input_mask.png'))

    print('loading model %s ...' % MODEL)
    pipe = StableDiffusionInpaintPipeline.from_pretrained(
        MODEL, torch_dtype=torch.float16, variant='fp16', safety_checker=None,
        requires_safety_checker=False, use_safetensors=True,
        local_files_only=os.path.isdir(MODEL))
    pipe = pipe.to('cuda')
    pipe.set_progress_bar_config(disable=False)
    print('model ready in %.1fs' % (time.time() - t0))

    gen = torch.Generator(device='cuda').manual_seed(20261113)
    res = pipe(
        prompt=PROMPT, negative_prompt=NEGATIVE,
        image=Image.fromarray(img512), mask_image=Image.fromarray(m512),
        height=nh, width=nw, num_inference_steps=30, guidance_scale=7.0,
        generator=gen,
    ).images[0]
    res.save(os.path.join(TMP, 'out512.png'))
    print('inpaint done in %.1fs' % (time.time() - t0))

    # 贴回原画幅：只取重绘的肉身区域，边缘羽化
    big = cv2.resize(np.array(res), (cw, ch), interpolation=cv2.INTER_LANCZOS4)
    out = rgb.astype(np.float32) / 255.0
    region_full = out.copy()
    region_full[y0:y1, x0:x1] = np.clip(big.astype(np.float32) / 255.0, 0, 1)
    m_full = np.zeros(rgb.shape[:2], np.uint8)
    m_full[y0:y1, x0:x1] = mcrop
    m_soft = cv2.GaussianBlur(m_full, (0, 0), 1.6).astype(np.float32) / 255.0
    a = (alpha.astype(np.float32) / 255.0)[..., None]
    comp = out * (1 - m_soft[..., None]) + region_full * m_soft[..., None]
    img = Image.fromarray((np.clip(comp * a + 0.05 * (1 - a), 0, 1) * 255).astype(np.uint8))
    before = Image.fromarray((np.clip(out * a + 0.05 * (1 - a), 0, 1) * 255).astype(np.uint8))

    pair = Image.new('RGB', (img.width * 2 + 20, img.height), (12, 13, 16))
    pair.paste(before, (0, 0))
    pair.paste(img, (img.width + 20, 0))
    pair.thumbnail((1500, 1500))
    p = os.path.join(OUT, '17_生成式换装_单帧验证.png')
    pair.save(p)
    img.thumbnail((700, 1400))
    img.save(os.path.join(TMP, 'after.png'))
    print('saved', p)


if __name__ == '__main__':
    main()
