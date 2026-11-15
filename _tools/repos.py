# -*- coding: utf-8 -*-
"""
数字人 · 依赖项目与仓库清单
============================
数字人能跑起来靠的不止一个仓库：产线工程 `new mcjc`（45.7 GB）、对话中枢 `angent`、
四屏舞台包、缺陷数据集，各自在不同位置、不同仓库状态。这个脚本只做三件事：

  1. 按 config\\repos.ini 把「谁在哪、有没有仓库、远端是什么」查清楚（--report）
  2. 给还没有仓库的项目建一个本地 git 仓库（--init <key>，只对 git=local 的条目开放）
  3. 生成给人看的清单 docs\\仓库与依赖清单.md（--manifest）

用法：
    python _tools/repos.py --report                 表格：在位 / 仓库 / 远端 / 未提交
    python _tools/repos.py --report --json          机器可读
    python _tools/repos.py --check                  required=yes 的必须在位，否则退出码 1
    python _tools/repos.py --init agent             给 angent 建本地仓库
    python _tools/repos.py --clone prod --yes       按登记表把缺的仓库克隆下来（大仓库要 --yes）
    python _tools/repos.py --manifest               写 docs\\仓库与依赖清单.md
    python _tools/repos.py --probe                  额外探测远端是否可达（要网络，慢）
"""
from __future__ import annotations

import argparse
import configparser
import json
import os
import subprocess
import sys
import time
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dh_env  # noqa: E402

ROOT_DEFAULT = Path(__file__).resolve().parent.parent
REPOS_REL = Path("config") / "repos.ini"
MANIFEST_REL = Path("docs") / "仓库与依赖清单.md"
REMOTE_KEYS = ("ssh_github", "https_github", "https_gitee", "https_gitee_origin",
               "https_gitee_inspection", "ssh_gitee", "https", "ssh")
WEB_KEYS = ("web_github", "web_gitee", "web_gitee_origin", "web_gitee_inspection")


# ------------------------------------------------------------------ 读登记表
def load_repos(root: Path) -> "OrderedDict[str, dict]":
    p = root / REPOS_REL
    if not p.is_file():
        raise SystemExit(f"[FAIL] 缺 {p}（登记表不在了）")
    cp = configparser.RawConfigParser()
    cp.read(p, encoding="utf-8")
    out: "OrderedDict[str, dict]" = OrderedDict()
    for sec in cp.sections():
        item = {k: cp.get(sec, k, fallback="").strip() for k in cp.options(sec)}
        item["key"] = sec
        out[sec] = item
    return out


def ctx(root: Path) -> dict:
    cfg = dh_env.read_ini(root / dh_env.CONFIG_REL)
    prod = cfg.get("PROD_ROOT") or os.environ.get("DH_PROD") or ""
    ds = cfg.get("DATASET_ROOT") or os.environ.get("DH_DS") or ""
    return {
        "%ROOT%": str(root),
        "@PROD_ROOT": prod,
        "@PROD_SIBLING": str(Path(prod).parent) if prod else "",
        "@DATASET_ROOT": ds,
    }


def expand(value: str, c: dict) -> str:
    if not value:
        return ""
    out = value
    for k, v in c.items():
        out = out.replace(k, v)
    return out


# ------------------------------------------------------------------ git 探测
def _git(args, cwd: Path, timeout=20) -> tuple:
    try:
        p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace").strip(), p.stderr.decode("utf-8", "replace").strip()
    except Exception as e:                       # noqa: BLE001
        return 1, "", f"{type(e).__name__}: {e}"


def git_state(path: Path) -> dict:
    st = {"is_repo": (path / ".git").exists(), "branch": "", "head": "", "dirty": None, "remotes": {}}
    if not st["is_repo"]:
        return st
    _, branch, _ = _git(["rev-parse", "--abbrev-ref", "HEAD"], path)
    st["branch"] = branch
    _, head, _ = _git(["log", "--oneline", "-1"], path)
    st["head"] = head
    rc, porc, _ = _git(["status", "--porcelain"], path)
    if rc == 0:
        st["dirty"] = len([ln for ln in porc.splitlines() if ln.strip()])
    rc, remotes, _ = _git(["remote", "-v"], path)
    if rc == 0:
        for line in remotes.splitlines():
            parts = line.split()
            if len(parts) >= 2 and "(fetch)" in line:
                st["remotes"][parts[0]] = parts[1]
    return st


