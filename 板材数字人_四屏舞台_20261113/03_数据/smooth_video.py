# -*- coding: utf-8 -*-
"""给逐帧重绘结果做时间平滑，压掉帧间闪烁（不动人脸/手：只在衣服区域混）。

做法：对 PNG 序列做一阶 EMA（当前帧 0.65 + 上一输出 0.35），只在衣服遮罩内生效，
      然后重新编码成 alpha 视频。指标用"相邻帧衣服区的平均绝对差"来验证有没有降。

用法：
  python 03_数据\smooth_video.py --video idle [--alpha 0.35]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import cv2
import numpy as np

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H3 = os.path.join(PKG, '01_人物素材_复用', 'H3')
DATA = os.path.join(PKG, '03_数据')
TMP = r'C:\Users\Administrator\AppData\Local\Temp\inpaint_batch'


def torso_mask(rgb, alpha):
    R, G, B = [rgb[..., i].astype(np.float32) / 255.0 for i in range(3)]
    Y = 0.2126 * R + 0.7152 * G + 0.0722 * B
    body = alpha > 0.7
    skin = ((R - B) > 0.06) & (R > 0.16)
    return (body & (~skin)).astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--video', default='idle')
    ap.add_argument('--alpha', type=float, default=0.65, help='当前帧权重，越大越不平滑')
    ap.add_argument('--tag', default='work')
    args = ap.parse_args()

    src_dir = os.path.join(TMP, args.video)
    files = sorted(f for f in os.listdir(src_dir) if f.startswith('f_') and f.endswith('.png'))
    if not files:
        raise SystemExit('没有帧：' + src_dir)
    print('%s: %d 帧' % (args.video, len(files)))

    prev_out = None
    diffs_before, diffs_after = [], []
    prev_raw = None
    for i, f in enumerate(files):
        img = cv2.imread(os.path.join(src_dir, f))
        h, w = img.shape[:2]
        half = w // 2
        cur = img[:, :half].astype(np.float32)
        alpha = img[:, half:, 0]
        rgb = cv2.cvtColor(img[:, :half], cv2.COLOR_BGR2RGB)
        m = torso_mask(rgb, alpha)[..., None]

        if prev_raw is not None:
            diffs_before.append(float(np.abs(cur - prev_raw)[m[:, :, 0] > 0.5].mean()))
        prev_raw = cur.copy()

        if prev_out is None:
            out = cur
        else:
            out = cur * args.alpha + prev_out * (1 - args.alpha)
            out = cur * (1 - m) + out * m          # 只在衣服区平滑
        if prev_out is not None:
            diffs_after.append(float(np.abs(out - prev_out)[m[:, :, 0] > 0.5].mean()))
        prev_out = out.copy()

        merged = np.zeros_like(img)
        merged[:, :half] = np.clip(out, 0, 255).astype(np.uint8)
        merged[:, half:] = np.dstack([alpha, alpha, alpha])
        cv2.imwrite(os.path.join(src_dir, 's_%05d.png' % i), merged)
        if i % 60 == 0:
            print('  smooth %d/%d' % (i + 1, len(files)), flush=True)

    b = float(np.mean(diffs_before)) if diffs_before else 0.0
    a = float(np.mean(diffs_after)) if diffs_after else 0.0
    print('衣服区相邻帧平均绝对差：平滑前 %.3f → 平滑后 %.3f（↓%.0f%%）' % (
        b, a, (1 - a / max(b, 1e-6)) * 100))

    cap = cv2.VideoCapture(os.path.join(H3, '%s_rgb_alpha.mp4' % args.video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    cap.release()
    dst = os.path.join(H3, '%s_rgb_alpha_%ss.mp4' % (args.video, args.tag))
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [ff, '-y', '-framerate', '%.6f' % fps, '-i', os.path.join(src_dir, 's_%05d.png'),
               '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '17', '-preset', 'medium', dst]
        subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    except Exception as e:                        # noqa: BLE001
        raise SystemExit('ffmpeg 不可用：%s' % e)
    print('smooth video ->', dst, '%.2f MB' % (os.path.getsize(dst) / 1048576))
    json.dump({'video': args.video, 'frames': len(files), 'alpha': args.alpha,
               'diff_before': round(b, 3), 'diff_after': round(a, 3), 'out': dst},
              open(os.path.join(DATA, 'smooth_%s.json' % args.video), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
