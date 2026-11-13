/* ==========================================================================
   四屏舞台总装：布局 / 人物 / 四块屏 / 数据源 / 讲解台词 / 光束
   ========================================================================== */
(function () {
  'use strict';

  var DECK = document.getElementById('deck');
  var stageEl = document.getElementById('stage');
  var subText = document.getElementById('subText');
  var speakingEl = document.getElementById('speaking');
  var beamsEl = document.getElementById('beams');

  // 现场最怕"界面在跑但某块空了"，任何脚本错误都直接摆到字幕条上，便于一眼看出
  window.addEventListener('error', function (e) {
    var p = document.getElementById('subText');
    if (p) {
      p.textContent = '【脚本错误】' + (e.message || (e.error && e.error.message) || '未知') +
        '（' + (e.filename || '').split('/').pop() + ':' + e.lineno + '）';
    }
    if (window.console) console.error('[stage]', e.message, e.filename, e.lineno);
  });

  var FOCUS_POINT = { x: 960, y: 430 };     // 人物胸口，光束起点
  var PERSPECTIVE = 1900, ORIGIN = { x: 960, y: 475.2 };

  var LAYOUT = {
    screen:  { x: 300,  y: 250, z: -380, ry:  26, w: 580 },
    inspect: { x: 500,  y: 690, z:  -60, ry:  15, w: 680 },
    review:  { x: 1420, y: 690, z:  -60, ry: -15, w: 680 },
    learn:   { x: 1620, y: 250, z: -380, ry: -26, w: 580 }
  };
  var ORDER = ['inspect', 'screen', 'review', 'learn'];
  var FOCUS_SCALE = 1.18;
  var focusAmt = { inspect: 0, screen: 0, review: 0, learn: 0 };

  var snap = null;
  var script = null;
  var audio = null;
  var focusKey = null;
  var lineIdx = -1;
  var autoPlay = true;
  var autoTimer = null;
  var learnLoop = null;
  var lastSystems = null;

  /* ------------------------------ 缩放 ------------------------------ */
  function fit() {
    var s = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
    document.documentElement.style.setProperty('--scale', s);
  }
  window.addEventListener('resize', fit);
  fit();

  /* --------------------------- 背景粒子 --------------------------- */
  (function background() {
    var cv = document.getElementById('bg');
    var ctx = cv.getContext('2d');
    var parts = [];
    var W, H;
    function size() {
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      W = 1920; H = 1080;
      cv.width = W * dpr; cv.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    size();
    for (var i = 0; i < 70; i++) {
      parts.push({
        x: Math.random() * 1920, y: Math.random() * 1080,
        r: 0.6 + Math.random() * 1.6, sp: 0.06 + Math.random() * 0.22,
        a: 0.06 + Math.random() * 0.22
      });
    }
    var scan = 0;
    function frame() {
      ctx.clearRect(0, 0, W, H);
      // 网格
      ctx.strokeStyle = 'rgba(217,138,61,.055)';
      ctx.lineWidth = 1;
      for (var gx = 0; gx <= W; gx += 80) {
        ctx.beginPath(); ctx.moveTo(gx + .5, 0); ctx.lineTo(gx + .5, H); ctx.stroke();
      }
      for (var gy = 0; gy <= H; gy += 80) {
        ctx.beginPath(); ctx.moveTo(0, gy + .5); ctx.lineTo(W, gy + .5); ctx.stroke();
      }
      // 粒子
      parts.forEach(function (p) {
        p.y -= p.sp;
        if (p.y < -10) { p.y = H + 10; p.x = Math.random() * W; }
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(232,160,90,' + p.a + ')';
        ctx.fill();
      });
      // 扫描线
      scan = (scan + 0.55) % (H + 240);
      var g = ctx.createLinearGradient(0, scan - 120, 0, scan + 60);
      g.addColorStop(0, 'rgba(217,138,61,0)');
      g.addColorStop(.55, 'rgba(217,138,61,.05)');
      g.addColorStop(1, 'rgba(217,138,61,0)');
      ctx.fillStyle = g;
      ctx.fillRect(0, scan - 120, W, 180);
      requestAnimationFrame(frame);
    }
    frame();
  })();

  /* ------------------------------ 人物 ------------------------------ */
  var rig = null;
  var outfitOn = 1;                          // 0=原西装 1=工装夹克贴片 2=只换布料颜色
  function bootCharacter() {
    var q = /[?&]outfit=(suit|plate|tint|wear|video)/.exec(location.search);
    var wardrobe = q ? q[1] : 'video';       // video = 已重绘好的工装视频；其它走着色器
    if (q) outfitOn = ({ suit: 0, plate: 1, wear: 1, tint: 2, video: 0 })[q[1]];
    var base = '../01_人物素材_复用/H3/';
    function buildWith(suffix) {
      try {
        rig = new window.CharacterRig(document.getElementById('avatar'), {
          idleSrc: base + 'idle_rgb_alpha' + suffix + '.mp4',
          talkSrc: base + 'talk_rgb_alpha' + suffix + '.mp4',
          stillSrc: '../01_人物素材_复用/pose_still_rgba.png',
          forceStill: /[?&]still=1/.test(location.search),
          grainSrc: 'assets/jacket_grain.png',
          plateSrc: 'assets/jacket_maps.png',
          target: [0.0941, 0.1255, 0.2039],   // 商品图藏青（03_数据/outfit.json）
          suitY: 0.0247,
          gate: 0.44,
          exposure: 0.90,
          outfit: outfitOn
        });
        return true;
      } catch (e) {
        console.error('人物初始化失败', e);
        return false;
      }
    }

    if (wardrobe !== 'video' || location.protocol === 'file:') {
      buildWith('');
      return;
    }
    // 重绘版视频要两段都在才用；缺一段就整体回落到着色器换装，避免风格混搭
    function head(url) {
      return fetch(url, { method: 'HEAD' }).then(function (r) { return r.ok; })
        .catch(function () { return false; });
    }
    // 优先用做过时间平滑的 _works，其次 _work；两段都齐才用，否则回落到着色器换装
    var names = ['_works', '_work'];
    var probes = [];
    names.forEach(function (sfx) {
      probes.push(head(base + 'idle_rgb_alpha' + sfx + '.mp4'));
      probes.push(head(base + 'talk_rgb_alpha' + sfx + '.mp4'));
    });
    Promise.all(probes).then(function (r) {
      for (var i = 0; i < names.length; i++) {
        if (r[i * 2] && r[i * 2 + 1]) {
          outfitOn = 0;
          buildWith(names[i]);
          return;
        }
      }
      buildWith('');
    });
  }

  /* --------------------------- 布局与漂浮 --------------------------- */
  function applyLayout(t) {
    ORDER.forEach(function (key, i) {
      var L = LAYOUT[key];
      var el = document.getElementById('p-' + key);
      if (!el) return;
      var target = (key === focusKey) ? 1 : 0;
      focusAmt[key] += (target - focusAmt[key]) * 0.14;
      var f = focusAmt[key];
      var bob = Math.sin(t * 0.55 + i * 1.35) * 7;
      var sway = Math.sin(t * 0.4 + i * 0.9) * 1.2;
      var s = 1 + (FOCUS_SCALE - 1) * f;
      el.style.width = L.w + 'px';
      el.style.transform =
        'translate3d(' + L.x + 'px,' + (L.y + bob) + 'px,' + (L.z + 130 * f) + 'px) ' +
        'rotateY(' + (L.ry + sway + (1 - f) * 0) + 'deg) scale(' + s.toFixed(4) + ') ' +
        'translate(-50%,-50%)';
    });
    drawBeams(t);
  }

  function project(key) {
    var L = LAYOUT[key];
    var k = PERSPECTIVE / (PERSPECTIVE + Math.abs(L.z));
    return { x: ORIGIN.x + (L.x - ORIGIN.x) * k, y: ORIGIN.y + (L.y - ORIGIN.y) * k, k: k, w: L.w * k };
  }

  function drawBeams(t) {
    if (!beamsEl) return;
    var html = '';
    ORDER.forEach(function (key) {
      var p = project(key);
      var dir = p.x < FOCUS_POINT.x ? 1 : -1;
      var ex = p.x + dir * (p.w / 2 - 8);
      var mx = (FOCUS_POINT.x + ex) / 2;
      var my = (FOCUS_POINT.y + p.y) / 2 - 26;
      var on = (key === focusKey) ? ' on' : '';
      html += '<path class="beam' + on + '" d="M' + FOCUS_POINT.x + ' ' + FOCUS_POINT.y +
        ' Q' + mx + ' ' + my + ' ' + ex + ' ' + p.y + '"></path>';
      html += '<circle cx="' + ex + '" cy="' + p.y + '" r="' + (key === focusKey ? 3 : 1.8) +
        '" opacity="' + (key === focusKey ? 1 : 0.5) + '"></circle>';
    });
    beamsEl.innerHTML = html;

    var dash = (t * 26) % 12;
    Array.prototype.forEach.call(beamsEl.querySelectorAll('path'), function (p) {
      p.style.strokeDashoffset = (-dash).toFixed(2);
    });
  }

  /* ------------------------------ 数据 ------------------------------ */
  function mdClock() {
    var d = new Date();
    var p = function (n) { return String(n).padStart(2, '0'); };
    document.getElementById('clock').textContent =
      d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' +
      p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }

  function paint(data, live) {
    snap = data;

    var t = document.getElementById('sysTitle');
    if (data.system && data.system.title) t.textContent = data.system.title;
    var te = document.getElementById('sysTitleEn');
    if (data.system && data.system.title_en) te.textContent = data.system.title_en;

    var pillSource = document.getElementById('pillSource');
    pillSource.textContent = live ? '数据源：实时库' : '数据源：快照';
    pillSource.className = 'pill ' + (live ? 'ok' : 'warn');

    var pillDb = document.getElementById('pillDb');
    if (data.db) {
      pillDb.textContent = '库 ' + (data.db.size_kb / 1024).toFixed(1) + 'MB · ' +
        (data.db.mtime || '').slice(5, 16);
    }
    var pillModel = document.getElementById('pillModel');
    if (data.system) {
      pillModel.textContent = 'conf ' + data.system.conf + ' · iou ' + data.system.iou;
      pillModel.title = '当前权重 ' + data.system.model;
    }

    // 01/02/03 是"真实系统"的窗口/页面，只建一次，避免每次刷新把 iframe 和抓帧打断
    var p1 = document.getElementById('p-inspect');
    if (p1 && !p1.dataset.built) {
      p1.innerHTML = window.Panels.renderInspect(data.inspect, data);
      p1.dataset.built = '1';
    }
    var p2 = document.getElementById('p-screen');
    if (p2 && !p2.dataset.built) {
      p2.innerHTML = window.Panels.renderScreen(data.screen, data);
      p2.dataset.built = '1';
      syncFrames(lastSystems || {});
    }
    var p3 = document.getElementById('p-review');
    if (p3 && !p3.dataset.built) {
      p3.innerHTML = window.Panels.renderReview(data.review, data);
      p3.dataset.built = '1';
      syncFrames(lastSystems || {});
    }
    document.getElementById('p-learn').innerHTML = window.Panels.renderLearn(data.selflearn, data);

    // 图例：六类缺陷色卡
    var lg = document.getElementById('legend');
    lg.innerHTML = '<span style="color:#6d737c">六类木材缺陷</span>' +
      (data.inspect.by_class || []).map(function (c) {
        return '<span><i style="background:' + c.color + '"></i>' + c.cn + ' ' + c.n + '</span>';
      }).join('');

    var cv = document.getElementById('learnCanvas');
    if (learnLoop && learnLoop.stop) learnLoop.stop();
    if (cv) learnLoop = new window.Panels.LearnLoop(cv, data.selflearn);

    applyLayout(performance.now() / 1000);
    setTimeout(function () { applyPanelHeights(); }, 0);
  }

  /* 01 屏：真实桌面窗口的连续抓帧（拿不到就显示"未启动"，不伪造画面） */
  var winLoopOn = false;
  function startWindowFeed() {
    if (winLoopOn || location.protocol === 'file:') return;
    winLoopOn = true;
    var img = null;
    function step() {
      img = document.getElementById('winShot');
      if (!img) { setTimeout(step, 700); return; }
      if (!img.getAttribute('src')) {
        img.setAttribute('src', '/api/win/desktop.jpg?t=' + Date.now());
      } else {
        img.setAttribute('src', '/api/win/desktop.jpg?t=' + Date.now());
      }
      setTimeout(step, 260);              // 约 4 帧/秒，够看界面变化
    }
    step();
  }

  /* 三套真实系统在线状态 */
  function syncFrames(rawSys) {
    var sys = rawSys || {};
    var screen = sys.screen || {};
    var agent = sys.agent || {};
    var desktop = sys.desktop || {};
    var mask = document.getElementById('maskScreen');
    var f2 = document.getElementById('frameScreen');
    if (f2 && screen.online && !f2.getAttribute('src')) {
      f2.setAttribute('src', f2.getAttribute('data-src'));
      if (mask) mask.classList.add('hide');
    } else if (f2 && !screen.online && mask) {
      mask.classList.remove('hide');
      mask.textContent = '8770 未启动（screen_service.py）';
    }
    var maskA = document.getElementById('maskAgent');
    var f3 = document.getElementById('frameAgent');
    if (f3 && agent.online && !f3.getAttribute('src')) {
      f3.setAttribute('src', f3.getAttribute('data-src'));
      if (maskA) maskA.classList.add('hide');
    } else if (f3 && !agent.online && maskA) {
      maskA.classList.remove('hide');
      maskA.textContent = '8999 未启动（agent_service.py）';
    }

    var stS = document.getElementById('stScreen');
    if (stS) stS.textContent = screen.online ? ('在线 · pid ' + (screen.pid || '—')) : '离线';
    var stA = document.getElementById('stAgent');
    if (stA) stA.textContent = agent.online ? ('在线 · pid ' + (agent.pid || '—')) : '离线';

    var wm = document.getElementById('winMask');
    if (wm) wm.classList.toggle('hide', !!desktop.online);
    var wp = document.getElementById('winPid');
    if (wp) wp.textContent = desktop.pid ? desktop.pid : '—';
    var wa = document.getElementById('winAt');
    if (wa) wa.textContent = desktop.capture_at ? desktop.capture_at : '—';
  }

  function fetchSystems() {
    if (location.protocol === 'file:') return;
    fetch('/api/systems', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (j && j.data) { lastSystems = j.data; syncFrames(j.data); }
      })
      .catch(function () {});
  }

  function applyPanelHeights() {
    // 面板高度由内容决定，这里只校正到偶数像素，避免亚像素模糊
    ORDER.forEach(function (k) {
      var el = document.getElementById('p-' + k);
      if (!el) return;
      el.style.height = '';
    });
  }

  function fetchLive() {
    if (location.protocol === 'file:') return;
    fetch('/api/stage', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { if (j && j.data && j.data.inspect) paint(j.data, true); })
      .catch(function () {});
  }

  /* ------------------------------ 台词 ------------------------------ */
  function lineDuration(line) {
    if (line.duration) return line.duration * 1000;
    return Math.max(4200, Math.min(15000, (line.text || '').length * 240));
  }

  function setSubtitle(line) {
    subText.textContent = line.text;
  }

  function stopAudio() {
    if (audio) { try { audio.pause(); } catch (e) {} audio = null; }
    if (rig) rig.setSpeaking(false);
    speakingEl.classList.remove('on');
  }

  function focusPanel(key) {
    focusKey = key;
    ORDER.forEach(function (k) {
      var el = document.getElementById('p-' + k);
      if (!el) return;
      el.classList.toggle('focus', k === key);
    });
  }

  function playLine(i) {
    if (!script || !script.lines || !script.lines.length) return;
    lineIdx = (i + script.lines.length) % script.lines.length;
    var line = script.lines[lineIdx];
    setSubtitle(line);
    focusPanel(line.panel || null);

    stopAudio();
    clearTimeout(autoTimer);

    var url = '../04_台词配音/voice/' + line.id + '.mp3';
    var started = false;

    function begin() {
      if (started) return;
      started = true;
      if (rig) rig.setSpeaking(true);
      speakingEl.classList.add('on');
      var wait = lineDuration(line) + 650;
      if (autoPlay) {
        autoTimer = setTimeout(function () { playLine(lineIdx + 1); }, wait);
      }
    }

    audio = new Audio(encodeURI(url));
    audio.addEventListener('playing', begin);
    audio.addEventListener('ended', function () {
      if (rig) rig.setSpeaking(false);
      speakingEl.classList.remove('on');
      if (autoPlay) {
        clearTimeout(autoTimer);
        autoTimer = setTimeout(function () { playLine(lineIdx + 1); }, 900);
      }
    });
    audio.addEventListener('error', function () {
      // 没有语音文件：按字数估时长走字幕 + 说话动作
      begin();
      setTimeout(function () {
        if (rig) rig.setSpeaking(false);
        speakingEl.classList.remove('on');
        if (autoPlay) {
          clearTimeout(autoTimer);
          autoTimer = setTimeout(function () { playLine(lineIdx + 1); }, 700);
        }
      }, lineDuration(line));
    });
    var pr = audio.play();
    if (pr && pr.catch) pr.catch(function () {});
    // 保险：1.2s 内没有 playing 事件也进入说话状态
    setTimeout(begin, 1200);
  }

  /* ------------------------------ 交互 ------------------------------ */
  document.addEventListener('keydown', function (e) {
    if (e.key === ' ' || e.code === 'Space') {
      e.preventDefault();
      autoPlay = false;
      playLine(lineIdx + 1);
      return;
    }
    var k = String(e.key);
    if (k >= '1' && k <= '4') {
      var key = ORDER[parseInt(k, 10) - 1];
      autoPlay = false;
      var idx = -1;
      (script.lines || []).forEach(function (l, i) { if (l.panel === key) idx = i; });
      if (idx >= 0) playLine(idx); else focusPanel(key);
      return;
    }
    if (k === 'a' || k === 'A') {
      autoPlay = !autoPlay;
      if (autoPlay) playLine(lineIdx + 1);
      return;
    }
    if (k === 't' || k === 'T') {
      outfitOn = outfitOn ? 0 : 1;           // 工装夹克 <-> 原西装
      if (rig && rig.setOutfit) rig.setOutfit(outfitOn);
      return;
    }
  });

  document.addEventListener('click', function () {       // 首点解锁音频
    if (audio) audio.play().catch(function () {});
  });

  /* ------------------------------ 启动 ------------------------------ */
  function boot() {
    bootCharacter();

    var t0 = performance.now() / 1000;
    (function anim() {
      applyLayout(performance.now() / 1000 - t0);
      requestAnimationFrame(anim);
    })();

    if (window.__STAGE_SNAPSHOT__) {
      paint(window.__STAGE_SNAPSHOT__, false);
    }
    fetchLive();
    setInterval(fetchLive, 5000);
    fetchSystems();
    setInterval(fetchSystems, 5000);
    startWindowFeed();
    setInterval(mdClock, 1000);
    mdClock();

    script = window.__STAGE_SCRIPT__ || null;
    if (!script) {
      fetch('../04_台词配音/script.json')
        .then(function (r) { return r.json(); })
        .then(function (d) { script = d; startLines(); })
        .catch(function () {
          subText.textContent = '台词文件未加载（04_台词配音/script.js 缺失）';
        });
    } else {
      startLines();
    }
  }

  function startLines() {
    focusPanel('inspect');
    setTimeout(function () { playLine(0); }, 900);
  }

  // 现场调试/串讲时可在控制台直接调用：StageCtl.focus('learn') / StageCtl.play(4) / StageCtl.auto(false)
  window.StageCtl = {
    focus: focusPanel,
    play: function (i) { autoPlay = false; playLine(i); },
    auto: function (on) { autoPlay = !!on; if (on) playLine(lineIdx + 1); },
    refresh: fetchLive,
    snapshot: function () { return snap; },
    outfit: function (mode) {
      if (typeof mode === 'boolean') outfitOn = mode ? 1 : 0;
      else if (typeof mode === 'number') outfitOn = mode | 0;
      else outfitOn = outfitOn ? 0 : 1;
      if (rig && rig.setOutfit) rig.setOutfit(outfitOn);
      return outfitOn;
    }
  };

  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    setTimeout(boot, 0);
  } else {
    document.addEventListener('DOMContentLoaded', boot);
  }
})();
