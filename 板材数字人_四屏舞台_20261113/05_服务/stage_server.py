# -*- coding: utf-8 -*-
"""四屏舞台本地服务（标准库 only，默认 127.0.0.1:8790）。

做四件事：
  1. 静态文件（支持 Range，mp4 才能顺畅循环/拖动）；
  2. GET /api/stage       —— 现场实时数据（只读 new mcjc 库 + 探 8770/8999 通道）；
  3. GET /api/win/<key>.jpg —— 真实桌面窗口抓帧（打印窗口，被遮挡也行）；
  4. GET /api/systems     —— 三套真实系统的在线状态。

**只读**：全程不写产线库、不改 settings.json、不碰 8770 / 8999。
端口 8790 是新增的，与 8770（大屏）/8999（对话中枢）无关。

运行：E:\\conda3\\envs\\yoloces\\python.exe 05_服务\\stage_server.py --port 8790
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import mimetypes
import os
import posixpath
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILDER = os.path.join(PKG, '03_数据', 'build_snapshot.py')
INDEX = '/02_四屏舞台/index.html'

mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('text/css', '.css')

_lock = threading.Lock()
_cache = {'ts': 0.0, 'data': None}
CACHE_TTL = 3.0

# 三套真实系统的地址（与 settings.json 的端口一致，只探活，不改它们）
SCREEN_URL = 'http://127.0.0.1:8770'
AGENT_URL = 'http://127.0.0.1:8999'
WIN_TITLE = '缺陷智能检测系统'          # run.py 窗口标题里的稳定片段

FEED = None


def get_feed():
    global FEED
    if FEED is None:
        from wincap import WindowFeed        # 同目录
        FEED = WindowFeed(WIN_TITLE, min_interval=0.18)
    return FEED


def probe(url, timeout=1.5):
    try:
        with urllib.request.urlopen(url + '/api/health', timeout=timeout) as r:
            doc = json.loads(r.read().decode('utf-8'))
        return {'online': True, 'pid': (doc.get('data') or {}).get('pid')}
    except Exception as e:                   # noqa: BLE001
        return {'online': False, 'error': '%s: %s' % (type(e).__name__, e)}


def load_builder():
    spec = importlib.util.spec_from_file_location('stage_builder', BUILDER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BUILDER_MOD = None


def stage_payload():
    global BUILDER_MOD
    now = time.time()
    with _lock:
        if _cache['data'] is not None and now - _cache['ts'] < CACHE_TTL:
            return _cache['data']
    if BUILDER_MOD is None:
        BUILDER_MOD = load_builder()
    data = BUILDER_MOD.compute_snapshot(make_preview=False)
    with _lock:
        _cache['ts'] = time.time()
        _cache['data'] = data
    return data


class Handler(BaseHTTPRequestHandler):
    server_version = 'bancai-stage/1.0'

    # ---------- 工具 ----------
    def _send(self, code, body=b'', ctype='text/plain; charset=utf-8', extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store' if ctype.startswith('application/json') else 'max-age=60')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self._send(code, body, 'application/json; charset=utf-8')

    def _resolve(self, path):
        path = urllib.parse.unquote(path.split('?', 1)[0])
        path = posixpath.normpath(path)
        if path in ('/', ''):
            return None
        rel = path.lstrip('/').replace('/', os.sep)
        full = os.path.abspath(os.path.join(PKG, rel))
        if not full.startswith(os.path.abspath(PKG)):
            return False
        return full

    def _serve_file(self, full):
        if not os.path.isfile(full):
            self._send(404, b'not found')
            return
        size = os.path.getsize(full)
        ctype = mimetypes.guess_type(full)[0] or 'application/octet-stream'
        rng = self.headers.get('Range')
        if rng and rng.startswith('bytes='):
            try:
                s, _, e = rng[6:].partition('-')
                start = int(s) if s else 0
                end = int(e) if e else size - 1
            except ValueError:
                start, end = 0, size - 1
            start = max(0, min(start, size - 1))
            end = max(start, min(end, size - 1))
            with open(full, 'rb') as f:
                f.seek(start)
                chunk = f.read(end - start + 1)
            self.send_response(206)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(chunk)))
            self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
            self.send_header('Accept-Ranges', 'bytes')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(chunk)
            return
        with open(full, 'rb') as f:
            body = f.read()
        self._send(200, body, ctype, {'Accept-Ranges': 'bytes'})

    # ---------- 路由 ----------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        try:
            if path == '/api/health':
                self._json({'code': 200, 'msg': 'ok', 'data': {
                    'service': 'bancai-stage', 'version': '1.0.0', 'pid': os.getpid(),
                    'package': PKG,
                    'index': INDEX,
                    'time': time.strftime('%Y-%m-%d %H:%M:%S'),
                }})
                return
            if path == '/api/stage':
                self._json({'code': 200, 'msg': 'ok', 'data': stage_payload()})
                return
            if path.startswith('/api/win/'):
                key = path[len('/api/win/'):]
                if not key.endswith('.jpg'):
                    self._json({'code': 400, 'msg': 'use /api/win/<key>.jpg'}, 400)
                    return
                key = key[:-4]
                if key != 'desktop':
                    self._json({'code': 404, 'msg': 'unknown window key'}, 404)
                    return
                jpeg, meta = get_feed().frame()
                if jpeg is None:
                    self._json({'code': 503, 'msg': 'window not found',
                                'data': {'title_part': WIN_TITLE, 'meta': meta}}, 503)
                    return
                self._send(200, jpeg, 'image/jpeg', {'Cache-Control': 'no-store, max-age=0'})
                return
            if path == '/api/systems':
                screen = probe(SCREEN_URL)
                agent = probe(AGENT_URL)
                meta = get_feed().frame()[1]
                self._json({'code': 200, 'msg': 'ok', 'data': {
                    'desktop': {'online': bool(meta.get('ok')), 'title': meta.get('title', ''),
                                'pid': meta.get('pid'), 'window': WIN_TITLE,
                                'capture_at': time.strftime('%H:%M:%S', time.localtime(meta['at']))
                                if meta.get('at') else None},
                    'screen': dict(screen, url=SCREEN_URL),
                    'agent': dict(agent, url=AGENT_URL),
                    'at': time.strftime('%Y-%m-%d %H:%M:%S'),
                }})
                return
            full = self._resolve(path)
            if full is None:
                q = urllib.parse.urlsplit(self.path).query      # 保留 ?outfit= / ?still= 这类参数
                loc = urllib.parse.quote(INDEX) + (('?' + q) if q else '')
                self.send_response(302)
                self.send_header('Location', loc)
                self.end_headers()
                return
            if full is False:
                self._send(403, b'forbidden')
                return
            if os.path.isdir(full):
                self._serve_file(os.path.join(full, 'index.html'))
            else:
                self._serve_file(full)
        except BrokenPipeError:
            pass
        except Exception as e:                   # noqa: BLE001
            self._send(500, ('server error: %s: %s' % (type(e).__name__, e)).encode('utf-8'))

    def log_message(self, fmt, *args):
        sys.stdout.write('[%s] %s\n' % (time.strftime('%H:%M:%S'), fmt % args))
        sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8790)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--check', action='store_true', help='只做一次数据自检，不起服务')
    args = ap.parse_args()

    if args.check:
        d = stage_payload()
        print(json.dumps({
            'generated_at': d['generated_at'],
            'inspect': {k: d['inspect'][k] for k in ('total', 'sessions', 'sessions_total', 'avg_conf', 'max_conf')},
            'screen': {k: d['screen'][k] for k in ('sessions', 'frames', 'defect_frames', 'defects',
                                                   'nodes_online', 'nodes_total')},
            'review': {k: d['review'][k] for k in ('total', 'needs_human', 'pending', 'avg_agree')},
            'selflearn': {'samples': d['selflearn']['samples'],
                          'current': (d['selflearn'].get('current') or {}).get('name')},
        }, ensure_ascii=False, indent=2))
        return

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print('bancai stage  ->  http://%s:%d%s' % (args.host, args.port, INDEX))
    print('api /api/stage  /api/health   (package: %s)' % PKG)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == '__main__':
    main()
