// Screenshot a page through Chrome DevTools Protocol using only Node built-ins (Node >= 22 has a global WebSocket).
// usage: node shot.mjs <url> <out.png> [--cookie name=value]... [--wait ms] [--width 1440] [--height 900] [--full]
import { spawn } from 'node:child_process';
import { writeFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const args = process.argv.slice(2);
const url = args[0], out = args[1];
const opt = (n, d) => { const i = args.indexOf(n); return i >= 0 ? args[i + 1] : d; };
const cookies = args.flatMap((a, i) => a === '--cookie' ? [args[i + 1]] : []);
const wait = +opt('--wait', 4000), width = +opt('--width', 1440), height = +opt('--height', 900);
const full = args.includes('--full');
const clickText = opt('--click', null); // click the first leaf element whose text equals this, then wait again
const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const port = 9300 + Math.floor(Math.random() * 500);
const profile = mkdtempSync(join(tmpdir(), 'shot-'));

const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu', `--window-size=${width},${height}`, 'about:blank'],
  { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));
try {
  let ver;
  for (let i = 0; i < 50; i++) { try { ver = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json(); break; } catch { await sleep(200); } }
  if (!ver) throw new Error('Chrome did not expose the debugging port');
  const ws = new WebSocket(ver.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0; const pending = new Map();
  ws.onmessage = m => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
  const send = (method, params = {}, sessionId) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params, sessionId })); });
  const { result: { targetId } } = await send('Target.createTarget', { url: 'about:blank' });
  const { result: { sessionId } } = await send('Target.attachToTarget', { targetId, flatten: true });
  const s = (m, p) => send(m, p, sessionId);
  await s('Page.enable'); await s('Network.enable');
  await s('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
  const host = new URL(url).hostname;
  for (const c of cookies) { const [name, ...v] = c.split('='); await s('Network.setCookie', { name, value: v.join('='), domain: host, path: '/' }); }
  await s('Page.navigate', { url });
  await sleep(wait);
  if (clickText) {
    const expr = `(() => { const t = ${JSON.stringify(clickText)}; const el = [...document.querySelectorAll('td, div, span, a')].find(e => e.children.length === 0 && e.textContent.trim() === t); if (el) { el.click(); return true; } return false; })()`;
    const r = await s('Runtime.evaluate', { expression: expr, returnByValue: true });
    console.log('click', clickText, r.result?.result?.value);
    await sleep(wait);
  }
  let clip;
  if (full) { const { result: { cssContentSize: c } } = await s('Page.getLayoutMetrics'); clip = { x: 0, y: 0, width: c.width, height: c.height, scale: 1 }; }
  const shot = await s('Page.captureScreenshot', { format: 'png', captureBeyondViewport: full, ...(clip ? { clip } : {}) });
  writeFileSync(out, Buffer.from(shot.result.data, 'base64'));
  console.log(`wrote ${out}`);
  ws.close();
} finally { chrome.kill(); }