def collect(root: Path, probe: bool = False) -> list:
    c = ctx(root)
    rows = []
    for key, item in load_repos(root).items():
        path = Path(expand(item.get("path", ""), c))
        row = dict(item)
        row["path_resolved"] = str(path)
        row["exists"] = path.is_dir()
        row["git_state"] = git_state(path) if row["exists"] else {"is_repo": False}
        link = ""
        for k in REMOTE_KEYS:
            if item.get(k):
                link = item[k]
                break
        row["clone_url"] = link
        row["web"] = [(k, item[k]) for k in WEB_KEYS if item.get(k)]
        if probe and link and not row["git_state"].get("is_repo"):
            rc, out, err = _git(["ls-remote", "--heads", link], root, timeout=30)
            row["probe"] = "可达" if rc == 0 else f"不可达（{(err or out).splitlines()[0][:60] if (err or out) else 'rc=%s' % rc}）"
        rows.append(row)
    return rows


# ------------------------------------------------------------------ 输出
def in_tree(root: Path, path: Path) -> bool:
    """path 是否就在本仓库里面（四屏舞台包就是这种，跟着主仓库走，不单独建仓库）。"""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:                            # noqa: BLE001
        return False


def state_text(r: dict, root: Path) -> str:
    g = r["git_state"]
    if not r["exists"]:
        return "缺（这台机器上没有）"
    if g.get("is_repo"):
        s = f"有仓库 分支 {g.get('branch') or '?'} ｜ HEAD {(g.get('head') or '')[:48]}"
        if g.get("dirty"):
            s += f" ｜ 未提交 {g['dirty']} 处"
        return s
    if in_tree(root, Path(r["path_resolved"])):
        return "随本仓库（子目录，不用单独建仓库）"
    if r.get("git") == "none":
        return "目录在（登记为不建仓库）"
    return "目录在，但没有 git 仓库"


def print_report(rows: list, as_json: bool = False, root: Path = ROOT_DEFAULT) -> int:
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print("=" * 100)
    print("数字人 · 依赖项目与仓库")
    print("=" * 100)
    for r in rows:
        g = r["git_state"]
        state = state_text(r, root)
        print(f"[{r['key']}] {r.get('name', '')}")
        print(f"   角色   {r.get('role', '')}")
        print(f"   路径   {r['path_resolved'] or '-'}")
        print(f"   状态   {state}")
        if g.get("remotes"):
            for name, url in g["remotes"].items():
                print(f"   远端   {name:12} {url}")
        elif r.get("web"):
            for name, url in r["web"]:
                print(f"   链接   {name:18} {url}")
        if r.get("probe"):
            print(f"   探测   {r['probe']}")
        print(f"   体量   {r.get('size', '-')}")
        print(f"   说明   {r.get('note', '')}")
        print("-" * 100)
    have = sum(1 for r in rows if r["exists"])
    repo = sum(1 for r in rows if r["git_state"].get("is_repo"))
    print(f"在位 {have}/{len(rows)} ｜ 有仓库 {repo}/{len(rows)}")
    print("生成给人看的清单：python _tools/repos.py --manifest")
    return 0


def do_check(rows: list) -> int:
    bad = [r for r in rows if str(r.get("required", "")).lower() in ("yes", "true", "1") and not r["exists"]]
    for r in bad:
        print(f"[FAIL] 必需项不在位：{r['key']}（{r.get('name')}）→ {r['path_resolved']}")
    if not bad:
        print(f"[OK] {sum(1 for r in rows if r.get('required','').lower() in ('yes','true','1'))} 个必需项都在位")
    return 1 if bad else 0


def do_init(root: Path, rows: list, key: str) -> int:
    row = next((r for r in rows if r["key"] == key), None)
    if not row:
        print(f"[FAIL] 登记表里没有 {key}（可选：{', '.join(r['key'] for r in rows)}）")
        return 2
    path = Path(row["path_resolved"])
    if not path.is_dir():
        print(f"[FAIL] 目录不存在：{path}")
        print("       先把项目放到这个位置（或改 config\\repos.ini 的 path=）再建仓库")
        return 2
    if row["git_state"].get("is_repo"):
        print(f"[skip] {key} 已经是 git 仓库：{path}")
        return 0
    if row.get("git") != "local":
        print(f"[FAIL] {key} 的 git={row.get('git')}，只允许对 git=local 的条目建仓库")
        print("       （git=none 是 GB 级数据；git=yes 说明它自己有仓库，别在这里重复 init）")
        return 2

    # .gitignore：ini 里用 | 分隔
    ign = [x.strip() for x in (row.get("ignore") or "").split("|") if x.strip()]
    gi = path / ".gitignore"
    if ign and not gi.is_file():
        gi.write_text("\n".join(ign) + "\n", encoding="utf-8", newline="")
        print(f"[ok] 写 .gitignore（{len(ign)} 条忽略规则）")
    elif ign:
        print(f"[skip] 已有 .gitignore，不覆盖：{gi}")

    user = subprocess.run(["git", "config", "--global", "--get", "user.name"],
                          capture_output=True).stdout.decode("utf-8", "replace").strip()
    email = subprocess.run(["git", "config", "--global", "--get", "user.email"],
                           capture_output=True).stdout.decode("utf-8", "replace").strip()
    if not user or not email:
        print("[FAIL] git 全局 user.name / user.email 没配，先配：")
        print('       git config --global user.name "你的名字"')
        print('       git config --global user.email "you@example.com"')
        return 2

    rc, _, err = _git(["init"], path)
    if rc != 0:
        print(f"[FAIL] git init 失败：{err}")
        return 2
    _git(["symbolic-ref", "HEAD", "refs/heads/master"], path)     # 与其它仓库统一用 master
    rc, out, err = _git(["add", "-A"], path)
    if rc != 0:
        print(f"[FAIL] git add 失败：{err}")
        return 2
    msg = "chore: 首次入库（代码与配置；数据库/日志/截图按 .gitignore 排除）"
    rc, out, err = _git(["commit", "-m", msg], path)
    if rc != 0:
        print(f"[FAIL] git commit 失败：{err or out}")
        return 2
    st = git_state(path)
    tracked = int(_git(["ls-files"], path)[1].count("\n")) + 1 if _git(["ls-files"], path)[1] else 0
    print(f"[ok] {key} 已建本地仓库：{path}")
    print(f"     分支 {st['branch']} ｜ HEAD {st['head'][:60]}")
    print(f"     已跟踪 {tracked} 个文件")
    print("     还没有远端；要推上去就在这个目录 git remote add origin <网址> && git push -u origin master")
    return 0


