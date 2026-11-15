# -*- coding: utf-8 -*-
"""数字人 · 一键启动全部（三个屏 + 舞台 + 主服务，纯标准库）

五块屏背后到底要哪几个进程，这个脚本把它们收成一条命令：

    ① 检测系统     <PROD_ROOT>\\run.py                    （PyQt5 桌面端，舞台按窗口标题抓帧）
    ② 大屏         <PROD_ROOT>\\screen_service.py    8770
    ③ 六专家复核   <PROD_ROOT>\\..\\angent\\agent_service.py  8999
    ④⑤ 数据/性能   板材数字人_四屏舞台_20261113\\05_服务\\stage_server.py  8790
    形象 + 控制台  server\\dh_server.py               8772（页面 + /api/stage 转发）

产线工程（new mcjc / angent）不在本仓库里：按 config\\paths.ini 的 PROD_ROOT 找。
找不到就跳过并说清怎么补 —— 舞台照样能起，①②③ 会各自显示「未连接」。

用法：
    python _tools\\dh_all.py --check          只体检：每个服务从哪来、在不在跑
    python _tools\\dh_all.py                 起全部（已在跑的直接跳过）
    python _tools\\dh_all.py --no-window     不打开浏览器窗口
    python _tools\\dh_all.py --only 8770,8790
    python _tools\\dh_all.py --fix-bats      先修一遍 .bat 行尾（LF 的 bat 会被 cmd 拆碎）
"""
from __future__ import annotations

import argparse
import ctypes
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "_tools"
sys.path.insert(0, str(TOOLS))
import dh_env  # noqa: E402

IS_WIN = os.name == "nt"
STAGE_PKG = ROOT / "板材数字人_四屏舞台_20261113"


# ------------------------------------------------------------------ 小工具
def port_open(port, host="127.0.0.1") -> bool:
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex((host, int(port))) == 0
    finally:
        s.close()


def http_ok(url, expect="", timeout=2.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
        if expect and expect not in body:
            return False, "HTTP %s 但正文没有 %r" % (r.status, expect)
        return True, "HTTP %s" % r.status
    except Exception as e:                        # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, e)


def _win_titles(part: str) -> list:
    """按标题片段找顶层窗口（找 run.py 的桌面端用，不依赖 Pillow）。"""
    if not IS_WIN:
        return []
    out = []
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if part and part in buf.value:
                out.append(buf.value)
        return True

    user32.EnumWindows(CB(cb), None)
    return out


def spawn(name: str, py: str, script: str, cwd: str, args: list) -> tuple:
    """后台起一个进程，日志写 logs\\<name>.log，返回 (ok, pid/说明)。"""
    logs = ROOT / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    if not Path(py).is_file():
        return False, "解释器不存在：%s" % py
    if not Path(script).is_file():
        return False, "脚本不存在：%s" % script
    log = logs / ("%s.log" % name)
    argv = [py, script] + [str(a) for a in args]
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    flags = 0
    if IS_WIN:
        flags = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    with open(log, "ab") as fh:
        fh.write(("\n===== %s start %s =====\n"
                  % (time.strftime("%Y-%m-%d %H:%M:%S"), " ".join(argv))).encode("utf-8"))
        p = subprocess.Popen(argv, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True, env=env)
    (logs / ("%s.pid" % name)).write_text(str(p.pid), encoding="utf-8")
    time.sleep(2.5)
    rc = p.poll()
    if rc is not None:
        tail = ""
        try:
            tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-10:])
        except Exception:                          # noqa: BLE001
            pass
        return False, "起来就退了（退出码 %s），日志尾：\n%s\n（完整日志 %s）" % (rc, tail, log)
    return True, "pid %s" % p.pid


def stop_all(services: list) -> int:
    """按端口收尸（桌面端按启动时记下的 pid）。只停本脚本/启动脚本起的那几个端口。"""
    print("停止全部服务：")
    out = ""
    if IS_WIN:
        out = subprocess.run(["netstat", "-ano"], capture_output=True).stdout.decode("utf-8", "replace")
    for svc in services:
        port = svc.get("port")
        if port:
            pids = set()
            for line in out.splitlines():
                parts = line.split()
                if (len(parts) >= 5 and parts[0].upper().startswith("TCP")
                        and parts[3].upper() == "LISTENING" and parts[1].endswith(":%s" % port)):
                    pids.add(parts[4])
            if not pids:
                print("  %-12s %-6s 没在跑" % (svc["title"], port))
                continue
            for pid in pids:
                subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
                print("  %-12s %-6s 已结束 PID %s" % (svc["title"], port, pid))
    desk = [s for s in services if s.get("window")]
    for svc in desk:
        pidf = ROOT / "logs" / ("all_%s.pid" % svc["key"])
        if not pidf.is_file():
            print("  %-12s 没有 pid 记录，跳过（要关就手动关那个窗口）" % svc["title"])
            continue
        pid = pidf.read_text(encoding="utf-8").strip()
        rc = subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True).returncode
        print("  %-12s PID %s %s" % (svc["title"], pid, "已结束" if rc == 0 else "已经不在了"))
    return 0


