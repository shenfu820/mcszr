# -*- coding: utf-8 -*-
"""
数字人 · 一键配置（新电脑第一步）
==================================
`.bat` 只留 3 行把活交给这里 —— 因为 cmd 只认 ANSI：文件里只要有中文，
换台机器/换个编辑器另存一次就可能被拆成
    '��失的仓库：python' is not recognized ...
（实测：纯 ASCII 的 .bat 即使被存成 LF 也照常跑；坏的一直是中文。）

这个脚本做四件事：
  1. 自动遍历磁盘，把各工程的位置找出来写回 config\\paths.ini（不写死路径）
  2. 装三个环境 main/asr/gpu（缺什么装什么，已有的复用）
  3. 修 ASCII 软链 + 修 .bat 行尾
  4. 打体检表，用**体检结果**决定退出码

用法（参数原样透传给 dh_bootstrap.py）：
    python _tools\\dh_setup.py                 装 main+asr+gpu
    python _tools\\dh_setup.py --check         只看计划，什么都不改
    python _tools\\dh_setup.py --only asr
    python _tools\\dh_setup.py --index-url https://pypi.tuna.tsinghua.edu.cn/simple
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent


def run(script: str, args: list) -> int:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, str(TOOLS / script), "--root", str(ROOT), *args]
    return subprocess.call(cmd, env=env)


def main() -> int:
    args = sys.argv[1:]
    check_only = any(a.lower() == "--check" for a in args)

    # 1) 先找路径（--check 时只读）
    run("discover.py", (["--check"] if check_only else ["--write"]))

    # 2) 装环境（没给参数就三个都装）
    boot_args = args if args else ["--all"]
    rc_boot = run("dh_bootstrap.py", ["--no-report", *boot_args])

    # 3) 软链 + 行尾（--check 不动文件）
    if not check_only:
        run("dh_env.py", ["--fix"])
        run("bat_lint.py", ["--fix"])

    # 4) 体检：退出码看这个，而不是看第 2 步
    print()
    print("==== 体检 ====")
    rc = run("dh_env.py", ["--check", "--next-hint"])

    if rc_boot != 0 and rc == 0:
        print()
        print("[说明] 有一个环境没装上（上面 PLAN/FAIL 行），但主服务能跑，体检也通过。")
        print("       缺 asr 就只能打字，缺 gpu 就 ⑤屏失效 —— 要补齐就再跑一次本脚本。")
    return rc


if __name__ == "__main__":
    sys.exit(main())
