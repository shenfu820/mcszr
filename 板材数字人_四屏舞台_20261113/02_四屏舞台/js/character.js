/* ==========================================================================
   人物播放器：把「左半 RGB / 右半 灰度 Alpha」的 mp4 直接当透明人像播放
   —— 与素材包 02_人物生成与抠像工程/Unity接入参考/Shaders/AlphaVideo.shader 同一套思路，
      这里用 WebGL1 单 quad 复刻，不依赖 three.js、不联网、不打 bundle。
   兜底：视频解不出来时，用 pose_still_rgba.png 静态帧顶上。
   ========================================================================== */
(function () {
  'use strict';

  var VS = [
    'attribute vec2 aPos;',
    'varying vec2 vUv;',
    'void main(){ vUv = aPos * 0.5 + 0.5; gl_Position = vec4(aPos, 0.0, 1.0); }'
  ].join('\n');

  var FS = [
    'precision mediump float;',
    'varying vec2 vUv;',
    'uniform sampler2D uIdle;',
    'uniform sampler2D uTalk;',
    'uniform sampler2D uStill;',
    'uniform sampler2D uGrain;',
    'uniform sampler2D uPlate;',    // 商品图工装夹克贴片（768×1344，RGBA）
    'uniform float uMix;',      // 0 = idle，1 = talk
    'uniform float uMode;',     // 0 = 视频（左右分半），1 = 静态 RGBA 兜底
    'uniform float uOpacity;',
    'uniform float uOutfit;',   // 0 = 原西装，1 = 贴片工装夹克，2 = 只换布料颜色
    'uniform vec3  uTarget;',   // 布料主色
    'uniform float uTargetY;',
    'uniform float uSuitY;',    // 西装亮度参考（0.0247 与 prepare_garment_plate.py 一致）
    'uniform float uGate;',
    'uniform float uGrainAmt;',
    'uniform float uGrainScale;',
    'uniform float uExposure;', // 贴片曝光系数（0.75）
    'const vec3 W = vec3(0.2126, 0.7152, 0.0722);',
    'void main(){',
    '  vec3 c; float a;',
    '  if (uMode > 0.5) {',
    '    vec4 s = texture2D(uStill, vUv);',
    '    c = s.rgb; a = s.a;',
    '  } else {',
    '    vec2 uvL = vec2(vUv.x * 0.5, vUv.y);',
    '    vec2 uvR = vec2(0.5 + vUv.x * 0.5, vUv.y);',
    '    vec4 A  = texture2D(uIdle, uvL);',
    '    float aA = texture2D(uIdle, uvR).r;',
    '    vec4 B  = texture2D(uTalk, uvL);',
    '    float aB = texture2D(uTalk, uvR).r;',
    '    c = mix(A.rgb, B.rgb, uMix);',
    '    a = mix(aA, aB, uMix);',
    '  }',
    // ---- 换装 ----
    '  if (uOutfit > 1.5 && uOutfit < 2.5) {',
    // 模式 2：只把布料颜色换掉（保留西装版型）
    '    float Y2 = dot(c, W);',
    '    float blueish = clamp((c.b - c.r) * 5.0, 0.0, 1.0);',
    '    float darkish = 1.0 - smoothstep(0.30, 0.62, max(c.r, max(c.g, c.b)));',
    '    float mask = blueish * darkish * smoothstep(uGate - 0.03, uGate + 0.05, vUv.y);',
    '    float ratio = clamp(Y2 / max(uSuitY, 1e-4), 0.05, 12.0);',
    '    float Yn = clamp(uTargetY * pow(ratio, 0.62), 0.006, 0.42);',
    '    vec3  chroma = uTarget / max(uTargetY, 1e-4);',
    '    vec3  recol = chroma * Yn;',
    '    float g = texture2D(uGrain, vUv * uGrainScale).r;',
    '    recol *= (1.0 + (g - 0.5) * 2.0 * uGrainAmt);',
    '    c = mix(c, recol, mask);',
    '  } else if (uOutfit > 2.5) {',
    // 模式 3：照片版贴片 —— 商品图的细节 + 小星的环境光
    '    vec4 ph = texture2D(uPlate, vUv);',
    '    vec2 uvP = vec2(vUv.x * 0.5, vUv.y);',
    '    float Yc3 = dot(c, W);',
    '    float Ym3 = 0.25 * (',
    '      dot(texture2D(uIdle, uvP + vec2(0.0042, 0.0)).rgb, W) +',
    '      dot(texture2D(uIdle, uvP - vec2(0.0042, 0.0)).rgb, W) +',
    '      dot(texture2D(uIdle, uvP + vec2(0.0, 0.0048)).rgb, W) +',
    '      dot(texture2D(uIdle, uvP - vec2(0.0, 0.0048)).rgb, W));',
    '    float fold3 = pow(clamp(Yc3 / max(Ym3, 1e-4), 0.60, 1.55), 0.85);',
    '    vec3 cloth3 = ph.rgb * (uExposure * 1.05 * fold3);',
    '    float warm3 = smoothstep(0.045, 0.10, c.r - c.b) * step(0.16, c.r);',
    '    c = mix(c, cloth3, ph.a * (1.0 - warm3));',
    '  } else if (uOutfit > 0.5) {',
    // 模式 1：搬商品图工装的**结构**（灰拼块/口袋/反光条），颜色与明暗全用小星自己的
    '    vec4 mp = texture2D(uPlate, vUv);',
    '    vec2 uvS = vec2(vUv.x * 0.5, vUv.y);',
    '    float detail = 0.82 + mp.r * 0.46;',
    '    float yoke = smoothstep(0.35, 0.62, mp.g);',
    '    float stripe = smoothstep(0.40, 0.66, mp.b);',
    '    vec3 base = mix(uTarget, vec3(0.627, 0.627, 0.675), yoke);',
    '    base = mix(base, vec3(0.80, 0.80, 0.82), stripe * 0.55);',
    '    vec2 tx = vec2(0.0042, 0.0048);',
    '    float Yc = dot(c, W);',
    '    float Ym = 0.25 * (',
    '      dot(texture2D(uIdle, uvS + vec2(tx.x, 0.0)).rgb, W) +',
    '      dot(texture2D(uIdle, uvS - vec2(tx.x, 0.0)).rgb, W) +',
    '      dot(texture2D(uIdle, uvS + vec2(0.0, tx.y)).rgb, W) +',
    '      dot(texture2D(uIdle, uvS - vec2(0.0, tx.y)).rgb, W));',
    '    float fold = pow(clamp(Yc / max(Ym, 1e-4), 0.60, 1.55), 0.85);',
    '    vec3 cloth = base * detail * (uExposure * fold);',
    '    float warm2 = smoothstep(0.045, 0.10, c.r - c.b) * step(0.16, c.r);',
    '    c = mix(c, cloth, mp.a * (1.0 - warm2));',
    '  }',
    '  a *= uOpacity;',
    '  gl_FragColor = vec4(c * a, a);',   // 预乘 alpha
    '}'
  ].join('\n');

  function compile(gl, type, src) {
    var s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
      throw new Error('shader: ' + gl.getShaderInfoLog(s));
    }
    return s;
  }

  function CharacterRig(canvas, opts) {
    this.canvas = canvas;
    this.opts = opts || {};
    this.mix = 0;
    this.target = 0;
    this.mode = 0;
    this.ready = { idle: false, talk: false, still: false };
    this.gl = canvas.getContext('webgl', { alpha: true, premultipliedAlpha: true, antialias: true });
    this.fallback2d = null;
    this._init();
  }

  CharacterRig.prototype._init = function () {
    var self = this;
    var c = this.canvas;
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    c.width = Math.round(400 * dpr);
    c.height = Math.round(700 * dpr);

    // 视频 -> 中间画布（800×700 装下 1536×1344 整帧，左右各半正好 400×700）
    this.buf = {
      idle: document.createElement('canvas'),
      talk: document.createElement('canvas')
    };
    ['idle', 'talk'].forEach(function (k) {
      self.buf[k].width = 800;
      self.buf[k].height = 700;
    });

    this.video = {};
    if (this.opts.forceStill) {                  // ?still=1：强制静态兜底（现场排障/离线降级）
      this.video.idle = null;
      this.video.talk = null;
    } else {
      ['idle', 'talk'].forEach(function (k) {
        var v = document.createElement('video');
        v.muted = true;
        v.loop = true;
        v.playsInline = true;
        v.autoplay = true;
        v.preload = 'auto';
        v.setAttribute('playsinline', '');
        v.src = k === 'idle' ? self.opts.idleSrc : self.opts.talkSrc;
        v.addEventListener('loadeddata', function () {
          self.ready[k] = true;
          v.play().catch(function () {});
        });
        v.addEventListener('error', function () { self.ready[k] = false; });
        self.video[k] = v;
      });
    }

    this.still = new Image();
    this.still.onload = function () { self.ready.still = true; };
    this.still.src = this.opts.stillSrc;

    var gl = this.gl;
    if (!gl) {                                   // 没有 WebGL：直接贴静态图
      var ctx = c.getContext('2d');
      var img = new Image();
      img.onload = function () { ctx.drawImage(img, 0, 0, c.width, c.height); };
      img.src = this.opts.stillSrc;
      this.fallback2d = ctx;
      return;
    }

    var prog = gl.createProgram();
    gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VS));
    gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      throw new Error('link: ' + gl.getProgramInfoLog(prog));
    }
    gl.useProgram(prog);
    this.prog = prog;

    var quad = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, quad);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    var aPos = gl.getAttribLocation(prog, 'aPos');
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

    this.u = {
      idle: gl.getUniformLocation(prog, 'uIdle'),
      talk: gl.getUniformLocation(prog, 'uTalk'),
      still: gl.getUniformLocation(prog, 'uStill'),
      grain: gl.getUniformLocation(prog, 'uGrain'),
      mix: gl.getUniformLocation(prog, 'uMix'),
      mode: gl.getUniformLocation(prog, 'uMode'),
      opacity: gl.getUniformLocation(prog, 'uOpacity'),
      outfit: gl.getUniformLocation(prog, 'uOutfit'),
      target: gl.getUniformLocation(prog, 'uTarget'),
      targetY: gl.getUniformLocation(prog, 'uTargetY'),
      suitY: gl.getUniformLocation(prog, 'uSuitY'),
      gate: gl.getUniformLocation(prog, 'uGate'),
      grainAmt: gl.getUniformLocation(prog, 'uGrainAmt'),
      grainScale: gl.getUniformLocation(prog, 'uGrainScale'),
      plate: gl.getUniformLocation(prog, 'uPlate'),
      exposure: gl.getUniformLocation(prog, 'uExposure')
    };

    this.tex = {
      idle: this._tex(), talk: this._tex(), still: this._tex(),
      grain: this._tex(true), plate: this._tex()
    };
    this.outfit = (this.opts.outfit === undefined) ? 1 : this.opts.outfit;
    this.exposure = (this.opts.exposure === undefined) ? 0.75 : this.opts.exposure;

    // 上衣主色/布纹：来自 prepare_outfit.py 对 USDZ 图集的取样（03_数据/outfit.json）
    var t = this.opts.target || [0.114, 0.133, 0.216];   // #1d2237  <-- 布料主色，别和 this.target(口型混合) 撞名
    this.targetY = 0.2126 * t[0] + 0.7152 * t[1] + 0.0722 * t[2];
    this.targetColor = t;          // 注意：this.target 是口型混合的目标值，不能同名
    this.suitY = this.opts.suitY || 0.024;
    this.gate = (this.opts.gate === undefined) ? 0.44 : this.opts.gate;

    if (this.opts.grainSrc) {
      var gi = new Image();
      gi.onload = function () {
        gl.activeTexture(gl.TEXTURE4);
        gl.bindTexture(gl.TEXTURE_2D, self.tex.grain);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, gi);
        self.ready.grain = true;
      };
      gi.src = this.opts.grainSrc;
    }

    if (this.opts.plateSrc) {
      var pi = new Image();
      pi.onload = function () {
        gl.activeTexture(gl.TEXTURE5);
        gl.bindTexture(gl.TEXTURE_2D, self.tex.plate);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, pi);
        self.ready.plate = true;
      };
      pi.src = this.opts.plateSrc;
    }

    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.clearColor(0, 0, 0, 0);

    this._loop();
  };

  CharacterRig.prototype._tex = function (repeat) {
    var gl = this.gl;
    var t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    var wrap = repeat ? gl.REPEAT : gl.CLAMP_TO_EDGE;
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, wrap);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, wrap);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE,
      new Uint8Array([128, 128, 128, 255]));
    return t;
  };

  CharacterRig.prototype._upload = function (key) {
    var gl = this.gl;
    var v = this.video[key];
    if (!this.ready[key] || !v || v.readyState < 2) return false;
    var cv = this.buf[key];
    var ctx = cv.getContext('2d');
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.drawImage(v, 0, 0, cv.width, cv.height);
    gl.activeTexture(gl.TEXTURE0);           // 上传必须落在固定单元，别把 3 号单元（布纹）抢了
    gl.bindTexture(gl.TEXTURE_2D, this.tex[key]);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, cv);
    return true;
  };

  CharacterRig.prototype._loop = function () {
    var self = this;
    var gl = this.gl;
    var lastStill = 0;

    function frame(ts) {
      var hasIdle = self._upload('idle');
      var hasTalk = self._upload('talk');
      if (hasIdle && self.ready.still) {
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, self.tex.still);
        if (ts - lastStill > 500) {
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, self.still);
          lastStill = ts;
        }
      }

      if (hasIdle) {
        self.mode = 0;
      } else if (self.ready.still) {
        self.mode = 1;
      }

      self.mix += (self.target - self.mix) * 0.10;
      var mix = hasTalk ? self.mix : 0;

      gl.viewport(0, 0, self.canvas.width, self.canvas.height);
      gl.clear(gl.COLOR_BUFFER_BIT);

      gl.uniform1i(self.u.idle, 0);
      gl.uniform1i(self.u.talk, 1);
      gl.uniform1i(self.u.still, 2);
      gl.uniform1i(self.u.grain, 4);
      gl.uniform1i(self.u.plate, 5);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, self.tex.idle);
      gl.activeTexture(gl.TEXTURE1);
      gl.bindTexture(gl.TEXTURE_2D, self.tex.talk);
      gl.activeTexture(gl.TEXTURE2);
      gl.bindTexture(gl.TEXTURE_2D, self.tex.still);
      gl.activeTexture(gl.TEXTURE4);
      gl.bindTexture(gl.TEXTURE_2D, self.tex.grain);
      gl.activeTexture(gl.TEXTURE5);
      gl.bindTexture(gl.TEXTURE_2D, self.tex.plate);
      gl.uniform1f(self.u.mix, mix);
      gl.uniform1f(self.u.mode, self.mode);
      gl.uniform1f(self.u.opacity, 1.0);
      gl.uniform1f(self.u.outfit, self.outfit || 0);
      gl.uniform3f(self.u.target, self.targetColor[0], self.targetColor[1], self.targetColor[2]);
      gl.uniform1f(self.u.targetY, self.targetY);
      gl.uniform1f(self.u.suitY, self.suitY);
      gl.uniform1f(self.u.gate, self.gate);
      gl.uniform1f(self.u.grainAmt, 0.10);
      gl.uniform1f(self.u.grainScale, 26.0);
      gl.uniform1f(self.u.exposure, self.exposure);

      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  };

  CharacterRig.prototype.setSpeaking = function (on) {
    this.target = on ? 1 : 0;
    var t = this.video && this.video.talk;
    if (on && t && t.paused) t.play().catch(function () {});
  };

  // 换装开关：0 = 原西装，1 = 贴片工装夹克，2 = 只换布料颜色
  CharacterRig.prototype.setOutfit = function (mode) {
    this.outfit = (typeof mode === 'boolean') ? (mode ? 1 : 0) : (mode | 0);
  };

  window.CharacterRig = CharacterRig;
})();