# ------------------------------------------------------------------ 服务表
def build_services(env: dict) -> list:
    prod = env.get("DH_PROD") or ""
    angent = str((Path(prod).parent / "angent") if prod else "")
    cn = lambda c: str(c) if c else ""             # noqa: E731
    return [
        {
            "key": "screen", "title": "② 大屏", "port": 8770,
            "health": "http://127.0.0.1:8770/api/health", "expect": "",
            "script": cn(Path(prod) / "screen_service.py") if prod else "",
            "cwd": prod, "py": "gpu", "args": lambda p: ["--port", str(p)],
            "optional": True,
            "hint": "本机没有产线：把 new mcjc 放到 config\\paths.ini 的 PROD_ROOT 指的位置，"
                    "或把 SCREEN_URL 指到产线机（屏上会显示「未连接」，不影响其它屏）",
        },
        {
            "key": "agent", "title": "③ 对话中枢", "port": 8999,
            "health": "http://127.0.0.1:8999/api/health", "expect": "",
            "script": cn(Path(angent) / "agent_service.py") if angent else "",
            "cwd": angent, "py": "main", "args": lambda p: ["--port", str(p), "--no-open"],
            "optional": True,
            "hint": "本机没有 angent：它就在 new mcjc 旁边（…\\aa比赛项目\\angent），"
                    "或把 AGENT_URL 指到产线机",
        },
        {
            "key": "desktop", "title": "① 检测系统", "port": None,
            "health": "", "expect": "", "window": "缺陷智能检测系统",
            "script": cn(Path(prod) / "run.py") if prod else "",
            "cwd": prod, "py": "gpu", "args": lambda p: [],
            "optional": True,
            "hint": "本机没有产线桌面端：装好 new mcjc 并把 PROD_ROOT 指过去；"
                    "没有它 ① 屏显示「未检测到窗口」",
        },
        {
            "key": "stage", "title": "舞台", "port": 8790,
            "health": "http://127.0.0.1:8790/api/health", "expect": "bancai-stage",
            "script": str(STAGE_PKG / "05_服务" / "stage_server.py"),
            "cwd": str(STAGE_PKG), "py": "main", "args": lambda p: ["--port", str(p)],
            "optional": False,
            "hint": "舞台包缺文件：仓库里 板材数字人_四屏舞台_20261113\\05_服务\\stage_server.py 不见了",
        },
        {
            "key": "server", "title": "数字人主服务", "port": int(env.get("PORT") or 8772),
            "health": "http://127.0.0.1:%s/api/health" % (env.get("PORT") or 8772),
            "expect": "digital-human",
            "script": str(ROOT / "server" / "dh_server.py"),
            "cwd": str(ROOT), "py": "main", "args": lambda p: ["--port", str(p)],
            "optional": False,
            "hint": "缺 server\\dh_server.py：仓库不完整",
        },
    ]


def pick_py(kind: str, env: dict) -> str:
    if kind == "gpu" and env.get("DH_GPU_PY"):
        return env["DH_GPU_PY"]
    return env.get("DH_PY") or sys.executable


