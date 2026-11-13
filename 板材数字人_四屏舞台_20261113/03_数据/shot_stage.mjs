// CDP 截图 + 控制台抓取（不装任何 npm 包，只用 Node 22 内置 fetch / WebSocket）
// 用法: node cdp_shot.mjs <url> <out.png> [waitMs] [width] [height]
import { spawn } from 'node:child_process';
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

const [, , url = 'http://127.0.0.1:8790/', out = 'shot.png', waitMs = '8000', W = '1920', H = '1080', expr = '', DPR = '1'] = process.argv;
const PORT = 9333 + Math.floor(Math.random() * 200);
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const profile = process.env.LOCALAPPDATA + '\\Temp\\cdp_prof_' + PORT;

const chrome = spawn(CHROME, [
  '--headless=new',
  '--remote-debugging-port=' + PORT,
  '--user-data-dir=' + profile,
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-gpu',
  '--enable-unsafe-swiftshader',
  '--use-angle=swiftshader',
  '--autoplay-policy=no-user-gesture-required',
  '--hide-scrollbars',
  '--window-size=' + W + ',' + H,
  'about:blank'
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function version() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch('http://127.0.0.1:' + PORT + '/json/version');
      if (r.ok) return await r.json();
    } catch { /* not up yet */ }
    await sleep(250);
  }
  throw new Error('chrome devtools not reachable');
}

const logs = [];
let id = 0;
const pending = new Map();

function send(ws, method, params = {}, sessionId) {
  const msgId = ++id;
  const payload = { id: msgId, method, params };
  if (sessionId) payload.sessionId = sessionId;
  ws.send(JSON.stringify(payload));
  return new Promise((res, rej) => pending.set(msgId, { res, rej }));
}

const v = await version();
const ws = new WebSocket(v.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r, { once: true }));

ws.addEventListener('message', (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) {
    const { res, rej } = pending.get(msg.id);
    pending.delete(msg.id);
    if (msg.error) rej(new Error(JSON.stringify(msg.error)));
    else res(msg.result);
    return;
  }
  if (msg.method === 'Runtime.consoleAPICalled') {
    logs.push('[console.' + msg.params.type + '] ' +
      msg.params.args.map((a) => a.value ?? a.description ?? a.type).join(' '));
  } else if (msg.method === 'Runtime.exceptionThrown') {
    const d = msg.params.exceptionDetails;
    logs.push('[exception] ' + (d.exception?.description || d.text));
  } else if (msg.method === 'Log.entryAdded') {
    logs.push('[log.' + msg.params.entry.level + '] ' + msg.params.entry.text);
  }
});

const { targetId } = await send(ws, 'Target.createTarget', { url: 'about:blank' });
const { sessionId } = await send(ws, 'Target.attachToTarget', { targetId, flatten: true });

await send(ws, 'Runtime.enable', {}, sessionId);
await send(ws, 'Log.enable', {}, sessionId);
await send(ws, 'Page.enable', {}, sessionId);
await send(ws, 'Emulation.setDeviceMetricsOverride',
  { width: +W, height: +H, deviceScaleFactor: +DPR, mobile: false }, sessionId);
await send(ws, 'Page.navigate', { url }, sessionId);
await sleep(+waitMs);

if (expr) {
  await send(ws, 'Runtime.evaluate', { expression: expr, returnByValue: true }, sessionId);
  await sleep(2500);
}

const probe = await send(ws, 'Runtime.evaluate', {
  expression: `(() => {
    const sub = document.getElementById('subText');
    const av = document.querySelectorAll('.panel').length;
    const cv = document.getElementById('avatar');
    return JSON.stringify({
      title: document.title,
      subtitle: sub ? sub.textContent.slice(0, 160) : null,
      panels: av,
      canvas: cv ? [cv.width, cv.height] : null,
      clock: (document.getElementById('clock') || {}).textContent,
      source: (document.getElementById('pillSource') || {}).textContent,
      hasSnapshot: !!window.__STAGE_SNAPSHOT__,
      hasScript: !!window.__STAGE_SCRIPT__,
      learnCanvasW: (document.getElementById('learnCanvas') || {}).clientWidth
    });
  })()`,
  returnByValue: true
}, sessionId);
console.log('PROBE ' + probe.result.value);

const shot = await send(ws, 'Page.captureScreenshot', { format: 'png', captureBeyondViewport: false }, sessionId);
mkdirSync(dirname(resolve(out)), { recursive: true });
writeFileSync(out, Buffer.from(shot.data, 'base64'));
console.log('SHOT ' + resolve(out));

if (logs.length) console.log('--- console ---\n' + logs.slice(0, 40).join('\n'));

await send(ws, 'Target.closeTarget', { targetId });
ws.close();
chrome.kill();
process.exit(0);
