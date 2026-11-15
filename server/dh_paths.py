# -*- coding: utf-8 -*-
"""
数字人 · 服务端跨机器路径解析
=============================
之前 `server/gpu_runner.py` / `server/gpu_worker.py` 里写死了
`E:\\conda3\\envs\\yoloces\\python.exe`、`C:/dh_prod`、`C:/dh_ds`，
换一台电脑必炸。现在统一走这里，顺序是：

    环境变量 DH_GPU_PY / DH_PROD / DH_DS
      > config/paths.ini 的 GPU_PYTHON / PROD_ROOT / DATASET_ROOT
        > _tools/dh_env.py 自动探测
          > 历史默认值（只为不改变本机现有行为）

另外：Windows 下会把中文路径换成 ASCII 软链（C:\\dh_root / C:\\dh_prod / C:\\dh_ds），
因为 ultralytics 起子进程时中文路径会被代码页吃掉（README 里记过这笔账）。
软链指向别处时**不**擅自替换，只在软链不存在时使用原路径。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_CACHE: dict = {}


def _cfg() -> dict:
    if "cfg" not in _CACHE:
        cfg = {}
        p = ROOT / "config" / "paths.ini"
        if p.is_file():
            for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
                s = raw.strip()
                if not s or s[0] in ";#[" or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                cfg[k.strip().upper()] = v.strip()
        _CACHE["cfg"] = cfg
    return _CACHE["cfg"]


def _auto() -> dict:
    """只在环境变量和配置都没给的时候才全盘探测（有副作用：起子进程，约 0.3 秒）。"""
    if "auto" not in _CACHE:
        env = {}
        try:
            sys.path.insert(0, str(ROOT / "_tools"))
            import dh_env  # type: ignore
            env = dh_env.resolve(ROOT, dh_env.read_ini(ROOT / "config" / "paths.ini"))
        except Exception:
            env = {}
        _CACHE["auto"] = env
    return _CACHE["auto"]


def _pick(cfg_key: str, env_names, auto_key: str, default: str = "") -> str:
    for n in env_names:
        v = os.environ.get(n)
        if v and Path(v).exists():
            return v
    v = _cfg().get(cfg_key, "")
    if v and Path(v).exists():
        return v
    v = _auto().get(auto_key, "")
    if v and Path(v).exists():
        return v
    return default


def _ascii_link(link: str, target: str, default: str) -> str:
    if Path(link).exists():
        return link
    return target or default


def prod_root() -> str:
    """产线工程目录（只读引用）。软链在就用软链。"""
    t = _pick("PROD_ROOT", ("DH_PROD",), "DH_PROD")
    return _ascii_link("C:/dh_prod", t, t or "C:/dh_prod")


def ds_root() -> str:
    """缺陷数据集根（认 images/train、images/val）。软链在就用软链。"""
    t = _pick("DATASET_ROOT", ("DH_DS",), "DH_DS")
    return _ascii_link("C:/dh_ds", t, t or "C:/dh_ds")


def prod_weights() -> str:
    return prod_root().rstrip("/") + "/models/yolo26m.pt"


def infer_src() -> str:
    return prod_root().rstrip("/") + "/data/real_boards/images"


def prod_db() -> str:
    return prod_root().rstrip("/") + "/data/wood_inspect.db"


def gpu_python() -> str:
    """带 CUDA 的解释器。没有就返回历史默认值，让报错信息可读。"""
    return _pick("GPU_PYTHON", ("DH_GPU_PY",), "DH_GPU_PY",
                 default=r"E:\conda3\envs\yoloces\python.exe")


def describe() -> dict:
    return {"root": str(ROOT), "gpu_python": gpu_python(), "prod_root": prod_root(),
            "ds_root": ds_root(), "prod_db": prod_db(), "infer_src": infer_src()}


if __name__ == "__main__":
    import json
    print(json.dumps(describe(), ensure_ascii=False, indent=2))
