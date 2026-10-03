// Browser smoke test (Node >= 22, no packages): loads pages in headless Chrome over the DevTools Protocol,
// exercises quizzes and interactive controls, and fails on console errors, uncaught exceptions, failed requests
// or Mermaid diagrams that did not render.
//   node smoke-test.mjs [page.html ...]      (default: index.html and every module)
import { spawn } from 'node:child_process';
import { mkdtempSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const course = resolve(here, '..');
const pages = process.argv.slice(2).length ? process.argv.slice(2).map(p => resolve(course, p))
  : [join(course, 'index.html'), ...readdirSync(join(course, 'modules')).filter(f => f.endsWith('.html')).sort().map(f => join(course, 'modules', f))];
const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const port = 9800 + Math.floor(Math.random() * 100);
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), 'smoke-'))}`,
  '--no-first-run', '--disable-gpu', '--allow-file-access-from-files', 'about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));

const INTERACT = `(() => {
  const r = { quiz: null, controls: 0, nodes: 0, outs: [] };
  const q = document.querySelector('[data-quiz]');
  if (q) {
    const f = q.querySelector('fieldset.q'); const ans = f.dataset.answer;
    const wrong = [...f.querySelectorAll('input')].find(i => i.value !== ans); wrong.click();
    const fbWrong = f.querySelector('.feedback').textContent;
    f.querySelector('input[value="' + ans + '"]').click();
    const fbRight = f.querySelector('.feedback').textContent;
    q.querySelectorAll('fieldset.q').forEach(fs => fs.querySelector('input[value="' + fs.dataset.answer + '"]').click());
    r.quiz = { wrongOk: fbWrong.startsWith('Not quite'), rightOk: fbRight.startsWith('Correct'),
               score: q.querySelector('.score').textContent, gateShown: !q.querySelector('.gate').hidden };
  }
  const snap = () => [...document.querySelectorAll('.sim')].map(e => e.textContent.replace(/\s+/g, ' '));
  const before = snap();
  document.querySelectorAll('.sim input[type=range]').forEach(x => { x.value = x.max; x.dispatchEvent(new Event('input', { bubbles: true })); r.controls++; });
  document.querySelectorAll('.sim input[type=number]').forEach(x => { x.dispatchEvent(new Event('input', { bubbles: true })); r.controls++; });
  document.querySelectorAll('.sim select').forEach(s => { s.selectedIndex = Math.min(1, s.options.length - 1); s.dispatchEvent(new Event('input', { bubbles: true })); s.dispatchEvent(new Event('change', { bubbles: true })); r.controls++; });
  document.querySelectorAll('.sim input[type=radio], .sim input[type=checkbox]').forEach(x => { x.click(); r.controls++; });
  document.querySelectorAll('.sim .btn, .controls-row .btn, .sim button, .sim summary, .sim [role=button], .sim [role=radio]').forEach(b => { b.click(); r.controls++; });
  document.querySelectorAll('svg [data-node], .sim [data-node]').forEach(n => { n.dispatchEvent(new MouseEvent('click', { bubbles: true })); r.nodes++; });
  const after = snap();
  r.outsChanged = before.length ? before.some((b, i) => b !== after[i]) : null;
  r.mermaid = { blocks: document.querySelectorAll('pre.mermaid').length, svgs: document.querySelectorAll('pre.mermaid svg').length };
  r.sims = document.querySelectorAll('.sim').length;
  r.overflow = document.documentElement.scrollWidth - window.innerWidth;
  r.offenders = r.overflow > 2 ? [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > window.innerWidth + 1 && !e.closest('pre')).slice(0, 4).map(e => e.tagName.toLowerCase() + (e.className && e.className.baseVal === undefined ? '.' + String(e.className).split(' ')[0] : '') + '#' + (e.id || '') + ' right=' + Math.round(e.getBoundingClientRect().right)) : [];
  r.images = [...document.images].filter(i => !i.complete || i.naturalWidth === 0).length;
  return r;
})()`;

let failed = 0;
try {
  let ver;
  for (let i = 0; i < 50; i++) { try { ver = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json(); break; } catch { await sleep(200); } }
  if (!ver) throw new Error('Chrome did not start');
  const ws = new WebSocket(ver.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0; const pending = new Map(); const listeners = [];
  ws.onmessage = m => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } else listeners.forEach(l => l(d)); };
  const send = (method, params = {}, sessionId) => new Promise(res => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params, sessionId })); });

  for (const page of pages) {
    const name = page.replace(course + '/', '');
    const problems = [];
    const { result: { targetId } } = await send('Target.createTarget', { url: 'about:blank' });
    const { result: { sessionId } } = await send('Target.attachToTarget', { targetId, flatten: true });
    const s = (m, p) => send(m, p, sessionId);
    const onEvent = d => {
      if (d.sessionId !== sessionId) return;
      if (d.method === 'Runtime.exceptionThrown') problems.push('exception: ' + (d.params.exceptionDetails.exception?.description || d.params.exceptionDetails.text).split('\n')[0]);
      if (d.method === 'Runtime.consoleAPICalled' && d.params.type === 'error') problems.push('console.error: ' + d.params.args.map(a => a.value ?? a.description).join(' ').slice(0, 160));
      if (d.method === 'Log.entryAdded' && d.params.entry.level === 'error') problems.push('log: ' + d.params.entry.text.slice(0, 160) + ' ' + (d.params.entry.url || ''));
      if (d.method === 'Network.loadingFailed' && !d.params.canceled) problems.push('request failed: ' + d.params.errorText);
    };
    listeners.push(onEvent);
    await s('Runtime.enable'); await s('Log.enable'); await s('Network.enable'); await s('Page.enable');
    if (process.env.NARROW) await s('Emulation.setDeviceMetricsOverride', { width: 500, height: 900, deviceScaleFactor: 1, mobile: false });
    await s('Page.navigate', { url: 'file://' + page });
    await sleep(1800);
    const ev = await s('Runtime.evaluate', { expression: INTERACT, returnByValue: true });
    await sleep(600);
    const r = ev.result?.result?.value;
    if (ev.result?.exceptionDetails) problems.push('interaction script failed: ' + ev.result.exceptionDetails.text);
    if (r) {
      if (r.quiz && !(r.quiz.wrongOk && r.quiz.rightOk && r.quiz.gateShown)) problems.push('quiz behaviour wrong: ' + JSON.stringify(r.quiz));
      if (r.mermaid.blocks !== r.mermaid.svgs) problems.push(`mermaid: ${r.mermaid.svgs} of ${r.mermaid.blocks} diagrams rendered`);
      if (process.env.NARROW && r.overflow > 2) problems.push(`page scrolls sideways at 500px by ${r.overflow}px: ${r.offenders.join(' | ')}`);
      if (r.images) problems.push(`${r.images} image(s) failed to load`);
      if (r.sims && r.controls + r.nodes === 0) problems.push('interactive panel without any control');
      if (r.sims && r.outsChanged === false) problems.push('interacting with controls changed no output');
    }
    console.log(`${problems.length ? 'FAIL' : 'ok  '} ${name}  ${r ? `quiz:${r.quiz ? r.quiz.score : '-'} controls:${r.controls} nodes:${r.nodes} sims:${r.sims} mermaid:${r.mermaid.svgs}/${r.mermaid.blocks}` : ''}`);
    problems.forEach(p => console.log('       ' + p));
    if (problems.length) failed++;
    listeners.splice(listeners.indexOf(onEvent), 1);
    await send('Target.closeTarget', { targetId });
  }
  ws.close();
} finally { chrome.kill(); }
console.log(failed ? `\n${failed} page(s) failed` : '\nall pages ok');
process.exit(failed ? 1 : 0);