def do_clone(root: Path, rows: list, key: str, yes: bool) -> int:
    row = next((r for r in rows if r["key"] == key), None)
    if not row:
        print(f"[FAIL] 登记表里没有 {key}")
        return 2
    if row["exists"]:
        print(f"[skip] {key} 已经在位：{row['path_resolved']}")
        return 0
    url = row.get("clone_url") or ""
    if not url:
        print(f"[FAIL] {key} 没有登记 clone 地址（config\\repos.ini 里加 https_* 字段）")
        return 2
    big = "GB" in (row.get("size") or "")
    if big and not yes:
        print(f"[WARN] {key} 体量很大（{row.get('size')}），确认要现在 clone 就加 --yes")
        print(f"       目标 {row['path_resolved']} ← {url}")
        return 3
    target = Path(row["path_resolved"])
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"$ git clone {url} \"{target}\"")
    rc = subprocess.run(["git", "clone", url, str(target)]).returncode
    if rc != 0:
        print(f"[FAIL] clone 失败（rc={rc}）：检查网络/凭据；gitee 私有库要登录")
        return rc
    print(f"[ok] {key} 已 clone 到 {target}")
    if big:
        print("     注意：仓库里通常没有权重/检测库/数据集，按 docs\\仓库与依赖清单.md 补齐")
    return 0


