// Dependency-free Chromium DevTools Protocol smoke test. Node 22+, an installed Chromium,
// and frontend/backend running on the configured local URL are required.
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const base = process.env.BROWSER_TEST_URL || 'http://localhost:3010';
const chrome = process.env.CHROME_PATH;
if (!chrome) throw new Error('Set CHROME_PATH to your installed Chromium executable.');
const out = path.resolve('../docs/screenshots');
await mkdir(out, { recursive: true });
const port = 9400 + Math.floor(Math.random() * 1000);
const browser = spawn(chrome, ['--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run', '--no-default-browser-check', `--remote-debugging-port=${port}`, `--user-data-dir=${path.resolve('../.local/browser-smoke-' + Date.now())}`, 'about:blank'], { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'] });
browser.stderr.on('data', chunk => { if (String(chunk).includes('DevTools listening')) console.log(String(chunk).trim()); });
browser.on('error', error => console.error('Chromium:', error.message));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let ws, sequence = 0; const pending = new Map(), errors = [];
async function waitFor(fn, label, timeout = 45000) { const end = Date.now() + timeout; while (Date.now() < end) { try { const result = await fn(); if (result) return result; } catch {} await delay(150); } throw new Error('Timed out: ' + label); }
function call(method, params = {}) { return new Promise((resolve, reject) => { const id = ++sequence; const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout: ' + method)); }, 45000); pending.set(id, { resolve: value => { clearTimeout(timer); resolve(value); }, reject: error => { clearTimeout(timer); reject(error); } }); ws.send(JSON.stringify({ id, method, params })); }); }
async function evaluate(expression) { const result = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }); if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails)); return result.result.value; }
async function click(text) { await waitFor(() => evaluate(`(() => { const el = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === ${JSON.stringify(text)} && !x.disabled); if (!el) return false; el.click(); return true; })()`), 'button ' + text); }
async function fill(selector, value) { await waitFor(() => evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el || el.disabled) return false; el.focus(); el.select?.(); return true; })()`), selector); await call('Input.insertText', { text: value }); }
async function textPresent(text) { return evaluate(`document.body.innerText.includes(${JSON.stringify(text)})`); }
async function screenshot(name) { await delay(300); const shot = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false }); await writeFile(path.join(out, name + '.png'), Buffer.from(shot.data, 'base64')); }
async function api(url) { return evaluate(`fetch('/api/v1' + ${JSON.stringify(url)}).then(async r => { if(!r.ok) throw new Error(r.status); return r.json() })`); }
async function chat(text) { await fill('textarea[aria-label="AI köməkçiyə mesaj"]', text); await evaluate(`document.querySelector('button[aria-label="Göndər"]').click()`); await waitFor(() => evaluate(`!document.querySelector('textarea[aria-label="AI köməkçiyə mesaj"]').disabled`), 'chat response'); }
try {
  const targets = await waitFor(async () => (await fetch(`http://127.0.0.1:${port}/json`)).json(), 'Chromium CDP');
  ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }));
  ws.addEventListener('close', event => { for (const p of pending.values()) p.reject(new Error('CDP closed: ' + event.code + ' ' + event.reason)); pending.clear(); });
  ws.addEventListener('message', event => { const data = JSON.parse(event.data); if (data.id) { const p = pending.get(data.id); pending.delete(data.id); data.error ? p?.reject(new Error(data.error.message)) : p?.resolve(data.result); } else if (data.method === 'Runtime.exceptionThrown') errors.push(data.params.exceptionDetails.text); });
  await call('Runtime.enable'); await call('Page.enable');
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await call('Page.navigate', { url: base });
  await click('Qeydiyyatdan keçin');
  await fill('input[name="full_name"]', 'QA Sahibkar');
  await fill('input[name="business_name"]', 'QA Mebel');
  await fill('input[name="email"]', `qa-${Date.now()}@example.com`);
  await fill('input[name="password"]', 'browser-test-secret-123');
  await click('Hesab yarat');
  await waitFor(() => textPresent('Biznes köməkçiniz'), 'registered workspace');
  await screenshot('assistant-desktop');
  const me = await api('/me'); const biz = `/businesses/${me.memberships[0].business.id}`;
  await chat('Məndə 500 qapı, 300 taxta və 150 masa var.');
  assert.equal((await api(biz + '/products')).total, 0, 'preview must not write');
  await screenshot('confirmation-desktop');
  await chat('Bəli');
  assert.equal((await api(biz + '/products')).total, 3);
  await chat('Bu gün 20 qapını 150 manatdan satdım.');
  await click('Təsdiqlə');
  await waitFor(async () => (await api(biz + '/sales')).total === 1, 'saved sale');
  assert.equal((await api(biz + '/products')).items.find(p => p.name === 'Qapı').quantity, '480');
  assert.equal((await api(biz + '/analytics/overview')).totals.revenue, '3000.00');
  await click('İcmal'); await waitFor(() => textPresent('Satış dinamikası'), 'dashboard'); await screenshot('dashboard-desktop');
  await click('İnventar'); await waitFor(() => textPresent('Qapı'), 'inventory'); await screenshot('inventory-desktop');
  await click('Məhsul əlavə et'); await fill('input[name="name"]', 'Stul'); await fill('input[name="quantity"]', '12'); await fill('input[name="selling_price"]', '45'); await click('Yadda saxla');
  await waitFor(async () => (await api(biz + '/products')).total === 4, 'manual product');
  await click('Satışlar'); await waitFor(() => textPresent('S-000001'), 'sales');
  await click('Yeni satış');
  await waitFor(() => evaluate(`(() => { const select = document.querySelector('dialog select'); const option = [...(select?.options || [])].find(o => o.textContent.startsWith('Stul')); if (!option) return false; select.value = option.value; select.dispatchEvent(new Event('change', { bubbles: true })); return true; })()`), 'manual sale product');
  await fill('dialog .sale-line input', '2');
  await click('Satışı yoxla'); await click('Satışı təsdiqlə və yadda saxla');
  await waitFor(async () => (await api(biz + '/sales')).total === 2, 'manual sale');
  assert.equal((await api(biz + '/analytics/overview')).totals.revenue, '3090.00');
  await waitFor(() => evaluate(`(() => { const button = document.querySelector('button[aria-label="S-000001 detallar"]'); if (!button) return false; button.click(); return true; })()`), 'sale detail after refresh');
  await click('Satışı tam ləğv et'); await click('Tam qaytarmanı təsdiqlə');
  await waitFor(async () => (await api(biz + '/analytics/overview')).totals.revenue === '90.00', 'refunded revenue');
  await click('Tənzimləmələr'); await waitFor(() => textPresent('AI provayderi'), 'settings');
  await evaluate(`document.querySelector('button[aria-label="Qaranlıq mövzu"]').click()`);
  await screenshot('settings-dark');
  await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await call('Page.navigate', { url: base + '/#assistant' });
  await waitFor(() => textPresent('Biznes köməkçiniz'), 'mobile session restored');
  assert.equal(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), true, 'no mobile page overflow');
  await screenshot('assistant-mobile');
  assert.equal(await evaluate(`document.cookie.includes('hesabatim_session')`), false, 'session must be HttpOnly');
  assert.deepEqual(errors, [], 'no uncaught browser exceptions');
  console.log('PASS: registration, preview isolation, chat confirmation, sale, stock/revenue, dashboard, manual product, manual sale, refund, settings, dark/mobile layout, cookie privacy.');
  console.log('Screenshots: ' + out);
} finally { ws?.close(); browser.kill(); }
