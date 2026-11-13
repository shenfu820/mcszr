# -*- coding: utf-8 -*-
"""
数字人 · Agent 层
=================
双通道设计，现场不赌网络：

  通道 A（LLM）：环境里有 DEEPSEEK_API_KEY / 或 config/llm.json 配了 key，
                 走 OpenAI 兼容 function-calling，多轮工具调用。

  通道 B（离线意图引擎）：没有任何 key 时默认走这条。
                 用剧本里真实存在的四个交付任务 + 三类查询做意图匹配，
                 同样真写文件、真 git commit、真查库 —— 只是“理解”靠规则而非模型。

两条通道共用同一批工具（tools.py），所以台上看到的落盘、commit、报警都是真的。

对外：has_key() / catalog() / chat(message, deliver=False)
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import tools
import gpu_runner
import ops_monitor

ROOT = Path(__file__).resolve().parent.parent
CFG_FILE = ROOT / "config" / "llm.json"
TEMPLATE_DIR = ROOT / "server" / "templates"

DEFAULT_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


# ══════════════════════════════════════════ 配置
def load_cfg() -> dict:
    cfg = {
        "enabled": True,
        "base_url": os.environ.get("DH_LLM_BASE", DEFAULT_BASE),
        "model": os.environ.get("DH_LLM_MODEL", DEFAULT_MODEL),
        "api_key": "",
        "api_key_env": "DEEPSEEK_API_KEY",
        "temperature": 0.2,
        "max_rounds": 4,
        "timeout": 60,
    }
    if CFG_FILE.exists():
        try:
            cfg.update(json.loads(CFG_FILE.read_text(encoding="utf-8-sig")))
        except Exception:
            pass
    return cfg


def get_key() -> str:
    cfg = load_cfg()
    return (cfg.get("api_key") or "").strip() or os.environ.get(cfg.get("api_key_env", "DEEPSEEK_API_KEY"), "").strip()


def has_key() -> bool:
    return bool(get_key())


# ══════════════════════════════════════════ 工具（两条通道共用）
SYSTEM_PROMPT = """你是「小星」，木业板材表面缺陷智能检测项目的数字工程师，兼任团队的 AI 编程助手与智能运维工程师。
你的同事是：1号杨飞越（项目经理/FDE）、2号周泽誉（机器学习工程师）、3号刘家荣（AI应用开发）、4号郭晓艺（数据分析）。
身份口径：剧本里同事喊你「5号同事」，台词里你自称「5号选手」，说的都是你本人；别人提起「5号」时不要否认，也别改口说自己不是。

说话要求：
- 中文，简短、干脆，像工程同事而不是客服。每次回复 1~3 句，不要长篇大论。
- 不要自吹，不要用“作为一名AI”这类话。被问到数字时先调工具，绝不凭印象报数。
- 拿不准就说不确定，并说明需要查什么。

干活要求（重要）：
- 同事让你「训练 / 微调 / 让 GPU 跑起来 / 评估一下 / 检测这批板」，**不要只嘴上答应**，
  直接调 start_gpu_job 把真作业起起来，然后一句话说清你在跑什么、大概多久。
- 起完作业之后，同事问「跑完没 / 到哪一步了」，用 job_status 读真实进度再回答。
- 同事问「模型性能怎么样 / 在往哪走」，用 ops_state 或 model_health 拿真实序列再说。
- 作业是跑完自己停的，不需要你守着；正在跑时又说要跑，就告诉他上一个还没完。

可调用工具：
- overview(days)：检测概览（条数、类别分布）
- hourly(hours)：按小时分布，找缺陷高发时段
- model_health()：模型性能健康度（真实置信度漂移/异常分/转人工率），degraded=true 说明性能异常
- recent_detections(limit)：最近检测记录
- pending_reviews(limit)：待人工复核队列
- write_deliverable(task, files)：把起草的代码写进该项目文件夹（task 取 preprocess/optimize/framework/dashboard）
- git_commit(task, message)：在交付仓库留痕
- start_gpu_job(mode)：**真起一个 GPU 作业**，mode 取 train（微调训练）/eval（验证集评估）/infer（批量推理）
- job_status()：当前 GPU 作业的真实进度（第几轮、指标、耗时、日志尾）
- ops_state()：身边运维屏的数据（GPU 利用率/显存 + 模型性能趋势 + 告警）

