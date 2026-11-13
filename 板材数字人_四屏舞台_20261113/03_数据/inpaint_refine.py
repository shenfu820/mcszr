# -*- coding: utf-8 -*-
"""两段式换装：先把工装夹克"合成"上去，再用 SD 以低强度重绘一遍把贴图感抹掉。

为什么这么做：SD1.5 inpainting 在"整块换一件衣服"上很容易还原成原样（先验太强），
但它在"保持形体、统一光照/质感"上很擅长。所以先给一件形状/颜色正确的工装，
再让它按照片的光照重画一遍 —— 出来的就是照片级的工装夹克。

运行：
  $env:SD_INPAINT_DIR='...snapshots\\master'
  C:\\Users\\Administrator\\Desktop\\数字人\\.venv-sd\\Scripts\\python.exe 03_数据\\inpaint_refine.py [frames]
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import time

import cv2
import numpy as np
from PIL import Image

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(PKG, '02_四屏舞台', 'assets')
DATA = os.path.join(PKG, '03_数据')
OUT = os.path.join(PKG, '06_验收截图')
TMP = r'C:\Users\Administrator\AppData\Local\Temp\inpaint_work'
os.makedirs(TMP, exist_ok=True)

MODEL = os.environ.get('SD_INPAINT_DIR') or 'runwayml/stable-diffusion-inpainting'
PROMPT = ('photo of a man wearing a navy blue workwear jacket with a light grey yoke panel across '
          'the shoulders and upper chest, two chest pockets with reflective strips, work uniform, '
          'natural fabric folds, same lighting as the photo, photorealistic, sharp fabric detail')
NEGATIVE = ('suit, blazer, lapels, tie, formal wear, illustration, cartoon, painting, deformed, '
            'extra limbs, blurry, plasticky')


def load_fit_module():
    spec = importlib.util.spec_from_file_location(
        'garment_fit', os.path.join(DATA, 'prepare_garment_fit.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_plate():
    p = os.path.join(ASSETS, 'jacket_fit.png')
    img = np.array(Image.open(p).convert('RGBA')).astype(np.float32) / 255.0
    return img


def composite(rgb, plate, exposure=0.95):
    """把照片版贴片按他的局部明暗合成到画面上。"""
    prgb, pa = plate[:, :, :3], plate[:, :, 3]
    Yp = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    blur = cv2.GaussianBlur(Yp, (0, 0), 9.0)
    fold = np.clip(Yp / np.maximum(blur, 1e-4), 0.62, 1.5) ** 0.85
    lit = np.clip(prgb * (exposure * fold)[..., None], 0, 1)
    return rgb * (1 - pa[..., None]) + lit * pa[..., None], pa


def main():
    import torch
    from diffusers import StableDiffusionInpaintPipeline

    fit = load_fit_module()
    rgb_f, alpha_f = fit.person_frame()
    rgb = (np.clip(rgb_f, 0, 1) * 255).astype(np.uint8)
    alpha = alpha_f
    plate = load_plate()
    comp, pa = composite(rgb_f, plate)

    # 遮罩：贴片覆盖区 + 脖子周围一圈（让 SD 自己长出领子，而不是把商品图的立领硬贴上去）
    mk = fit.masks(rgb_f, alpha_f)
    body_m = mk['body']; skin_m = mk['skin']
    y_chin = int(mk['y0'] + (mk['hip_y'] - mk['y0']) * 0.15)
    neck_band = np.zeros(rgb.shape[:2], np.uint8)
    neck_band[y_chin:mk['shoulder_y'] + 14] = 255
    neck_band[~body_m] = 0
    neck_band[skin_m] = 0
    m = (pa > 0.20).astype(np.uint8) * 255
    m = np.maximum(m, neck_band)
    m = cv2.dilate(m, np.ones((15, 15), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    ys, xs = np.where(m > 0)
    pad = 56
    x0 = max(0, xs.min() - pad); x1 = min(rgb.shape[1], xs.max() + pad)
    y0 = max(0, ys.min() - pad - 30); y1 = min(rgb.shape[0], ys.max() + pad)
    crop = (np.clip(comp, 0, 1) * 255).astype(np.uint8)[y0:y1, x0:x1]
    mcrop = m[y0:y1, x0:x1]
    ch, cw = crop.shape[:2]
    scale = 760.0 / ch
    nw, nh = int(round(cw * scale / 8) * 8), 760

    img_sd = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA)
    m_sd = cv2.resize(mcrop, (nw, nh), interpolation=cv2.INTER_NEAREST)
    Image.fromarray(img_sd).save(os.path.join(TMP, 'refine_in.png'))
    Image.fromarray(m_sd).save(os.path.join(TMP, 'refine_mask.png'))

    print('loading model ...')
    t0 = time.time()
    pipe = StableDiffusionInpaintPipeline.from_pretrained(
        MODEL, torch_dtype=torch.float16, variant='fp16', safety_checker=None,
        requires_safety_checker=False, use_safetensors=True,
        local_files_only=os.path.isdir(MODEL))
    pipe = pipe.to('cuda')
    print('ready in %.1fs' % (time.time() - t0))

    strength = float(os.environ.get('SD_STRENGTH', '0.42'))
    res = pipe(
        prompt=PROMPT, negative_prompt=NEGATIVE,
        image=Image.fromarray(img_sd), mask_image=Image.fromarray(m_sd),
        height=nh, width=nw, num_inference_steps=40, strength=strength,
        guidance_scale=8.0,
        generator=torch.Generator(device='cuda').manual_seed(20261113),
    ).images[0]
    print('refined in %.1fs (strength=%.2f)' % (time.time() - t0, strength))
    res.save(os.path.join(TMP, 'refine_out.png'))

    big = cv2.resize(np.array(res), (cw, ch), interpolation=cv2.INTER_LANCZOS4)
    out = (np.clip(comp, 0, 1) * 255).astype(np.uint8).astype(np.float32) / 255.0
    region = out.copy()
    region[y0:y1, x0:x1] = np.clip(big.astype(np.float32) / 255.0, 0, 1)
    m_full = np.zeros(rgb.shape[:2], np.uint8)
    m_full[y0:y1, x0:x1] = mcrop
    m_soft = cv2.GaussianBlur(m_full, (0, 0), 2.0).astype(np.float32) / 255.0
    final = out * (1 - m_soft[..., None]) + region * m_soft[..., None]
    a = alpha[..., None]
    after = Image.fromarray((np.clip(final * a + 0.05 * (1 - a), 0, 1) * 255).astype(np.uint8))
    before = Image.fromarray((np.clip(rgb_f * a + 0.05 * (1 - a), 0, 1) * 255).astype(np.uint8))
    mid = Image.fromarray((np.clip(comp * a + 0.05 * (1 - a), 0, 1) * 255).astype(np.uint8))

    trio = Image.new('RGB', (before.width * 3 + 40, before.height), (12, 13, 16))
    trio.paste(before, (0, 0))
    trio.paste(mid, (before.width + 20, 0))
    trio.paste(after, (before.width * 2 + 40, 0))
    trio.thumbnail((1800, 1800))
    p = os.path.join(OUT, '18_生成式换装_合成+重绘.png')
    trio.save(p)
    print('saved', p)
    after.save(os.path.join(TMP, 'after_frame.png'))

    # 记录参数，便于复现
    meta = {'model': MODEL, 'prompt': PROMPT, 'negative': NEGATIVE,
            'strength': strength, 'steps': 40, 'guidance': 8.0, 'seed': 20261113,
            'mask_px': int((mcrop > 0).sum()), 'crop': [int(x0), int(y0), int(cw), int(ch)],
            'when': time.strftime('%Y-%m-%d %H:%M:%S')}
    json.dump(meta, open(os.path.join(DATA, 'inpaint_meta.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
