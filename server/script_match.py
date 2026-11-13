# -*- coding: utf-8 -*-
"""
小星 · 剧本接话（上下文匹配）
=============================
现场真正要的是：**同事按剧本念上一句，小星接住下一句**，而不是靠关键词瞎猜。

做法：
  1) 把 script.json 里每条 cue 的 trigger（触发条件）当作"上一句"；
     其中很多 trigger 就是同事的原始台词，可以直接做文本相似度匹配。
  2) 顺序先验：当前停在剧本第 i 条时，第 i+1 条加分（同事往往会顺着往下说）。
  3) 对上就播**预渲染原声**（/voice/<cue>.mp3），和点控制台按钮的效果一模一样；
     对不上才交给意图引擎（查数、起 GPU 作业）。

对外：
  match(text) -> {"cue":..., "score":..., "why":...}  或 None
  current()/goto(cue_id)/step(n)
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_FILE = ROOT / "script" / "script.json"

# 相似度阈值：实测剧本原句 ≈0.75~0.95，ASR 带错字 ≈0.55~0.75，无关问句 <0.35
THRESH = 0.45
MIN_LEN = 6          # 太短的句子（"好的""收到"）不做匹配，交给顺序推进
STATE = {"idx": -1}  # 当前停在第几条（-1 表示还没开始）

# 有些 cue 的"上一句"不是一句台词，而是一段要素/一组数字，用关键词认最稳。
# 关键词直接写在 script.json 的 keywords 字段里，这里只保留"需要命中几次"的特殊约定。
KEYWORD_MIN_HITS = {"received": 2}          # 六要素至少要念到两个要素名
EXTRA_KEYWORDS = {
    "alert_abnormal": [r"准确率.{0,8}(下降|下滑|掉下来)", r"百分之八十九点七", r"89\.7"],
}


def _norm(s: str) -> str:
    s = (s or "").lower()
    return "".join(ch for ch in s if ch.isalnum())


def _cues() -> list:
    try:
        return json.loads(SCRIPT_FILE.read_text(encoding="utf-8")).get("cues", [])
    except Exception:
        return []


def _sim(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if len(na) >= 5 and (na in nb or nb in na):
        return 0.92
    return SequenceMatcher(None, na, nb).ratio()


def current() -> dict:
    cues = _cues()
    i = STATE["idx"]
    if 0 <= i < len(cues):
        return {"index": i, "id": cues[i]["id"], "scene": cues[i].get("scene", ""),
                "text": cues[i].get("text", "")}
    return {"index": -1, "id": None, "scene": "", "text": ""}


def goto(cue_id: str) -> dict:
    cues = _cues()
    for i, c in enumerate(cues):
        if c["id"] == cue_id:
            STATE["idx"] = i
            return current()
    return current()


def step(n: int = 1) -> dict:
    cues = _cues()
    if not cues:
        return current()
    STATE["idx"] = min(len(cues) - 1, max(-1, STATE["idx"] + n))
    return current()


def match(text: str) -> dict | None:
    """同事说了上一句 → 找出该接的那一条。"""
    cues = _cues()
    if not cues or len(_norm(text)) < MIN_LEN:
        return None

    # 0) 关键词规则：优先用 script.json 里写死的 keywords（确定性，不猜）
    for i, c in enumerate(cues):
        kws = [k for k in (c.get("keywords") or []) if k]
        kws += EXTRA_KEYWORDS.get(c["id"], [])
        if not kws:
            continue
        hits = sum(1 for k in kws if re.search(re.escape(k) if not k.startswith("[r") else k, text))
        need = KEYWORD_MIN_HITS.get(c["id"], 1)
        if hits >= need:
            STATE["idx"] = i
            return {"index": i, "cue": c, "score": 0.8, "why": f"关键词命中×{hits}"}

    best = None
    for i, c in enumerate(cues):
        # trigger 里可能有"杨飞越：xxx"这种前缀，去掉人名再比
        trig = c.get("trigger", "") or ""
        trig_body = re.sub(r"^[^：:]{1,6}[：:]", "", trig).strip()
        s = max(_sim(text, trig), _sim(text, trig_body))
        if not trig_body:
            s = max(s, _sim(text, c.get("text", "")) * 0.0)   # 没 trigger 文本就不猜
        # 顺序先验：紧接着当前这条的下一句，给一点加成（不改变明显更优的匹配）
        if i == STATE["idx"] + 1:
            s += 0.12
        if best is None or s > best["score"]:
            best = {"index": i, "cue": c, "score": round(min(s, 1.0), 3),
                    "why": (trig_body or trig)[:40]}

    if best and best["score"] >= THRESH:
        STATE["idx"] = best["index"]
        return best
    return None


ADVANCE_RE = re.compile(r"继续|下一句|下一个|往下|该你了|说下一句|接着来|next")


def advance(text: str) -> dict | None:
    """口令：「继续 / 下一句 / 往下 / 该你了」→ 按剧本顺序说下一条。

    这是"每一句都能说出来"的最后一道保险：不管同事有没有念对上一句，
    一句口令就能推到下一条，用来走位、补漏、救场都行。
    """
    if not ADVANCE_RE.search(_norm(text)):
        return None
    cues = _cues()
    if not cues:
        return None
    STATE["idx"] = min(len(cues) - 1, STATE["idx"] + 1)
    c = cues[STATE["idx"]]
    return {"index": STATE["idx"], "cue": c, "score": 1.0, "why": "口令推进"}


def advance_words(text: str) -> bool:
    return advance(text) is not None


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cues = _cues()
    print(f"剧本 {len(cues)} 条")
    for q in (sys.argv[1:] or [
        "5号同事，在我进行第一步数据采集的同时，请你帮助我起草第二步数据预处理代码",
        "好的，接下来请你配合其他同事开发",
        "我需要你协助我完成模型优化，我将通过网页端下发优化指令",
        "收到，5号同事，我来处理",
        "请各位同事配合汇报项目进展",
        "今天一共检出了多少条缺陷",
        "让GPU跑一下训练",
    ]):
        m = match(q)
        print(f"  「{q[:34]}…」 -> " + (f"{m['cue']['id']} score={m['score']}" if m else "无匹配"))