合规红线：不修改产线配置、不覆盖 models/yolo26m_wood_best.pt、不删库、不删历史产物。
GPU 作业的权重只落在数字人/ops_jobs/ 下的作业目录里。"""

TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "overview", "description": "检测概览：近 N 天的检测条数、缺陷类别分布",
        "parameters": {"type": "object", "properties": {
            "days": {"type": "integer", "description": "统计天数，默认 1"}}}}},
    {"type": "function", "function": {
        "name": "hourly", "description": "按小时统计缺陷分布，找出高发时段",
        "parameters": {"type": "object", "properties": {
            "hours": {"type": "integer", "description": "最近多少小时，默认 24"}}}}},
    {"type": "function", "function": {
        "name": "model_health", "description": "模型性能健康度与异常判定（真实数据）",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "recent_detections", "description": "最近若干条检测记录",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "条数，默认 10"}}}}},
    {"type": "function", "function": {
        "name": "pending_reviews", "description": "待人工复核队列",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "条数，默认 10"}}}}},
    {"type": "function", "function": {
        "name": "write_deliverable", "description": "把起草的代码写入该项目文件夹（沙箱内）",
        "parameters": {"type": "object", "properties": {
            "task": {"type": "string", "enum": ["preprocess", "optimize", "framework", "dashboard"]},
            "note": {"type": "string", "description": "交付说明"}},
            "required": ["task"]}}},
    {"type": "function", "function": {
        "name": "git_commit", "description": "在交付仓库里为这次交付留痕（commit）",
        "parameters": {"type": "object", "properties": {
            "task": {"type": "string"},
            "message": {"type": "string"}}, "required": ["task", "message"]}}},
    {"type": "function", "function": {
        "name": "start_gpu_job",
        "description": "在本机 GPU 上真起一个作业并自动跑完退出。train=用真实数据集微调，eval=验证集跑 mAP，infer=对一批真实板材图跑推理",
        "parameters": {"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["train", "eval", "infer"],
                     "description": "作业类型"},
            "epochs": {"type": "integer", "description": "训练轮数（train 用，默认 2）"},
            "imgsz": {"type": "integer", "description": "输入尺寸，默认 320"},
            "fraction": {"type": "number", "description": "只用多少比例数据，默认 0.12"}},
            "required": ["mode"]}}},
    {"type": "function", "function": {
        "name": "job_status", "description": "读当前 GPU 作业的真实进度与指标",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "ops_state",
        "description": "运维屏数据：GPU 利用率/显存/温度、模型性能趋势（自学习 mAP50 序列、会话置信度）、健康告警",
        "parameters": {"type": "object", "properties": {}}}},
]


def catalog() -> list:
    return [{"name": t["function"]["name"], "desc": t["function"]["description"]} for t in TOOL_SPECS]


def _dispatch(name: str, args: dict) -> dict:
    """执行工具，返回结构化结果。异常也返回结构，不炸主流程。"""
    try:
        if name == "overview":
            return tools.overview(int(args.get("days") or 1))
        if name == "hourly":
            return tools.hourly(int(args.get("hours") or 24))
        if name == "model_health":
            return tools.model_health(20)
        if name == "recent_detections":
            return tools.recent_detections(int(args.get("limit") or 10))
        if name == "pending_reviews":
            return tools.pending_reviews(int(args.get("limit") or 10))
        if name == "write_deliverable":
            return deliver_task(args.get("task", ""), note=args.get("note", ""))
        if name == "git_commit":
            return tools.git_commit(args.get("task", ""), args.get("message", "数字人交付"))
        if name == "start_gpu_job":
            kw = {k: args[k] for k in ("epochs", "imgsz", "fraction") if args.get(k)}
            return gpu_runner.RUNNER.start(args.get("mode", "train"), **kw)
        if name == "job_status":
            return gpu_runner.RUNNER.status()
        if name == "ops_state":
            return ops_monitor.state(job=gpu_runner.RUNNER.status())
        return {"error": f"未知工具 {name}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ══════════════════════════════════════════ 交付任务（真实代码）
TASKS = {
    "preprocess": {
        "dir": "01_预处理",
        "label": "图像预处理脚本",
        "notify": "delivered_1",
        "owner": "1号",
    },
    "optimize": {
        "dir": "02_模型优化",
        "label": "YOLO26m 优化模块",
        "notify": "delivered_2",
        "owner": "2号",
    },
    "framework": {
        "dir": "03_系统框架",
        "label": "检测系统框架",
        "notify": "delivered_3",
        "owner": "3号",
    },
    "dashboard": {
        "dir": "04_可视化大屏",
        "label": "数据可视化大屏",
        "notify": "delivered_4",
        "owner": "4号",
    },
}


SKIP_DIRS = {"__pycache__", ".git", ".idea", "node_modules", ".venv"}
SKIP_SUFFIX = {".pyc", ".pyo", ".so", ".dll", ".exe", ".jpg", ".png", ".vrm", ".mp3", ".db"}


def _read_templates(task: str) -> dict:
    """从 server/templates/<task>/ 读取要交付的文件（只收文本文件）。

    要注意别把 __pycache__/*.pyc 之类二进制当源码读进来 —— 曾经踩过。
    """
    base = TEMPLATE_DIR / task
    if not base.exists():
        return {}
    out = {}
    for f in sorted(base.rglob("*")):
        if not f.is_file():
            continue
        if any(part in SKIP_DIRS for part in f.parts):
            continue
        if f.suffix.lower() in SKIP_SUFFIX:
            continue
        try:
            out[f.relative_to(base).as_posix()] = f.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            continue      # 非 UTF-8 文本就跳过，不阻断交付
    return out


def deliver_task(task: str, note: str = "") -> dict:
    """把一个任务的代码真正写进 交付/<任务目录>/，并 git 留痕。"""
    if task not in TASKS:
        return {"error": f"未知任务 {task}，可选 {list(TASKS)}"}
    meta = TASKS[task]
    files = _read_templates(task)
    if not files:
        return {"error": f"模板缺失：{TEMPLATE_DIR / task}"}

    target = f"{meta['dir']}"
    readme = note or (
        f"# {meta['label']}（数字人交付）\n\n"
        f"- 交付人：小星 · AI 编程助手\n"
        f"- 交付对象：{meta['owner']}同事\n"
        f"- 交付时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- 说明：请审查后使用；参数集中在配置文件，未写死在代码里。\n"
    )
    res = tools.write_deliverable(target, files, note=readme)
    commit = tools.git_commit(
        target,
        f"feat({task}): 小星交付{meta['label']}（{res['count']} 个文件）",
    )
    return {
        "task": task,
        "label": meta["label"],
        "dir": res["dir"],
        "files": res["files"],
        "count": res["count"],
        "commit": commit,
        "notify_cue": meta["notify"],
        "owner": meta["owner"],
    }


# ══════════════════════════════════════════ 通道 A：真 LLM
def _http_chat(messages: list, tools_spec: list, cfg: dict) -> dict:
    key = get_key()
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": cfg.get("temperature", 0.2),
        "stream": False,
    }
    if tools_spec:
        payload["tools"] = tools_spec
        payload["tool_choice"] = "auto"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=cfg.get("timeout", 60)) as r:
        return json.loads(r.read().decode("utf-8"))


def chat_llm(message: str, history: list | None = None) -> dict:
    cfg = load_cfg()
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in (history or [])[-6:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            msgs.append({"role": h["role"], "content": h["content"]})
    msgs.append({"role": "user", "content": message})

    steps = []
    for rnd in range(int(cfg.get("max_rounds", 4))):
        try:
            resp = _http_chat(msgs, TOOL_SPECS, cfg)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            return {"ok": False, "channel": "llm", "error": f"HTTP {e.code}: {detail}",
                    "say": "大模型通道报错了，我先把错误原样放上来，不猜。", "steps": steps}
        except Exception as e:
            return {"ok": False, "channel": "llm", "error": f"{type(e).__name__}: {e}",
                    "say": "大模型通道连不上，我这边先停住，等你确认网络或密钥。", "steps": steps}

        choice = (resp.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls = msg.get("tool_calls") or []

        if not calls:
            return {"ok": True, "channel": "llm", "say": (msg.get("content") or "").strip(),
                    "steps": steps, "rounds": rnd + 1}

        msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            fn = (c.get("function") or {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            res = _dispatch(name, args)
            steps.append({"tool": name, "args": args,
                          "ok": "error" not in (res or {}),
                          "brief": _brief(res)})
            msgs.append({
                "role": "tool", "tool_call_id": c.get("id", ""), "name": name,
                "content": json.dumps(res, ensure_ascii=False, default=str)[:6000],
            })

    return {"ok": True, "channel": "llm", "say": "工具调用轮次用完了，我先停在这里。",
            "steps": steps, "rounds": int(cfg.get("max_rounds", 4))}


# ══════════════════════════════════════════ 通道 B：离线意图引擎
INTENT_RULES = [
    # (正则, 意图)
    (r"预处理|降噪|增强|归一化|划分|数据集", "preprocess"),
    (r"模型优化|优化代码|yolo|算法优化|小目标|专家复核|自学习", "optimize"),
    (r"系统集成|框架|应用开发|软件|erp|报警联动", "framework"),
    (r"大屏|可视化|看板|图表|dashboard", "dashboard"),
    (r"性能|准确率|下滑|异常|效率|巡检|监控|退化", "health"),
    (r"高峰|时段|几点|小时", "hourly"),
    (r"今天|多少|统计|概览|产量|合格率|检了", "overview"),
    (r"待复核|复核队列|转人工", "reviews"),
    (r"最近|最新|记录", "detections"),
]


def _detect_intent(text: str) -> str:
    t = (text or "").lower()
    for pat, intent in INTENT_RULES:
        if re.search(pat, t, re.I):
            return intent
    return "unknown"


# ══════════════════════════════════════════ 通道 B：GPU 作业意图（离线也要真干活）
GPU_RULES = [
    (r"停(掉|下|了)?(这个|那个)?(作业|任务)|别跑了|中断", "stop"),
    (r"跑完(了)?没|到哪(一)?步|进度|在干(嘛|什么)|还(在)?跑|跑多久了", "status"),
    (r"评估|验证集|跑个?m?ap|测试集", "eval"),
    (r"推理|检测这批|跑一批|检一板|批量检测|过一遍图", "infer"),
    (r"训练|微调|让.{0,4}gpu|gpu.{0,4}(转|跑|动)|跑起来|开工|学一下|再学", "train"),
]


def _gpu_reply(text: str) -> dict | None:
    t0 = time.time()
    for pat, act in GPU_RULES:
        if not re.search(pat, text or "", re.I):
            continue
        if act == "stop":
            r = gpu_runner.RUNNER.stop()
            return {"ok": True, "channel": "offline", "say": "好，已经把这个作业停下了。",
                    "state": "idle", "steps": [{"tool": "stop_gpu_job", "ok": bool(r.get("ok")),
                                                "brief": r.get("note", "")}],
                    "elapsed_ms": int((time.time() - t0) * 1000)}
        if act == "status":
            s = gpu_runner.RUNNER.status()
            if s.get("state") == "idle":
                say = "现在手上没有在跑的作业，GPU 空着。要跑就说一声。"
            elif s.get("state") == "running":
                ep = f"{s.get('epoch')}/{s.get('epochs')} 轮" if s.get("epochs") else ""
                say = (f"{s.get('label')}还在跑，{ep}，已经 {s.get('elapsed')} 秒。"
                       f"GPU 现在 {ops_monitor.gpu().get('util')}% 利用率。")
            else:
                m = s.get("metrics") or {}
                bits = [f"{k} {v}" for k, v in m.items() if k in ("mAP50", "mAP50_95", "boxes", "images")]
                cn = {"done": "已经跑完了", "failed": "跑挂了"}.get(s.get("state"), "停了")
                say = (f"{s.get('label')}{cn}，用时 {s.get('elapsed')} 秒"
                       + ("，" + "，".join(bits) if bits else "") + "。")
            return {"ok": True, "channel": "offline", "say": say, "state": "speak",
                    "steps": [{"tool": "job_status", "ok": True, "brief": s.get("state", "")}],
                    "job": s, "elapsed_ms": int((time.time() - t0) * 1000)}
        # train / eval / infer：真起作业
        r = gpu_runner.RUNNER.start(act)
        if not r.get("ok"):
            return {"ok": True, "channel": "offline",
                    "say": "上一个作业还在跑，我不重复起。等它自己停，或者你说停我就停。",
                    "state": "busy", "steps": [{"tool": "start_gpu_job", "ok": False,
                                                "brief": r.get("error", "")}],
                    "job": r.get("job"), "elapsed_ms": int((time.time() - t0) * 1000)}
        label = (r.get("job") or {}).get("label", act)
        p = (r.get("job") or {}).get("params", {})
        extra = f"（{p.get('epochs')} 轮、{p.get('imgsz')} 像素）" if act == "train" else ""
        say = f"好，{label}已经起来了{extra}，GPU 开始转，跑完它自己停，我盯着进度。"
        return {"ok": True, "channel": "offline", "say": say, "state": "busy",
                "steps": [{"tool": "start_gpu_job", "ok": True,
                           "brief": f"{act} job={(r.get('job') or {}).get('job_id')}"}],
                "job": r.get("job"), "elapsed_ms": int((time.time() - t0) * 1000)}
    return None


def chat_offline(message: str, deliver: bool = False) -> dict:
    """不依赖任何模型：按意图干活，回复用剧本里的原话口径。"""
    text = message or ""
    g = _gpu_reply(text)
    if g is not None:
        return g
    intent = _detect_intent(text)
    t0 = time.time()

    # 需要一个“确认/收到”式短回复的场合
    if re.search(r"确认|收到|好的|明白", text) and intent == "unknown":
        return {"ok": True, "channel": "offline", "say": "好的。", "state": "idle",
                "steps": [], "rounds": 0}

    if intent in TASKS:
        # 这是任务交付请求
        meta = TASKS[intent]
        res = deliver_task(intent)
        if "error" in res:
            return {"ok": False, "channel": "offline", "say": f"这个我没做成：{res['error']}",
                    "steps": [{"tool": "write_deliverable", "ok": False, "brief": res["error"]}]}
        say = f"{meta['owner']}同事，{meta['label']}代码已放到你的项目文件夹中，请确认。"
        return {
            "ok": True, "channel": "offline", "say": say, "state": "speak", "mood": "happy",
            "steps": [
                {"tool": "write_deliverable", "ok": True,
                 "brief": f"写入 {res['dir']}，{res['count']} 个文件"},
                {"tool": "git_commit", "ok": bool(res["commit"].get("ok")),
                 "brief": f"commit {res['commit'].get('commit', '')[:40]}"},
            ],
            "delivery": res,
            "notify_cue": meta["notify"],
            "elapsed_ms": int((time.time() - t0) * 1000),
        }

    if intent == "health":
        h = tools.model_health(20)
        c, r = h["confidence"], h["reviews"]
        if h["degraded"]:
            say = "模型性能异常。" + (h["reasons"][0] if h["reasons"] else "")
            state, mood = "alert", "surprised"
        else:
            say = (f"现在没到报警线。近 {c['recent_n']} 个会话平均置信度 {c['recent']:.3f}，"
                   f"较基线 {c['baseline']:.3f} 下滑 {c['drift']:.1%}，阈值 {c['thresh']:.0%}。")
            state, mood = "speak", "relaxed"
        return {"ok": True, "channel": "offline", "say": say, "state": state, "mood": mood,
                "steps": [{"tool": "model_health", "ok": True,
                           "brief": f"degraded={h['degraded']}, drift={c['drift']:.1%}"}],
                "health": h, "elapsed_ms": int((time.time() - t0) * 1000)}

    if intent == "hourly":
        hh = tools.hourly(24)
        say = f"近 24 小时缺陷高峰在 {hh['peak_hour']} 时，{hh['peak_count']} 条。"
        return {"ok": True, "channel": "offline", "say": say, "state": "speak",
                "steps": [{"tool": "hourly", "ok": True, "brief": f"peak={hh['peak_hour']}"}],
                "data": hh, "elapsed_ms": int((time.time() - t0) * 1000)}

    if intent == "overview":
        ov = tools.overview(1)
        top = sorted(ov["classes"].items(), key=lambda kv: -kv[1])[:3]
        tops = "、".join(f"{k} {v} 条" for k, v in top) or "暂无"
        say = f"今天累计 {ov['detections']} 条缺陷记录，{ov['sessions']} 个检测会话。最多的是{tops}。"
        return {"ok": True, "channel": "offline", "say": say, "state": "speak",
                "steps": [{"tool": "overview", "ok": True, "brief": f"{ov['detections']} 条"}],
                "data": ov, "elapsed_ms": int((time.time() - t0) * 1000)}

    if intent == "reviews":
        p = tools.pending_reviews(10)
        say = f"待复核队列还有 {len(p['items'])} 条（本次取最近 10 条查看）。"
        return {"ok": True, "channel": "offline", "say": say, "state": "speak",
                "steps": [{"tool": "pending_reviews", "ok": True, "brief": f"{len(p['items'])} 条"}],
                "data": p, "elapsed_ms": int((time.time() - t0) * 1000)}

    if intent == "detections":
        d = tools.recent_detections(5)
        items = d["items"]
        if items:
            it = items[0]
            say = f"最近一条：{it['ts']} {it['class_cn']}，置信度 {it['confidence']:.3f}。"
        else:
            say = "库里暂时没有检测记录。"
        return {"ok": True, "channel": "offline", "say": say, "state": "speak",
                "steps": [{"tool": "recent_detections", "ok": True, "brief": f"{len(items)} 条"}],
                "data": d, "elapsed_ms": int((time.time() - t0) * 1000)}

    return {
        "ok": True, "channel": "offline",
        "say": "这条我没听出要做什么。你可以说：起草预处理代码 / 查模型性能 / 今天检了多少 / 看缺陷高峰时段。",
        "state": "idle", "steps": [], "intent": "unknown",
    }


def _brief(res) -> str:
    if not isinstance(res, dict):
        return str(res)[:120]
    if "error" in res:
        return f"ERROR {res['error']}"[:140]
    for k in ("count", "degraded", "detections", "commit", "peak_hour"):
        if k in res:
            return f"{k}={res[k]}"[:140]
    return json.dumps(res, ensure_ascii=False, default=str)[:140]


# ══════════════════════════════════════════ 对外统一入口
def chat(message: str, deliver: bool = False, history: list | None = None) -> dict:
    if has_key():
        try:
            res = chat_llm(message, history)
            if res.get("ok") and res.get("say"):
                return res
            # LLM 失败自动降级到离线引擎，保证现场不哑
            fb = chat_offline(message, deliver)
            fb["fallback_from"] = "llm"
            fb["llm_error"] = res.get("error", "")
            return fb
        except Exception as e:
            fb = chat_offline(message, deliver)
            fb["fallback_from"] = "llm"
            fb["llm_error"] = f"{type(e).__name__}: {e}"
            return fb
    return chat_offline(message, deliver)


if __name__ == "__main__":
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8-sig", errors="replace")
    except Exception:
        pass
    print("LLM key:", "已配置" if has_key() else "未配置（走离线意图引擎）")
    print("工具：")
    for t in catalog():
        print(f"  - {t['name']:<20} {t['desc']}")
    print("\n意图测试：")
    for s in ["今天检了多少", "看看模型性能", "起草预处理代码", "缺陷高峰在几点", "你好"]:
        r = chat(s)
        print(f"  Q: {s}\n     -> [{r['channel']}] {r['say'][:80]}")
