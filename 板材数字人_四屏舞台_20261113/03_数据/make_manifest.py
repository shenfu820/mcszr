# -*- coding: utf-8 -*-
"""生成 校验清单.json：全包文件清单 + SHA256（跑在包根目录下的任意位置均可）。"""
import hashlib
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {'__pycache__', '.git'}
SKIP_FILES = {'校验清单.json'}


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
    items = []
    total = 0
    for root, dirs, files in os.walk(PKG):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in sorted(files):
            if f in SKIP_FILES:
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, PKG).replace('\\', '/')
            size = os.path.getsize(p)
            total += size
            items.append({'path': rel, 'bytes': size, 'sha256': sha256(p)})
    manifest = {
        'package': os.path.basename(PKG),
        'files': len(items),
        'total_bytes': total,
        'note': '本表为本包内文件的实测尺寸与 SHA256；人物素材的复制来源见 '
                '01_人物素材_复用/来源与SHA256.json',
        'items': items,
    }
    p = os.path.join(PKG, '校验清单.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print('files=%d total=%.2f MB -> %s' % (len(items), total / 1024 / 1024, p))


if __name__ == '__main__':
    main()
