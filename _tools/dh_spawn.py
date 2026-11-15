# -*- coding: utf-8 -*-
"""
数字人 · 后台起服务 / 等就绪 / 按端口收尸
==========================================
为什么不用 `start "" cmd /c ""py" "x.py""`：那串三层引号只要换台机器、
路径里多一个空格或中文，cmd 就会把它拆成 "voice" / "+" 这种碎片当命令去执行，
报的错跟真正原因（找不到解释器）完全不相干。这里用 Python 起进程，
参数是列表不是字符串，没有引号可拆。

    python _tools/dh_spawn.py start --name asr --py <python.exe> --script server/asr_service.py --port 8773
    python _tools/dh_spawn.py start --name server --py <python.exe> --script server/dh_server.py --port 8772
    python _tools/dh_spawn.py wait  --url http://127.0.0.1:8772/api/health --timeout 25
    python _tools/dh_spawn.py stop  --port 8772
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

IS_WIN = os.name == "nt"
ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex((host, int(port))) == 0
    finally:
        s.close()


def cmd_start(a) -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    if port_open(a.port):
        print(f"[spawn] {a.name}: 端口 {a.port} 已在监听，跳过")
        return 0
    py = Path(a.py)
    script = Path(a.script)
    if not py.is_file():
        print(f"[spawn] {a.name}: 解释器不存在 {py}", file=sys.stderr)
        return 2
    if not script.is_file():
        print(f"[spawn] {a.name}: 脚本不存在 {script}", file=sys.stderr)
        return 2
    log = LOGS / f"{a.name}.log"
    argv = [str(py), str(script), "--port", str(a.port)]
    argv += a.extra
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    flags = 0
    if IS_WIN:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    with open(log, "ab") as fh:
        fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start {a.name} "
                 f"port {a.port} =====\n".encode("utf-8"))
        p = subprocess.Popen(argv, cwd=str(a.cwd or ROOT), stdout=fh, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True, env=env)
    (LOGS / f"{a.name}.pid").write_text(str(p.pid), encoding="utf-8")
    print(f"[spawn] {a.name}: pid {p.pid}，日志 {log}")

    # 起完等一拍：立刻死掉的（解释器不对 / 端口被占 / 依赖缺失）当场报出来，
    # 别等用户以为「在跑」而实际什么也没有。
    time.sleep(2.5)
    rc = p.poll()
    if rc is not None:
        tail = ""
        try:
            with open(log, "r", encoding="utf-8", errors="replace") as fh:
                tail = "\n".join(fh.read().splitlines()[-12:])
        except Exception:
            pass
        print(f"[spawn] {a.name}: 起来就退了（退出码 {rc}），日志尾：\n{tail}", file=sys.stderr)
        return 1
    return 0


def cmd_wait(a) -> int:
    t0 = time.time()
    last = ""
    while time.time() - t0 < a.timeout:
        try:
            with urllib.request.urlopen(a.url, timeout=2) as r:
                body = r.read().decode("utf-8", "replace")
            if a.expect and a.expect not in body:
                last = f"HTTP {r.status} 但正文里没有 {a.expect!r}"
            else:
                print(f"[spawn] {a.name}: 就绪（{round(time.time() - t0, 1)}s）{a.url}")
                return 0
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(a.interval)
    print(f"[spawn] {a.name}: {a.timeout}s 内没就绪 -> {last}", file=sys.stderr)
    return 1


def cmd_stop(a) -> int:
    if not IS_WIN:
        print("[spawn] 仅 Windows 实现按端口收尸", file=sys.stderr)
        return 2
    ports = []
    if getattr(a, "config", False):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import dh_env
        env = dh_env.resolve(ROOT, dh_env.read_ini(ROOT / dh_env.CONFIG_REL))
        ports = [int(env["PORT"]), int(env["ASR_PORT"])]
    elif a.port:
        ports = [int(a.port)]
    if not ports:
        print("[spawn] 没给端口，也没要按配置停", file=sys.stderr)
        return 2
    out = subprocess.run(["netstat", "-ano"], capture_output=True).stdout.decode("utf-8", "replace")
    killed = 0
    for port in ports:
        pids = set()
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0].upper().startswith("TCP") and parts[3].upper() == "LISTENING":
                if parts[1].endswith(f":{port}"):
                    pids.add(parts[4])
        if not pids:
            print(f"[spawn] 端口 {port} 没有监听进程")
            continue
        for pid in pids:
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
            killed += 1
            print(f"[spawn] 已结束 PID {pid}（端口 {port}）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("start")
    sp.add_argument("--name", required=True)
    sp.add_argument("--py", required=True)
    sp.add_argument("--script", required=True)
    sp.add_argument("--port", type=int, required=True)
    sp.add_argument("--cwd", default="")
    sp.add_argument("extra", nargs="*")
    sp.set_defaults(fn=cmd_start)

    wp = sub.add_parser("wait")
    wp.add_argument("--name", default="服务")
    wp.add_argument("--url", required=True)
    wp.add_argument("--timeout", type=float, default=25)
    wp.add_argument("--interval", type=float, default=0.8)
    wp.add_argument("--expect", default="")
    wp.set_defaults(fn=cmd_wait)

    tp = sub.add_parser("stop")
    tp.add_argument("--port", type=int, default=0)
    tp.add_argument("--config", action="store_true", help="按 config/paths.ini 里的 PORT/ASR_PORT 停")
    tp.set_defaults(fn=cmd_stop)

    sp2 = sub.add_parser("status")
    sp2.add_argument("--port", type=int, required=True)
    sp2.set_defaults(fn=lambda a: (print("busy" if port_open(a.port) else "free"), 0)[1])

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