def write_manifest(root: Path, rows: list) -> int:
    out = root / MANIFEST_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    A = lines.append
    A("# 仓库与依赖清单")
    A("")
    A("> 本文件由 `python _tools/repos.py --manifest` 生成，手工改会被下次覆盖。")
    A(f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}　生成机器：`{os.environ.get('COMPUTERNAME', '?')}`")
    A(f"> 登记表：`config\\repos.ini`　本机项目根：`{root}`")
    A("")
    A("## 一、一张表")
    A("")
    A("| key | 项目 | 角色 | 本机路径 | 仓库状态 | 远端/链接 | 体量 |")
    A("|---|---|---|---|---|---|---|")
    for r in rows:
        g = r["git_state"]
        if not r["exists"]:
            st = "**不在位**"
        elif g.get("is_repo"):
            st = "有仓库"
            if g.get("dirty"):
                st += f"（未提交 {g['dirty']}）"
        elif in_tree(root, Path(r["path_resolved"])):
            st = "随本仓库"
        else:
            st = "无仓库" if r.get("git") != "none" else "不建仓库"
        links = []
        if g.get("remotes"):
            links = [f"`{n}` {u}" for n, u in g["remotes"].items()]
        elif r.get("web"):
            links = [f"[{n}]({u})" for n, u in r["web"]]
        A(f"| `{r['key']}` | {r.get('name', '')} | {r.get('role', '')} | "
          f"`{r['path_resolved'] or '-'}` | {st} | {'<br>'.join(links) or '-'} | {r.get('size', '-')} |")
    A("")
    A("## 二、链接汇总（复制给别人时只看这一段）")
    A("")
    for r in rows:
        web = list(r.get("web") or [])
        remotes = list(r["git_state"].get("remotes", {}).items())
        if not web and not remotes:
            continue
        A(f"### {r.get('name', r['key'])}")
        for n, u in web:
            A(f"- 网页（{n}）：{u}")
        for n, u in remotes:
            web_url = u[:-4] if u.startswith("https://") and u.endswith(".git") else ""
            if web_url:
                A(f"- clone（{n}）：`git clone {u}`　→ {web_url}")
            elif u.startswith("git@"):
                A(f"- clone（{n}，ssh 别名）：`git clone {u}`")
            else:
                A(f"- clone（{n}）：`git clone {u}`")
        A("")
    A("## 三、逐个说明")
    A("")
    for r in rows:
        g = r["git_state"]
        A(f"### {r.get('name', r['key'])}（`{r['key']}`）")
        A("")
        A(f"- 角色：{r.get('role', '-')}")
        A(f"- 本机路径：`{r['path_resolved']}`　{'在位' if r['exists'] else '**不在位**'}")
        if g.get("is_repo"):
            A(f"- 仓库：分支 `{g.get('branch')}`，HEAD `{(g.get('head') or '')}`"
              + (f"，未提交 {g['dirty']} 处" if g.get("dirty") else "，工作区干净"))
        else:
            A(f"- 仓库：{'无（登记为不建仓库）' if r.get('git') == 'none' else '无'}")
        for k in WEB_KEYS:
            if r.get(k):
                A(f"- 网页：{r[k]}")
        for k in ("ssh_github", "https_gitee", "https_origin", "https_inspection"):
            if r.get(k):
                A(f"- 地址：`{r[k]}`")
        if r.get("pull"):
            A(f"- 拉取：`{r['pull']}`")
        if r.get("push"):
            A(f"- 推送：{r['push']}")
        A(f"- 体量：{r.get('size', '-')}")
        if r.get("note"):
            A(f"- 说明：{r['note']}")
        A("")
    A("## 四、新机器怎么把依赖拿齐")
    A("")
    A("```")
    A("1) 数字人本体（本仓库）")
    A("      git clone https://gitee.com/shenfu1314/mcszr.git      # 或 https://github.com/shenfu820/mcszr")
    A("      cd mcszr && 一键配置环境.bat                          # 三个环境一条命令装完")
    A("")
    A("2) 四屏舞台包：随本仓库走，不用单独 clone")
    A("")
    A("3) 产线工程 new mcjc（只有代码；权重/检测库/输出要另拷）")
    A("      git clone https://gitee.com/shenfu1314/wood-testing.git <目标目录>")
    A("      然后把 config\\paths.ini 的 PROD_ROOT 指到 <目标目录>")
    A("")
    A("4) 对话中枢 angent：目前只有本地仓库（没有远端）")
    A("      - 在产线机上：整个目录拷过去即可（git=local 的仓库不带历史也行）")
    A("      - 或拿到远端地址后：git clone <地址> <PROD_ROOT 的父目录>\\angent")
    A("")
    A("5) 缺陷数据集：不建仓库，只登记路径")
    A("      把数据集放到任意目录，config\\paths.ini 的 DATASET_ROOT= 指过去")
    A("      结构要求：images\\{train,val} + labels\\{train,val}（gpu_worker 按它生成 data.yaml）")
    A("```")
    A("")
    A("## 五、注意")
    A("")
    A("- `new mcjc` 45.7 GB（`output/` 34.8 GB、`data/` 1.3 GB、`models/` 1.2 GB）不进仓库；")
    A("  clone 下来只有代码，**检测库 `data/wood_inspect.db` 与权重 `models/*.pt` 要单独拷**，")
    A("  否则体检表里的「产线库」会显示 WARN（查询类回答会说库不存在）。")
    A("- `angent` 的 `data\\*.db*`、`screenshots\\`、`logs\\` 不入库（数据库与截图是运行产物）。")
    A("- 数字人本仓库的 `.venv*/`、`models/`、`_tools/`（除部署工具）已 gitignore。")
    A("")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    print(f"[ok] 已生成 {out}")
    return 0


# ------------------------------------------------------------------ 入口
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--init", default="", metavar="KEY")
    ap.add_argument("--clone", default="", metavar="KEY")
    ap.add_argument("--yes", action="store_true", help="克隆大仓库时确认")
    ap.add_argument("--manifest", action="store_true")
    ap.add_argument("--probe", action="store_true", help="探测远端可达性（要网络）")
    a = ap.parse_args()

    root = Path(a.root).resolve() if a.root else ROOT_DEFAULT
    rows = collect(root, probe=a.probe)

    rc = 0
    did = False
    if a.init:
        rc = do_init(root, rows, a.init)
        did = True
        rows = collect(root)
    if a.clone:
        rc = do_clone(root, rows, a.clone, a.yes)
        did = True
        rows = collect(root)
    if a.manifest:
        rc = write_manifest(root, rows)
        did = True
    if a.check:
        rc = max(rc, do_check(rows))
        did = True
    if a.report or a.json or not did:
        print_report(rows, as_json=a.json, root=root)
    return rc


if __name__ == "__main__":
    sys.exit(main())
