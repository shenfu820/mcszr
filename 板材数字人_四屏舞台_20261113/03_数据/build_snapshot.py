# -*- coding: utf-8 -*-
"""四屏舞台数据构建器（只读）。

用途：从 **真实** new mcjc 库 + config/settings.json + 8770 接口，算出四块屏幕要显示的
每一个数字，写成 03_数据/snapshot.json 与 snapshot.js（file:// 兜底用）。
不写任何产线文件，不写库。

运行：
    E:\\conda3\\envs\\yoloces\\python.exe 03_数据\\build_snapshot.py
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(PKG)              # 数字人 根（config/paths.ini、_tools/dh_env.py 在这）


def _ini_get(key: str, default: str = '') -> str:
    p = os.path.join(ROOT, 'config', 'paths.ini')
    try:
        with open(p, 'r', encoding='utf-8', errors='replace') as f:
            for raw in f:
                s = raw.strip()
                if not s or s[0] in ';#[' or '=' not in s:
                    continue
                k, v = s.split('=', 1)
                if k.strip().upper() == key:
                    return v.strip() or default
    except Exception:                        # noqa: BLE001
        pass
    return default


def _detect_proj() -> str:
    """产线目录：环境变量 DH_PROD > config/paths.ini 的 PROD_ROOT > _tools/dh_env.py 自动探测。

    换台电脑（没装 new mcjc）时这里会返回空串，下面的 compute_snapshot 就吐「未部署」空快照，
    而不是抛异常把 8790 打成 500。
    """
    for cand in (os.environ.get('DH_PROD'), _ini_get('PROD_ROOT')):
        if cand and os.path.isdir(cand):
            return cand
    try:
        import importlib.util as _il
        from pathlib import Path as _P
        spec = _il.spec_from_file_location('dh_env_bs', os.path.join(ROOT, '_tools', 'dh_env.py'))
        if spec is None:
            return ''
        mod = _il.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cfg = mod.read_ini(_P(os.path.join(ROOT, 'config', 'paths.ini')))
        return (mod.resolve(_P(ROOT), cfg) or {}).get('DH_PROD') or ''
    except Exception:                        # noqa: BLE001
        return ''


def _base_url(env_name: str, ini_key: str, port: int) -> str:
    v = (os.environ.get(env_name) or _ini_get(ini_key) or '').strip()
    return (v.rstrip('/') if v else 'http://127.0.0.1:%d' % port)


PROJ = _detect_proj()
DB = os.path.join(PROJ, 'data', 'wood_inspect.db') if PROJ else ''
SETTINGS = os.path.join(PROJ, 'config', 'settings.json') if PROJ else ''
DETECTED_DIR = os.path.join(PROJ, 'output', 'detected') if PROJ else ''
OUT_DATA = os.path.join(PKG, '03_数据')
OUT_ASSETS = os.path.join(PKG, '02_四屏舞台', 'assets')
OUT_SCRIPT = os.path.join(PKG, '04_台词配音')
SCREEN_BASE = _base_url('DH_SCREEN_URL', 'SCREEN_URL', 8770)
AGENT_BASE = _base_url('DH_AGENT_URL', 'AGENT_URL', 8999)
SCREEN_API = SCREEN_BASE + '/api/overview'
SCREEN_HEALTH = SCREEN_BASE + '/api/health'

CN_COLOR = {          # 与 wood_inspect/taxonomy.py 类别一一对应，颜色仅本舞台使用
    'dry_knot': '#d98a3d', 'sound_knot': '#89b4fa', 'edge_knot': '#a6e3a1',
    'small_knot': '#f9e2af', 'split': '#f38ba8', 'wave': '#89dceb',
}
EXPERT_CN = {
    'geometry': '形态', 'texture': '纹理', 'context': '上下文',
    'temporal': '时序', 'knowledge': '知识库', 'feature': '特征',
}


def load_settings() -> dict:
    with open(SETTINGS, 'r', encoding='utf-8') as f:
        return json.load(f)


def http_json(url, timeout=6):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception as e:                      # noqa: BLE001
        return {'__error__': '%s: %s' % (type(e).__name__, e)}


def rgb(hexstr):
    c = hexstr.lstrip('#')
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


# --------------------------------------------------------------------------
# 1) 检测系统面板：detections / sessions 真值
# --------------------------------------------------------------------------
def build_inspect(cur, cfg):
    total, sessions, avg_conf, max_conf, first_ts, last_ts = cur.execute(
        'select count(*), count(distinct session_id), avg(confidence), max(confidence), '
        'min(ts), max(ts) from detections').fetchone()
    by_class = []
    for en, cn, n, c_avg in cur.execute(
            'select class_en, class_cn, count(*), avg(confidence) from detections '
            'group by class_en order by count(*) desc'):
        by_class.append({
            'en': en, 'cn': cn, 'n': n,
            'pct': round(100.0 * n / total, 1) if total else 0.0,
            'conf': round(c_avg or 0, 4),
            'color': CN_COLOR.get(en, '#9ca2ab'),
        })

    last = cur.execute(
        'select id, started_at, ended_at, source_name, model_name, total_frames, '
        'defect_frames, total_defects, defect_kinds, max_conf, avg_conf, duration_ms '
        'from sessions order by id desc limit 1').fetchone()
    last_session = dict(zip(
        ['id', 'started_at', 'ended_at', 'source_name', 'model_name', 'total_frames',
         'defect_frames', 'total_defects', 'defect_kinds', 'max_conf', 'avg_conf',
         'duration_ms'], last)) if last else {}

    recent = []
    for row in cur.execute(
            'select ts, class_cn, class_en, confidence, width, height, source '
            'from detections order by id desc limit 5'):
        recent.append({'ts': row[0], 'cn': row[1], 'en': row[2], 'conf': round(row[3], 4),
                       'w': row[4], 'h': row[5], 'src': row[6]})

    sessions_total = cur.execute('select count(*) from sessions').fetchone()[0]
    return {
        'total': total,
        'sessions': sessions,                 # 有缺陷的会话数（detections 里出现过的 session_id）
        'sessions_total': sessions_total,     # sessions 表会话总数
        'avg_conf': round(avg_conf or 0, 4),
        'max_conf': round(max_conf or 0, 4),
        'first_ts': first_ts, 'last_ts': last_ts,
        'by_class': by_class,
        'last_session': last_session,
        'recent': recent,
        'params': {k: cfg.get(k) for k in ('conf', 'iou', 'imgsz', 'tile_size',
                                           'dedup_window_ms', 'dedup_iou', 'max_det')},
        'model_path': cfg.get('model_path'),
    }


# --------------------------------------------------------------------------
# 2) 大屏面板：sessions 聚合（与 screen_service /api/overview 同名字段）
# --------------------------------------------------------------------------
def build_screen(cur, cfg):
    n_sess, frames, d_frames, defects, max_conf, avg_conf = cur.execute(
        'select count(*), sum(total_frames), sum(defect_frames), sum(total_defects), '
        'max(max_conf), avg(avg_conf) from sessions').fetchone()
    models = [r[0] for r in cur.execute(
        'select distinct model_name from sessions where model_name is not null')]

    days = []
    last_day = cur.execute('select max(date(started_at)) from sessions').fetchone()[0]
    if last_day:
        base = datetime.strptime(last_day, '%Y-%m-%d')
        for i in range(6, -1, -1):
            d = (base - timedelta(days=i)).strftime('%Y-%m-%d')
            s, df, dd, ac = cur.execute(
                "select count(*), sum(defect_frames), sum(total_defects), avg(avg_conf) "
                "from sessions where date(started_at)=?", (d,)).fetchone()
            days.append({'day': d, 'sessions': s or 0, 'defectFrames': df or 0,
                         'defects': dd or 0, 'avgConf': round(ac or 0, 4)})

    # 8770 通道探测（只探测，不改它）
    health = http_json(SCREEN_HEALTH)
    ov = http_json(SCREEN_API)
    screen_online = health.get('code') == 200
    live_sessions = None
    if ov.get('code') == 200:
        try:
            live_sessions = ov['data']['kpi']['sessions']
        except Exception:                        # noqa: BLE001
            live_sessions = None

    db_size_kb = round(os.path.getsize(DB) / 1024.0, 1)
    node_defs = [
        ('推理引擎',
         '正常' if cfg.get('model_path') and os.path.exists(os.path.join(PROJ, cfg['model_path'])) else '异常',
         '%s ｜ conf %s iou %s' % (os.path.basename(str(cfg.get('model_path'))), cfg.get('conf'), cfg.get('iou'))),
        ('检测会话', '离线', '桌面端未在推送（大屏 live.desktop_online=false 时同此）'),
        ('检测数据库', '正常', 'SQLite ｜ %.1f MB ｜ %d 会话 / %d 缺陷点' % (db_size_kb / 1024.0, n_sess, defects or 0)),
        ('声光报警', '就绪' if cfg.get('alarm_enabled') else '停用',
         'RS485 ｜ %s ｜ 保持 %ss' % (cfg.get('alarm_port'), cfg.get('alarm_duration_sec'))),
        ('采集输入源', '就绪', '%s ｜ %s' % (cfg.get('source_mode'), cfg.get('cam_expect_ip'))),
        ('大屏通道', '正常' if screen_online else '离线',
         'SSE ｜ %s' % SCREEN_API.split('//', 1)[-1]),
    ]
    nodes = [{'name': n, 'status': s, 'detail': d} for n, s, d in node_defs]
    return {
        'sessions': n_sess,
        'frames': frames or 0,
        'defect_frames': d_frames or 0,
        'defects': defects or 0,
        'max_conf': round(max_conf or 0, 4),
        'avg_conf': round(avg_conf or 0, 4),
        'models': models,
        'daily': days,
        'nodes': nodes,
        'nodes_online': sum(1 for n in nodes if n['status'] in ('正常', '就绪')),
        'nodes_total': len(nodes),
        'screen_api': {
            'url': SCREEN_API,
            'online': screen_online,
            'version': (health.get('data') or {}).get('version') if screen_online else None,
            'pid': (health.get('data') or {}).get('pid') if screen_online else None,
            'reported_sessions': live_sessions,
            'local_sessions': n_sess,
            # 若两端口径不同，说明在跑的 8770 实例读的不是本库，舞台只用它做"通道在线"判断
            'consistent': (live_sessions == n_sess) if live_sessions is not None else None,
        },
    }


# --------------------------------------------------------------------------
# 3) 六专家复核面板：ai_reviews
# --------------------------------------------------------------------------
def build_review(cur, cfg):
    total, needs_human, pending = cur.execute(
        "select count(*), sum(needs_human), sum(case when needs_human=1 and human_state='pending' then 1 else 0 end) "
        'from ai_reviews').fetchone()
    avg_agree, avg_anom = cur.execute(
        'select avg(agree_ratio), avg(anomaly_score) from ai_reviews').fetchone()
    dec = dict(cur.execute('select decision, count(*) from ai_reviews group by decision').fetchall())

    experts = {k: {'key': k, 'cn': v, 'confirm': 0, 'reject': 0, 'uncertain': 0, 'abstain': 0, 'n': 0}
               for k, v in EXPERT_CN.items()}
    reasons = {'split': 0, 'few_votes': 0, 'low_conf': 0, 'empty_panel': 0}
    conf_floor = float(cfg.get('ai_review_conf_floor') or 0.5)
    min_voters = int(cfg.get('ai_review_min_voters') or 2)
    for (opinions, consensus, voter_n, confidence) in cur.execute(
            'select opinions, consensus, voter_n, confidence from ai_reviews'):
        try:
            ops = json.loads(opinions) if opinions else []
        except Exception:                        # noqa: BLE001
            ops = []
        if not ops:
            reasons['empty_panel'] += 1
        for o in ops:
            k = o.get('expert')
            if k not in experts:
                continue
            experts[k]['n'] += 1
            v = (o.get('verdict') or '').lower()
            if v in ('confirm', 'reject', 'uncertain', 'abstain'):
                experts[k][v] += 1
        if consensus == 'split':
            reasons['split'] += 1
        if (voter_n or 0) < min_voters:
            reasons['few_votes'] += 1
        if (confidence or 0) < conf_floor:
            reasons['low_conf'] += 1

    recent = []
    for row in cur.execute(
            'select id, ts, class_cn, confidence, decision, agree_ratio, panel_size, '
            'needs_human, voter_n, abstain_n, human_state from ai_reviews order by id desc limit 4'):
        recent.append({'id': row[0], 'ts': row[1], 'cn': row[2], 'conf': round(row[3], 4),
                       'decision': row[4], 'agree': round(row[5] or 0, 3), 'panel': row[6],
                       'needs_human': row[7], 'voters': row[8], 'abstain': row[9],
                       'state': row[10]})
    return {
        'total': total, 'needs_human': needs_human or 0, 'pending': pending or 0,
        'confirmed': dec.get('confirmed', 0), 'escalate': dec.get('escalate', 0),
        'avg_agree': round(avg_agree or 0, 4), 'avg_anomaly': round(avg_anom or 0, 4),
        'experts': [experts[k] for k in EXPERT_CN],
        'reasons': reasons,
        'params': {'conf_floor': conf_floor, 'min_voters': min_voters,
                   'abstain_policy': cfg.get('ai_review_abstain_policy'),
                   'experts': cfg.get('ai_review_experts')},
        'recent': recent,
    }


# --------------------------------------------------------------------------
# 4) 模型自学习面板：ai_learning_* / ai_models
# --------------------------------------------------------------------------
def build_selflearn(cur, cfg):
    n_samples = cur.execute('select count(*) from ai_learning_samples').fetchone()[0]
    unused = cur.execute('select count(*) from ai_learning_samples where used_run is null').fetchone()[0]
    by_source = [{'src': s, 'n': n} for s, n in cur.execute(
        'select label_source, count(*) from ai_learning_samples group by label_source order by 2 desc')]
    by_class = [{'cn': c, 'n': n} for c, n in cur.execute(
        'select class_cn, count(*) from ai_learning_samples group by class_cn order by 2 desc')]

    runs = []
    for rid, started, trig, sn, epochs, status, metrics in cur.execute(
            'select id, started_at, trigger, samples_n, epochs, status, metrics '
            'from ai_learning_runs order by id desc limit 6'):
        m = {}
        try:
            m = json.loads(metrics) if metrics else {}
        except Exception:                        # noqa: BLE001
            pass
        runs.append({'id': rid, 'started_at': started, 'trigger': trig, 'samples': sn,
                     'epochs': epochs, 'status': status,
                     'map50': round(m.get('map50', 0) or 0, 4),
                     'map50_95': round(m.get('map50_95', 0) or 0, 4),
                     'seconds': round(m.get('seconds', 0) or 0, 2),
                     'reason': (m.get('reason') or '')[:120]})

    cur_row = cur.execute("select id, name, metrics, created_at, note from ai_models "
                          "where stage='current' order by id desc limit 1").fetchone()
    current = {}
    if cur_row:
        m = {}
        try:
            m = json.loads(cur_row[2]) if cur_row[2] else {}
        except Exception:                        # noqa: BLE001
            pass
        current = {'id': cur_row[0], 'name': cur_row[1], 'created_at': cur_row[3],
                   'note': cur_row[4], 'map50': round(m.get('map50', 0) or 0, 4),
                   'baseline_map50': round(m.get('baseline_map50', 0) or 0, 4),
                   'promote_delta': m.get('promote_delta', cfg.get('ai_selflearn_promote_delta')),
                   'precision': m.get('precision'), 'recall': m.get('recall')}
    stages = dict(cur.execute('select stage, count(*) from ai_models group by stage').fetchall())
    return {
        'samples': n_samples, 'unused': unused,
        'by_source': by_source, 'by_class': by_class,
        'runs': runs, 'current': current, 'stages': stages,
        'gate': {'min_samples': cfg.get('ai_selflearn_min_samples'),
                 'epochs': cfg.get('ai_selflearn_epochs'),
                 'promote_delta': cfg.get('ai_selflearn_promote_delta'),
                 'auto': cfg.get('ai_selflearn_auto'),
                 'enabled': cfg.get('ai_selflearn_enabled')},
    }


# --------------------------------------------------------------------------
# 5) 检测结果缩略图（真实 det_*.jpg + 同目录 json 的检出）
# --------------------------------------------------------------------------
def build_preview(cur):
    if not os.path.isdir(DETECTED_DIR):
        return None
    best = None
    for root, _dirs, files in os.walk(DETECTED_DIR):
        for f in files:
            if not f.lower().endswith('.jpg'):
                continue
            p = os.path.join(root, f)
            mt = os.path.getmtime(p)
            if best is None or mt > best[0]:
                best = (mt, p)
    if not best:
        return None
    jpg = best[1]
    meta_p = os.path.splitext(jpg)[0] + '.json'
    meta = {}
    if os.path.exists(meta_p):
        try:
            with open(meta_p, 'r', encoding='utf-8') as f:
                meta = json.load(f)
        except Exception:                        # noqa: BLE001
            meta = {}
    os.makedirs(OUT_ASSETS, exist_ok=True)
    out = os.path.join(OUT_ASSETS, 'detected_preview.jpg')

    from PIL import Image
    im = Image.open(jpg).convert('RGB')
    W, H = im.size
    dets = meta.get('detections') or []
    if dets:
        xs = [d['bbox'][0] for d in dets] + [d['bbox'][2] for d in dets]
        ys = [d['bbox'][1] for d in dets] + [d['bbox'][3] for d in dets]
        cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
        span_x = max(1200.0, (max(xs) - min(xs)) * 1.8)
        span_y = max(1600.0, (max(ys) - min(ys)) * 1.35)
        # 目标比例 3:4
        if span_x / span_y > 0.75:
            span_y = span_x / 0.75
        else:
            span_x = span_y * 0.75
        box = (int(max(0, cx - span_x / 2)), int(max(0, cy - span_y / 2)),
               int(min(W, cx + span_x / 2)), int(min(H, cy + span_y / 2)))
        im = im.crop(box)
    im = im.resize((450, 600), Image.LANCZOS)
    im.save(out, quality=88)
    return {
        'image': 'assets/detected_preview.jpg',
        'source_image': jpg,
        'ts': meta.get('ts'),
        'session_id': meta.get('session_id'),
        'frame': meta.get('frame'),
        'board': meta.get('source'),
        'model': meta.get('model'),
        'backend': meta.get('backend'),
        'conf': meta.get('conf'), 'iou': meta.get('iou'),
        'count': meta.get('count'),
        'dets': [{'cn': d.get('cn'), 'en': d.get('en'), 'conf': d.get('conf'),
                  'bbox': d.get('bbox')} for d in dets],
        'image_wh': [W, H],
    }


# --------------------------------------------------------------------------
# 台词：数字全部来自上面算出的真值，避免"台上说的和库里不一样"
# --------------------------------------------------------------------------
def build_script(snap):
    ins, scr, rev, sl = snap['inspect'], snap['screen'], snap['review'], snap['selflearn']
    pv = snap.get('preview') or {}
    det = pv.get('dets') or []
    det_txt = '、'.join('%s %.3f' % (d['cn'], d['conf']) for d in det[:3]) if det else '暂无'
    gate = sl['gate']
    passed = '已过闸' if sl['samples'] >= (gate['min_samples'] or 30) else '未过闸'
    cur_m = sl.get('current') or {}
    lines = [
        {'id': 'intro', 'panel': None, 'text':
         '我是板材表面缺陷检测项目的数字工程师。我周围这四块屏，放的都是正在跑的系统的本体：'
         '检测系统、大屏可视化、对话中枢，还有一块模型自学习的闭环动画。'},
        {'id': 'inspect', 'panel': 'inspect', 'text':
         '第一块，就是桌面端检测程序本身，实时窗口抓帧，不是重画的图。'
         '现在库里累计 %d 条缺陷标注，%d 场检测会话里有 %d 场检出过缺陷，平均置信度 %.4f，最高 %.4f。'
         '最近这一板检出 %d 处：%s。' % (
             ins['total'], ins['sessions_total'], ins['sessions'], ins['avg_conf'],
             ins['max_conf'], pv.get('count') or 0, det_txt)},
        {'id': 'screen', 'panel': 'screen', 'text':
         '第二块，就是大屏系统的页面本体，8770 上的实时数据。'
         '目前 %d 场会话共 %d 帧、其中缺陷帧 %d 帧，累计缺陷点 %d 个；六个系统节点在线 %d 个。' % (
             scr['sessions'], scr['frames'], scr['defect_frames'], scr['defects'], scr['nodes_online'])},
        {'id': 'review', 'panel': 'review', 'text':
         '第三块是对话中枢，左边就是人工复核队列。'
         '库里 %d 条复核记录，%d 条转人工，其中 %d 条还在等人裁定，平均一致率 %.4f；弃权不算反对票。' % (
             rev['total'], rev['needs_human'], rev['pending'], rev['avg_agree'])},
        {'id': 'learn', 'panel': 'learn', 'text':
         '第四块是模型自学习闭环，链路和数字都来自自学习相关的表：样本池 %d 条，%s；'
         '最近一次增量训练 mAP50 %.4f，基线 %.4f，闸门要求基线加 %.3f，判定结果：%s。'
         '要真跑一次，就在对话中枢里点蒸馏自学习。' % (
             sl['samples'], passed, cur_m.get('map50', 0), cur_m.get('baseline_map50', 0),
             gate['promote_delta'] or 0, '晋升为 current' if cur_m else '未上线')},
        {'id': 'outro', 'panel': None, 'text':
         '三个系统都在跑，第四块是自己迭代模型的闭环。屏上每个数字都能回库核对。'},
    ]
    os.makedirs(OUT_SCRIPT, exist_ok=True)
    payload = {'persona': {'name': '小星', 'role': '板材检测项目数字工程师',
                           'voice': 'zh-CN-YunxiNeural'},
               'audio_dir': 'voice', 'voice_enabled': True,
               'lines': lines}
    p = os.path.join(OUT_SCRIPT, 'script.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    pjs = os.path.join(OUT_SCRIPT, 'script.js')
    with open(pjs, 'w', encoding='utf-8') as f:
        f.write('window.__STAGE_SCRIPT__ = ')
        json.dump(payload, f, ensure_ascii=False)
        f.write(';\n')
    return p


def _zeroize(o):
    """把快照模板里的数字/文字清零但保留结构：前端是按字段名读的，缺字段会报错。"""
    if isinstance(o, bool):
        return False
    if isinstance(o, dict):
        return {k: _zeroize(v) for k, v in o.items()}
    if isinstance(o, list):
        return []
    if isinstance(o, (int, float)):
        return 0
    if isinstance(o, str):
        return ''
    return o


def _stub_snapshot():
    """本机没接产线系统时用的空快照：结构照旧、数字全 0、带一句人话说明。

    模板取 03_数据/snapshot.json（仓库里带了一份真快照），所以字段名不会漂。
    """
    try:
        with open(os.path.join(OUT_DATA, 'snapshot.json'), 'r', encoding='utf-8') as f:
            snap = _zeroize(json.load(f))
    except Exception:                        # noqa: BLE001
        snap = {}
    snap.update({
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'deployed': False,
        'note': ('本机没有接产线系统（PROD_ROOT / DH_PROD 指向的 new mcjc 不存在），'
                 '四块屏的数字显示 0。要连产线机：在 config/paths.ini 写 PROD_ROOT=、'
                 'SCREEN_URL=、AGENT_URL=（见 docs/跨机器部署.md「只部署屏幕」）'),
        'db': {'path': '', 'size_kb': 0, 'mtime': ''},
    })
    sysd = snap.setdefault('system', {})
    sysd.update({'title': '（本机未接产线系统）',
                 'title_en': 'Production system not deployed on this PC',
                 'model_exists': False})
    return snap


def compute_snapshot(make_preview=True):
    """算出四屏要用的全部数据（只读库）。make_preview=False 时不重写缩略图，
    供 05_服务/stage_server.py 每 5 秒刷新用。"""
    if not (PROJ and os.path.isfile(DB) and os.path.isfile(SETTINGS)):
        return _stub_snapshot()          # 换台没装产线的电脑：给空快照，别抛异常
    cfg = load_settings()
    con = sqlite3.connect('file:' + DB + '?mode=ro', uri=True)
    cur = con.cursor()
    snap = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'deployed': True,
        'system': {
            'title': cfg.get('user_title'), 'title_en': cfg.get('user_title_en'),
            'theme': cfg.get('theme'), 'model': os.path.basename(str(cfg.get('model_path'))),
            'model_exists': os.path.exists(os.path.join(PROJ, str(cfg.get('model_path')))),
            'conf': cfg.get('conf'), 'iou': cfg.get('iou'), 'imgsz': cfg.get('imgsz'),
            'screen_port': cfg.get('screen_port'), 'chat_port': cfg.get('chat_port'),
            'alarm_port': cfg.get('alarm_port'),
        },
        'db': {'path': DB, 'size_kb': round(os.path.getsize(DB) / 1024.0, 1),
               'mtime': datetime.fromtimestamp(os.path.getmtime(DB)).strftime('%Y-%m-%d %H:%M:%S')},
        'inspect': build_inspect(cur, cfg),
        'screen': build_screen(cur, cfg),
        'review': build_review(cur, cfg),
        'selflearn': build_selflearn(cur, cfg),
        'preview': build_preview(cur) if make_preview else _preview_meta(),
    }
    con.close()
    return snap


def _preview_meta():
    """不重写缩略图时，沿用上一次构建写下的 preview 记录。"""
    try:
        with open(os.path.join(OUT_DATA, 'snapshot.json'), 'r', encoding='utf-8') as f:
            return json.load(f).get('preview')
    except Exception:                            # noqa: BLE001
        return build_preview()


def main():
    snap = compute_snapshot(make_preview=True)
    if snap.get('deployed') is False and '--force' not in sys.argv:
        print('本机没有接产线系统：这次不写 snapshot（免得把 0 覆盖进仓库里的模板快照）。')
        print('  ' + str(snap.get('note', '')))
        print('  确认要写空快照就加 --force。')
        return 1

    os.makedirs(OUT_DATA, exist_ok=True)
    pj = os.path.join(OUT_DATA, 'snapshot.json')
    with open(pj, 'w', encoding='utf-8') as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    pjs = os.path.join(OUT_DATA, 'snapshot.js')
    with open(pjs, 'w', encoding='utf-8') as f:
        f.write('window.__STAGE_SNAPSHOT__ = ')
        json.dump(snap, f, ensure_ascii=False)
        f.write(';\n')
    ps = build_script(snap)
    print('written', pj, os.path.getsize(pj))
    print('written', pjs, os.path.getsize(pjs))
    print('written', ps, os.path.getsize(ps))
    print('—— 关键真值 ——')
    print(' 检测系统: total=%s sessions=%s avg_conf=%s max_conf=%s' % (
        snap['inspect']['total'], snap['inspect']['sessions'],
        snap['inspect']['avg_conf'], snap['inspect']['max_conf']))
    print(' 大屏口径: sessions=%s frames=%s defect_frames=%s defects=%s nodes=%s/%s' % (
        snap['screen']['sessions'], snap['screen']['frames'], snap['screen']['defect_frames'],
        snap['screen']['defects'], snap['screen']['nodes_online'], snap['screen']['nodes_total']))
    print(' 六专家: total=%s needs_human=%s pending=%s avg_agree=%s' % (
        snap['review']['total'], snap['review']['needs_human'], snap['review']['pending'],
        snap['review']['avg_agree']))
    print(' 自学习: samples=%s current=%s' % (
        snap['selflearn']['samples'], snap['selflearn']['current'].get('name')))
    print(' 预览图: %s dets=%s' % ((snap['preview'] or {}).get('source_image'),
                               (snap['preview'] or {}).get('count')))


if __name__ == '__main__':
    sys.exit(main())
