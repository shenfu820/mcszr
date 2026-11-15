# -*- coding: utf-8 -*-
"""
数字人 · 批处理文件编码体检 / 修复
==================================
cmd 解析 .bat 的两条铁律（踩过的坑，写在这里免得再踩）：

1. **必须是 CRLF**。LF-only 的 .bat 在 cmd 里会被拆成残缺命令行，报出
   `'xxx' is not recognized as an internal or external command`，而那个 xxx
   往往是一段中文的碎片（比如 "voice" / "+"），跟真正的错误毫无关系。
2. **不能有 BOM**。BOM 会粘在第一行 `@echo off` 前面，第一行直接报错。
3. 文件是 UTF-8 时，`chcp 65001` 必须出现在**第一处中文之前**——
   cmd 是边读边解析的，先切代码页再读中文，才不会把汉字切碎。

用法：
    python _tools/bat_lint.py --check     只检查（有问题退出码 1）
    python _tools/bat_lint.py --fix       就地修：去 BOM + 转 CRLF
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"_backup", ".git", ".idea", "models", "node_modules", "__pycache__",
             "venv", "i23d"}          # i23d = 第三方 vendored 工具，不归我们管
SKIP_PREFIX = (".venv",)


def iter_bats(root: Path):
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in (".bat", ".cmd") or not p.is_file():
            continue
        parts = set(p.relative_to(root).parts[:-1])
        if parts & SKIP_DIRS:
            continue
        if any(part.startswith(SKIP_PREFIX) for part in p.relative_to(root).parts[:-1]):
            continue
        yield p


def inspect(p: Path) -> list:
    raw = p.read_bytes()
    probs = []
    if raw.startswith(b"\xef\xbb\xbf"):
        probs.append("有 UTF-8 BOM（第一行 @echo off 会失效）")
    if b"\n" in raw and b"\r\n" not in raw:
        probs.append("行尾是 LF，cmd 会把它拆成残缺命令（必须 CRLF）")
    elif raw.count(b"\n") != raw.count(b"\r\n"):
        lonely_lf = raw.count(b"\n") - raw.count(b"\r\n")
        probs.append(f"行尾混用（{lonely_lf} 处换行不是 CRLF）")
    # 中文必须出现在 chcp 之后
    if any(b > 0x7F for b in raw):
        text = raw.decode("utf-8", "replace") if b"\xef\xbb\xbf" not in raw else raw.decode("utf-8-sig", "replace")
        lines = text.splitlines()
        chcp_at, first_cn = None, None
        for i, line in enumerate(lines, 1):
            low = line.strip().lower()
            if chcp_at is None and low.startswith("chcp") and "65001" in low:
                chcp_at = i
            if first_cn is None and any(ord(c) > 0x7F for c in line):
                first_cn = i
        if first_cn and (chcp_at is None or first_cn < chcp_at):
            probs.append(f"第 {first_cn} 行出现非 ASCII，但 `chcp 65001` 在第 {chcp_at or '无'} 行（中文会被切碎）")
    return probs


def fix(p: Path) -> bool:
    raw = p.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8", "replace")
    new = "\r\n".join(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    out = new.encode("utf-8")
    if out != p.read_bytes():
        p.write_bytes(out)
        return True
    return False


def fix_all(root: Path) -> int:
    """把 root 下所有 .bat/.cmd 就地修成 utf-8 + CRLF（去 BOM）。返回修了几个。"""
    n = 0
    for p in iter_bats(root):
        if fix(p):
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--root", default="")
    a = ap.parse_args()
    root = Path(a.root).resolve() if a.root else ROOT

    files = list(iter_bats(root))
    bad = 0
    for p in files:
        if a.fix and fix(p):
            print(f"[fix] {p.relative_to(root)}")
        probs = inspect(p)
        if probs:
            bad += 1
            print(f"[FAIL] {p.relative_to(root)}")
            for x in probs:
                print(f"       - {x}")
        elif not a.fix:
            print(f"[OK]   {p.relative_to(root)}")
    print(f"---- 共 {len(files)} 个批处理，{bad} 个有问题 ----")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