def status_of(svc: dict) -> str:
    if svc.get("port"):
        if port_open(svc["port"]):
            return "running"
    elif svc.get("window"):
        if _win_titles(svc["window"]):
            return "running"
    return "stopped"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只体检，不起服务")
    ap.add_argument("--no-window", action="store_true", help="不打开浏览器窗口")
    ap.add_argument("--no-wait", action="store_true", help="起完不等健康检查")
    ap.add_argument("--only", default="", help="只起这些端口，逗号分隔")
    ap.add_argument("--stop", action="store_true", help="停止全部（8770/8999/8790/8772 + 桌面端）")
    ap.add_argument("--fix-bats", action="store_true", help="先修 .bat 行尾（LF -> CRLF）")
    a = ap.parse_args()

    if a.fix_bats:
        try:
            import bat_lint
            fixed = bat_lint.fix_all(ROOT)
            print("[bat] 行尾修复：%s" % ("修了 %d 个" % fixed if fixed else "都是好的"))
        except Exception as e:                     # noqa: BLE001
            print("[bat] 跳过修复：%s: %s" % (type(e).__name__, e))

    cfg = dh_env.read_ini(ROOT / dh_env.CONFIG_REL)
    env = dh_env.resolve(ROOT, cfg)
    services = build_services(env)

    if a.stop:
        return stop_all(services)
    only = {int(x) for x in a.only.split(",") if x.strip().isdigit()} if a.only else set()

    print("=" * 78)
    print("数字人 · 一键启动全部   项目根 %s" % ROOT)
    print("产线目录 %s" % (env.get("DH_PROD") or "（没找到：①②③ 会显示未连接）"))
    print("=" * 78)

    warn, fail, started = [], [], []
    for svc in services:
        port = svc.get("port")
        if only and port not in only:
            continue
        py = pick_py(svc["py"], env)
        state = status_of(svc)
        ok_script = bool(svc["script"]) and Path(svc["script"]).is_file()

        line = " %-12s %-6s %-8s" % (svc["title"], (str(port) if port else "-"),
                                     "已在跑" if state == "running" else "未启动")
        if a.check:
            if state == "running":
                print(line + "  " + ("脚本在" if ok_script else "（脚本不在，但已经有进程在跑）"))
            elif not ok_script:
                print(line + "  缺工程：" + svc["hint"])
            else:
                print(line + "  %s  %s" % (py, svc["script"]))
            continue

        if state == "running":
            print(line + "  跳过")
            continue
        if not ok_script:
            print(line + "  跳过 —— " + svc["hint"])
            (warn if svc["optional"] else fail).append(svc["title"])
            continue

        print(line + "  启动 %s" % svc["script"])
        ok, msg = spawn("all_" + svc["key"], py, svc["script"], svc["cwd"] or str(ROOT),
                        svc["args"](port) if port else [])
        if not ok:
            print("      [失败] " + msg)
            (warn if svc["optional"] else fail).append(svc["title"])
            continue
        print("      " + msg)
        started.append(svc)

        if svc["health"] and not a.no_wait:
            t0 = time.time()
            while time.time() - t0 < 40:
                good, detail = http_ok(svc["health"], svc["expect"])
                if good:
                    print("      就绪（%.1fs）%s" % (time.time() - t0, svc["health"]))
                    break
                time.sleep(1.0)
            else:
                print("      [警告] 40 秒没就绪：%s（看 logs\\all_%s.log）" % (detail, svc["key"]))
                warn.append(svc["title"] + "（健康检查超时）")

    if a.check:
        print("-" * 78)
        print("体检完：加 --only 8770,8790 可以只起某几个；不带参数就是起全部。")
        return 0

    # ---- 开窗口
    main_port = int(env.get("PORT") or 8772)
    if not a.no_window:
        browser = env.get("DH_BROWSER") or ""
        urls = ["http://127.0.0.1:%d/xx/xiaoxing5.html" % main_port,
                "http://127.0.0.1:%d/console/" % main_port]
        if port_open(main_port):
            for u in urls:
                try:
                    if browser:
                        subprocess.Popen([browser, "--app=" + u, "--window-size=1400,900",
                                          "--no-first-run", "--use-fake-ui-for-media-stream",
                                          "--autoplay-policy=no-user-gesture-required"],
                                         creationflags=0x00000008 if IS_WIN else 0)
                    else:
                        os.startfile(u)               # noqa: S606  (Windows 专用)
                except Exception as e:                # noqa: BLE001
                    print("[窗口] 打开失败 %s：%s" % (u, e))
        else:
            print("[窗口] 主服务 %d 没在监听，先别开窗口" % main_port)

    print("-" * 78)
    print("五块屏   http://127.0.0.1:%d/xx/xiaoxing5.html" % main_port)
    print("舞台首页 http://127.0.0.1:8790/")
    print("控制台   http://127.0.0.1:%d/console/" % main_port)
    print("停止全部 python _tools\\dh_all.py --stop   （等价 停止数字人.bat + 关舞台）")
    if warn:
        print("[WARN] 降级运行：%s" % "；".join(warn))
    if fail:
        print("[FAIL] 起不来：%s" % "；".join(fail))
    print("=" * 78)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
