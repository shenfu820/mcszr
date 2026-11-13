/* ==========================================================================
   四块屏幕的内容渲染：全部数字来自 03_数据/snapshot.json（真实库聚合）
   ========================================================================== */
(function () {
  'use strict';

  var C = {
    accent: '#d98a3d', accent2: '#e8a05a', text: '#eae8e3', dim: '#9ca2ab',
    faint: '#6d737c', ok: '#a6e3a1', warn: '#f9e2af', danger: '#f38ba8',
    blue: '#89b4fa', cyan: '#89dceb'
  };

  function n0(v) {
    if (v === null || v === undefined || isNaN(v)) return '—';
    return String(v).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }
  function f4(v) { return (v === null || v === undefined || isNaN(v)) ? '—' : Number(v).toFixed(4); }
  function esc(s) { return String(s === null || s === undefined ? '' : s).replace(/[&<>]/g, function (m) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;' }[m];
  }); }
  function pct(a, b) { return b ? (100 * a / b) : 0; }

  function hd(idx, title, sub, state, stateCls) {
    return '<div class="hd"><span class="idx">' + idx + '</span>' +
      '<div><h3>' + esc(title) + '</h3><small>' + esc(sub) + '</small></div>' +
      '<span class="state ' + (stateCls || '') + '">' + esc(state) + '</span></div>';
  }
  function corners() {
    return '<span class="corner tl"></span><span class="corner tr"></span>' +
      '<span class="corner bl"></span><span class="corner br"></span>';
  }
  function ft(html) { return '<div class="ft"><span class="dot"></span><span>' + html + '</span></div>'; }

  /* ---------------- 01 检测系统（真实桌面窗口抓帧） ---------------- */
  function renderInspect(d, snap) {
    var model = (snap.system ? snap.system.model : '—');
    return corners() +
      hd('01', '板材缺陷检测系统', '真实桌面端 run.py · PyQt5 · 权重 ' + model,
        '实时投屏', '') +
      '<div class="bd winbd">' +
        '<div class="winwrap">' +
          '<img id="winShot" class="winshot" alt="桌面端实时窗口">' +
          '<div class="winmask" id="winMask">桌面端未启动（run.py）</div>' +
        '</div>' +
      '</div>' +
      ft('窗口抓帧 <b id="winAt">—</b> · pid <b id="winPid">—</b> · 库内 <b>' +
        n0(d.sessions_total) + ' 场会话 / ' + n0(d.total) + ' 条检测明细</b>');
  }

  /* ---------------- 02 大屏可视化（真实 8770 页面） ---------------- */
  function renderScreen(d, snap) {
    var port = (snap.system ? snap.system.screen_port : 8770);
    return corners() +
      hd('02', '大屏数据可视化', '真实服务 screen_service.py · 127.0.0.1:' + port,
        '实时页面', '') +
      '<div class="bd framebd">' +
        '<div class="framewrap">' +
          '<iframe id="frameScreen" class="liveframe" data-src="http://127.0.0.1:' + port + '/" ' +
          'title="大屏数据可视化" loading="eager"></iframe>' +
          '<div class="framemask" id="maskScreen">正在连接 8770 …</div>' +
        '</div>' +
      '</div>' +
      ft('真实页面 <b>/api/overview</b> 同源直连 · 通道 <b id="stScreen">探测中</b>');
  }

  /* ---------------- 03 六专家复核（真实 8999 对话中枢） ---------------- */
  function renderReview(d, snap) {
    var port = (snap.system ? snap.system.chat_port : 8999);
    return corners() +
      hd('03', '六专家复核 · 人工裁定', '真实服务 angent 对话中枢 · 127.0.0.1:' + port,
        '实时页面', '') +
      '<div class="bd framebd">' +
        '<div class="framewrap zoomwrap">' +
          '<iframe id="frameAgent" class="liveframe zoomed" data-src="http://127.0.0.1:' + port + '/" ' +
          'title="检测对话中枢"></iframe>' +
          '<div class="framemask" id="maskAgent">正在连接 8999 …</div>' +
        '</div>' +
      '</div>' +
      ft('左栏=人工复核队列 · 右栏「蒸馏·自学习」= 真实自学习视图 · 通道 <b id="stAgent">探测中</b>' +
        (d && d.pending ? ' · 待裁定 <b>' + n0(d.pending) + '</b>' : ''));
  }

  /* ---------------- 04 模型自学习 ---------------- */
  function renderLearn(d, snap) {
    var cur = d.current || {};
    var runs = d.runs || [];
    var lastRun = runs[0] || {};
    var delta = (d.gate.promote_delta === null || d.gate.promote_delta === undefined) ? 0 : d.gate.promote_delta;
    var passed = (d.samples >= (d.gate.min_samples || 30));
    var srcTxt = (d.by_source || []).map(function (s) { return s.src + ' ' + s.n; }).join(' / ');

    return corners() +
      hd('04', '模型自学习闭环 · 动画', 'ai_learning_* · 样本池 → 闸门 → 增量训练 → 评估 → 上线',
        passed ? '已过闸门' : '样本不足', passed ? '' : 'off') +
      '<div class="bd">' +
        '<canvas id="learnCanvas"></canvas>' +
        '<div class="learn-facts">' +
          '<div><span>样本池</span><em>' + n0(d.samples) + ' 条（' + esc(srcTxt) + '）</em></div>' +
          '<div><span>最近一轮</span><em>run#' + n0(lastRun.id) + ' · ' + esc(lastRun.status) +
            ' · ' + lastRun.epochs + ' epoch · ' + lastRun.seconds + 's</em></div>' +
          '<div><span>评估闸门</span><em class="mid">mAP50 ' + f4(cur.map50) + ' ≥ 基线 ' +
            f4(cur.baseline_map50) + ' + δ ' + delta + '</em></div>' +
          '<div><span>当前权重</span><em class="good">' + esc((cur.name || '—').slice(0, 26)) + '</em></div>' +
        '</div>' +
      '</div>' +
      ft('动画：链路与数字来自 <b>ai_learning_samples / runs / ai_models</b> · 真实跑批在 <b>8999 → 蒸馏·自学习</b> · 候选 <b>' +
        n0((d.stages || {}).candidate || 0) + '</b> / 归档 <b>' + n0((d.stages || {}).archived || 0) + '</b>');
  }

  /* ---------------- 闭环动画 ---------------- */
  function LearnLoop(canvas, data) {
    this.cv = canvas;
    this.d = data;
    this.t = 0;
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    var w = canvas.clientWidth || 470, h = canvas.clientHeight || 158;
    canvas.width = Math.round(w * this.dpr);
    canvas.height = Math.round(h * this.dpr);
    this.w = w; this.h = h;
    this.ctx = canvas.getContext('2d');
    this.steps = [
      { k: 'pool', label: '样本池', val: data.samples + ' 条' },
      { k: 'gate', label: '样本闸门', val: '≥ ' + data.gate.min_samples },
      { k: 'train', label: '增量训练', val: data.gate.epochs + ' epoch' },
      { k: 'eval', label: '评估', val: '基线+δ' },
      { k: 'ship', label: '上线 / 回滚', val: 'current' }
    ];
    this._loop();
  }
  LearnLoop.prototype._loop = function () {
    var self = this;
    function frame() {
      self.t += 0.016;
      self._draw();
      self._raf = requestAnimationFrame(frame);
    }
    frame();
  };
  LearnLoop.prototype.stop = function () {
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
  };
  LearnLoop.prototype._draw = function () {
    var ctx = this.ctx, dpr = this.dpr, w = this.w, h = this.h, t = this.t;
    var dat = this.d;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    var y = 82, pad = 34, n = this.steps.length;
    var gap = (w - pad * 2) / (n - 1);

    // 轨道
    ctx.strokeStyle = 'rgba(217,138,61,.26)';
    ctx.lineWidth = 1.4;
    ctx.setLineDash([3, 6]);
    ctx.beginPath();
    ctx.moveTo(pad, y);
    ctx.lineTo(w - pad, y);
    ctx.stroke();
    ctx.setLineDash([]);

    // 流动光点
    for (var i = 0; i < 3; i++) {
      var p = ((t * 0.28 + i / 3) % 1);
      var x = pad + (w - pad * 2) * p;
      var grd = ctx.createRadialGradient(x, y, 0, x, y, 7);
      grd.addColorStop(0, 'rgba(232,160,90,.95)');
      grd.addColorStop(1, 'rgba(232,160,90,0)');
      ctx.fillStyle = grd;
      ctx.beginPath();
      ctx.arc(x, y, 7, 0, Math.PI * 2);
      ctx.fill();
    }

    // 节点
    for (var j = 0; j < n; j++) {
      var cx = pad + gap * j;
      var step = this.steps[j];
      var ph = Math.sin(t * 1.6 + j * 0.9) * 0.5 + 0.5;
      var active = j === 1 ? (dat.samples >= (dat.gate.min_samples)) : true;

      ctx.beginPath();
      ctx.arc(cx, y, 13, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(16,17,21,.95)';
      ctx.fill();
      ctx.lineWidth = 1.6;
      ctx.strokeStyle = active ? 'rgba(232,160,90,' + (0.5 + 0.4 * ph) + ')' : 'rgba(156,162,171,.5)';
      ctx.stroke();

      ctx.beginPath();
      ctx.arc(cx, y, 4.2, 0, Math.PI * 2);
      ctx.fillStyle = active ? 'rgba(232,160,90,' + (0.55 + 0.45 * ph) + ')' : 'rgba(156,162,171,.7)';
      ctx.fill();

      // 标签
      ctx.textAlign = 'center';
      ctx.fillStyle = 'rgba(234,232,227,.92)';
      ctx.font = '11.5px "Microsoft YaHei UI",sans-serif';
      ctx.fillText(step.label, cx, y - 24);
      ctx.fillStyle = 'rgba(156,162,171,.95)';
      ctx.font = '10px "DIN Alternate",ui-monospace,monospace';
      ctx.fillText(step.val, cx, y + 30);
    }

    // 运行记录：画成右侧的小折线，不写字，避免和下方事实行抢位置
    var runs = (dat.runs || []).slice().reverse();
    if (runs.length > 1) {
      var rx0 = w - pad - 96, rx1 = w - pad, ry0 = 24, ry1 = 46;
      ctx.strokeStyle = 'rgba(137,180,250,.85)';
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      var lo = Math.min.apply(null, runs.map(function (r) { return r.map50 || 0; }));
      var hi = Math.max.apply(null, runs.map(function (r) { return r.map50 || 0; }));
      var span = Math.max(0.01, hi - lo);
      runs.forEach(function (r, i2) {
        var x = rx0 + (rx1 - rx0) * i2 / (runs.length - 1);
        var yy = ry1 - (ry1 - ry0) * (((r.map50 || 0) - lo) / span);
        if (i2 === 0) ctx.moveTo(x, yy); else ctx.lineTo(x, yy);
      });
      ctx.stroke();
      ctx.fillStyle = 'rgba(137,180,250,.9)';
      ctx.beginPath();
      ctx.arc(rx1, ry1 - (ry1 - ry0) * (((runs[runs.length - 1].map50 || 0) - lo) / span), 2.4, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = 'rgba(109,115,124,.95)';
      ctx.textAlign = 'right';
      ctx.font = '9px "DIN Alternate",ui-monospace,monospace';
      ctx.fillText('run#' + runs[0].id + '→#' + runs[runs.length - 1].id, rx1, 16);
    }
  };

  window.Panels = {
    renderInspect: renderInspect,
    renderScreen: renderScreen,
    renderReview: renderReview,
    renderLearn: renderLearn,
    LearnLoop: LearnLoop,
    colors: C
  };
})();
