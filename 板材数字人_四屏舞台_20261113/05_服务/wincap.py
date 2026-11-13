# -*- coding: utf-8 -*-
"""窗口实时抓取（Windows / GDI PrintWindow）——给四屏舞台的"检测系统"那块屏用。

为什么要它：主系统是 PyQt5 桌面程序，浏览器没法 iframe 一个原生窗口。
这里用 PrintWindow(PW_RENDERFULLCONTENT) 把窗口位图抓下来，编成 JPEG 给前端 <img> 轮询，
于是那块屏放的是**真正的 run.py 窗口**，不是重画的仪表盘。

被遮挡也能抓（PrintWindow 走的是窗口自己的绘制，不是截屏幕）。
找不到窗口时返回 None，前端显示"未启动"，不伪造画面。
"""
from __future__ import annotations

import ctypes
import io
import threading
import time
from ctypes import wintypes

from PIL import Image

user32 = ctypes.WinDLL('user32', use_last_error=True)
gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)

PW_RENDERFULLCONTENT = 0x00000002
SRCCOPY = 0x00CC0020

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG), ('biHeight', wintypes.LONG),
        ('biPlanes', wintypes.WORD), ('biBitCount', wintypes.WORD),
        ('biCompression', wintypes.DWORD), ('biSizeImage', wintypes.DWORD),
        ('biXPelsPerMeter', wintypes.LONG), ('biYPelsPerMeter', wintypes.LONG),
        ('biClrUsed', wintypes.DWORD), ('biClrImportant', wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [('bmiHeader', BITMAPINFOHEADER), ('bmiColors', wintypes.DWORD * 3)]


def list_windows():
    """返回 [(hwnd, title, pid)]，只要可见且有标题的顶层窗口。"""
    out = []

    def cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            out.append((hwnd, buf.value, pid.value))
        except Exception:                        # noqa: BLE001
            pass
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def find_window(title_part: str, min_width: int = 600):
    """按标题子串找窗口。

    注意：最小化时 Windows 会把主窗口报成一个很小的矩形（甚至 51×68），这时按尺寸筛会漏掉，
    所以先找正常尺寸的，找不到再退回"被最小化但标题对得上"的那个；grab() 会无焦还原它。
    """
    normal, iconic = [], []
    for hwnd, title, pid in list_windows():
        if title_part not in title:
            continue
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            continue
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        try:
            iso = bool(user32.IsIconic(hwnd))
        except Exception:                        # noqa: BLE001
            iso = False
        if iso:
            iconic.append((hwnd, title, pid))
        elif w >= min_width and h >= 200:
            normal.append((hwnd, title, pid, w * h))
    if normal:
        best = max(normal, key=lambda m: m[3])
        return best[0], best[1], best[2]
    if iconic:
        return iconic[0]
    return None


def grab(hwnd, max_width=1100, quality=78):
    """抓窗口位图 -> JPEG bytes；失败返回 None。"""
    # 最小化的窗口不渲染，PrintWindow 会拿到空白：先无焦还原
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 4)          # SW_SHOWNOACTIVATE：还原但不抢焦点
            time.sleep(0.12)
    except Exception:                            # noqa: BLE001
        pass
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0 or w > 8000 or h > 8000:
        return None

    hdc = user32.GetWindowDC(hwnd)
    if not hdc:
        return None
    mfc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mfc, bmp)
    try:
        ok = user32.PrintWindow(hwnd, mfc, PW_RENDERFULLCONTENT)
        if not ok:
            gdi32.BitBlt(mfc, 0, 0, w, h, hdc, 0, 0, SRCCOPY)

        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h          # 负数 = top-down
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0      # BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        got = gdi32.GetDIBits(mfc, bmp, 0, h, buf, ctypes.byref(bi), 0)
        if not got:
            return None
        img = Image.frombuffer('RGB', (w, h), buf, 'raw', 'BGRX', 0, 1)
        if w > max_width:
            nh = max(1, int(h * max_width / w))
            img = img.resize((max_width, nh), Image.BILINEAR)
        out = io.BytesIO()
        img.save(out, 'JPEG', quality=quality)
        return out.getvalue()
    finally:
        try:
            gdi32.SelectObject(mfc, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mfc)
        except Exception:                        # noqa: BLE001
            pass
        user32.ReleaseDC(hwnd, hdc)


class WindowFeed:
    """带缓存的窗口抓取器：前台多快轮询，后端最多按 min_interval 抓一次。"""

    def __init__(self, title_part: str, min_interval=0.18):
        self.title_part = title_part
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0
        self._jpeg = None
        self._meta = {'ok': False, 'title': '', 'pid': None, 'at': None}

    def frame(self):
        with self._lock:
            now = time.time()
            if self._jpeg is not None and now - self._last < self.min_interval:
                return self._jpeg, self._meta
            hit = find_window(self.title_part)
            if not hit:
                self._jpeg = None
                self._meta = {'ok': False, 'title': '', 'pid': None, 'at': now}
                self._last = now
                return None, self._meta
            hwnd, title, pid = hit
            jpeg = grab(hwnd)
            self._last = time.time()
            if jpeg:
                self._jpeg = jpeg
                self._meta = {'ok': True, 'title': title, 'pid': pid,
                              'bytes': len(jpeg), 'at': self._last}
            else:
                self._meta = {'ok': False, 'title': title, 'pid': pid,
                              'error': 'PrintWindow 失败', 'at': self._last}
                self._jpeg = None
            return self._jpeg, self._meta

    def meta(self):
        return self._meta
