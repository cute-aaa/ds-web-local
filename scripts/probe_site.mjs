#!/usr/bin/env node
/**
 * 站点实测探测器 —— 用真实浏览器打开站点，抓取输入框 / 发送按钮 / 消息容器 / 聊天接口，
 * 并**直接用实测结果生成** bridge/adapters/<site>.js（避免凭空猜测选择器）。
 *
 * 前置：npm i -D playwright-core
 *
 * 用法：
 *   node scripts/probe_site.mjs --site kimi
 *   node scripts/probe_site.mjs --site kimi --send      # 自动发一条消息以捕获聊天接口
 *   node scripts/probe_site.mjs --sites kimi,qwen,doubao
 *   node scripts/probe_site.mjs --list
 *
 * 流程：启动浏览器（复用 shared-profile 的登录态）→ 打开站点 → 等你确认已登录并进入对话页
 *      → 按回车 → 自动探测 → 输出报告并生成适配器。
 *
 * 浏览器目录：默认用仓库内 `.chrome-test/`（首次使用需在该浏览器里登录站点）；
 *            也可用环境变量 CHROME_TEST_DIR（浏览器目录）/ CHROME_PROFILE（用户数据目录）覆盖。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import readline from 'node:readline';
import { chromium } from 'playwright-core';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const BROWSER_DIR = process.env.CHROME_TEST_DIR || path.join(ROOT, '.chrome-test');
const PROFILE_DIR = process.env.CHROME_PROFILE || path.join(BROWSER_DIR, 'shared-profile');
const OUT_DIR = path.join(ROOT, 'scripts', 'probe-out');

// 站点登记表（新增站点只需在这里加一条）
const SITES = {
  deepseek: { name: 'DeepSeek', url: 'https://chat.deepseek.com', match: 'chat\\.deepseek\\.com' },
  mimo: { name: 'MiMo', url: 'https://ultraspeed.xiaomimimo.com', match: 'ultraspeed\\.xiaomimimo\\.com' },
  kimi: { name: 'Kimi', url: 'https://www.kimi.com', match: 'kimi\\.com' },
  qwen: { name: '通义千问', url: 'https://chat.qwen.ai', match: 'chat\\.qwen\\.ai' },
  doubao: { name: '豆包', url: 'https://www.doubao.com/chat/', match: 'doubao\\.com' },
  chatglm: { name: '智谱清言', url: 'https://chatglm.cn', match: 'chatglm\\.cn' },
  chatgpt: { name: 'ChatGPT', url: 'https://chatgpt.com', match: 'chatgpt\\.com' },
  gemini: { name: 'Gemini', url: 'https://gemini.google.com', match: 'gemini\\.google\\.com' },
};

function findChrome() {
  // ChromeTest 下有多个版本目录，取可用的 chrome.exe（优先版本号大的）
  if (!fs.existsSync(BROWSER_DIR)) return null;
  const found = [];
  for (const d of fs.readdirSync(BROWSER_DIR)) {
    for (const sub of ['chrome-win64', 'chrome-win', 'chrome']) {
      const p = path.join(BROWSER_DIR, d, sub, 'chrome.exe');
      if (fs.existsSync(p)) found.push(p);
    }
  }
  found.sort().reverse();
  return found[0] || null;
}

function ask(question) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => rl.question(question, (ans) => { rl.close(); resolve(ans); }));
}

// ---- 页面内探测（在浏览器上下文执行）----
function probeInPage() {
  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const isHashed = (c) => /^[_-]?[a-f0-9]{6,}$/i.test(c) || /^[_-]?[a-z]{0,3}[0-9]{5,}$/i.test(c);
  // 生成尽量稳定的选择器：id > data-testid > 稳定 class > 属性组合
  const bestSelector = (el) => {
    if (el.id && !isHashed(el.id)) return '#' + el.id;
    for (const a of ['data-testid', 'data-test-id', 'data-id', 'data-role', 'name', 'aria-label']) {
      const v = el.getAttribute && el.getAttribute(a);
      if (v && !isHashed(v) && v.length < 40) return `${el.tagName.toLowerCase()}[${a}="${v}"]`;
    }
    const cls = Array.from(el.classList || []).filter((c) => c && !isHashed(c));
    if (cls.length) return el.tagName.toLowerCase() + '.' + cls.slice(0, 2).join('.');
    return null;
  };
  const uniq = (arr) => Array.from(new Set(arr.filter(Boolean)));

  const inputEls = Array.from(document.querySelectorAll(
    'textarea, [contenteditable="true"], [role="textbox"], input[type="text"]')).filter(isVisible);
  const inputs = inputEls.map((el) => ({
    selector: bestSelector(el),
    tag: el.tagName.toLowerCase(),
    placeholder: el.getAttribute('placeholder') || '',
    id: el.id || '',
    className: (el.className && el.className.toString ? el.className.toString() : '').slice(0, 80),
    rect: (({ x, y, width, height }) => ({ x: Math.round(x), y: Math.round(y), width: Math.round(width), height: Math.round(height) }))(el.getBoundingClientRect()),
  }));

  // 发送按钮：优先输入框祖先容器内、靠右、非上传/语音的按钮
  let sendButtons = [];
  const primary = inputEls[0];
  if (primary) {
    let node = primary;
    for (let i = 0; i < 6 && node; i++) {
      node = node.parentElement;
      if (!node) break;
      const btns = Array.from(node.querySelectorAll('button, [role="button"], div[class*="send"], div[class*="Send"]'));
      const vis = btns.filter(isVisible);
      if (vis.length) {
        const filtered = vis.filter((b) => {
          const t = ((b.getAttribute('aria-label') || '') + ' ' + (b.textContent || '')).toLowerCase();
          return !/upload|attach|file|voice|mic|语音|上传|文件|图片/.test(t);
        });
        if (filtered.length) { sendButtons = filtered; break; }
      }
    }
  }
  const sends = sendButtons.map((el) => ({
    selector: bestSelector(el),
    text: (el.textContent || '').trim().slice(0, 30),
    ariaLabel: el.getAttribute('aria-label') || '',
    className: (el.className && el.className.toString ? el.className.toString() : '').slice(0, 80),
  }));

  // 消息容器：取文本最长的若干容器（AI 回复通常最长）
  const msgCandidates = Array.from(document.querySelectorAll(
    'div, article, section, li')).filter((el) => {
      const t = (el.textContent || '').trim();
      return isVisible(el) && t.length > 40 && t.length < 20000 && el.children.length < 60;
    }).sort((a, b) => (b.textContent || '').length - (a.textContent || '').length).slice(0, 8);
  const messages = msgCandidates.map((el) => ({
    selector: bestSelector(el),
    textLen: (el.textContent || '').trim().length,
    className: (el.className && el.className.toString ? el.className.toString() : '').slice(0, 80),
  }));

  return {
    url: location.href,
    title: document.title,
    inputs,
    sends,
    messages,
    hasLoginWall: /login|signin|sign-in|登录|扫码/i.test(document.title + ' ' + location.href),
  };
}

// ---- 适配器代码生成（用实测选择器填充）----
function buildAdapter(site, key, report) {
  const inputs = report.inputs.map((i) => i.selector).filter(Boolean);
  const sends = report.sends.map((s) => s.selector).filter(Boolean);
  const msgs = report.messages.map((m) => m.selector).filter(Boolean).slice(0, 3);
  const list = (arr, fallback) => {
    const items = arr.length ? arr : fallback;
    return items.map((s) => `      '${s.replace(/'/g, "\\'")}',`).join('\n');
  };
  return `/**
 * ${site.name} 适配器（由 scripts/probe_site.mjs 实测生成）
 *
 * 实测时间: ${new Date().toISOString()}
 * 实测页面: ${report.url}
 * 生成后请人工复核选择器，并在 build.py 的 ADAPTERS / HEADER 中登记。
 */
(function (global) {
  'use strict';

  const ${key.charAt(0).toUpperCase() + key.slice(1)}Adapter = {
    id: '${key}',
    name: '${site.name}',
    match: /${site.match}/,

    _toast: null,
    _statusBtn: null,
    _configPanel: null,
    _replyHandler: null,
    _backendUrl: 'http://localhost:8088',
    _autoInject: true,
    _autoSend: false,

    INPUT_SELECTORS: [
${list(inputs, ['textarea', '[contenteditable="true"]'])}
    ],

    SEND_SELECTORS: [
${list(sends, ['button[type="submit"]', 'div[role="button"]'])}
    ],

    MESSAGE_SELECTORS: [
${list(msgs, ['[class*="markdown"]', '[class*="message"]'])}
    ],

    // 聊天接口过滤：务必精确到「生成」接口，避免心跳/埋点轮询触发解析
    API_URL_PATTERN: /chat|completion|conversation|message|assistant|api/i,

    ROLECARD_MARKER: '系统规范（必须严格遵守）',
    SEND_STRATEGY: 'keyboard',
    SEND_KEY_SEQUENCE: [{ key: 'Enter' }],
    MAX_FILL: 60000,
  };

  global.BridgeUI.install(${key.charAt(0).toUpperCase() + key.slice(1)}Adapter);
  global.${key.charAt(0).toUpperCase() + key.slice(1)}Adapter = ${key.charAt(0).toUpperCase() + key.slice(1)}Adapter;
})(typeof window !== 'undefined' ? window : this);
`;
}

async function probeOne(context, key, opts) {
  const site = SITES[key];
  const page = await context.newPage();
  const apiHits = [];
  page.on('request', (req) => {
    const u = req.url();
    if (req.method() === 'POST' && /chat|completion|conversation|message|assistant/i.test(u)) {
      apiHits.push({ url: u.split('?')[0], method: req.method() });
    }
  });

  console.log(`\n=== ${site.name} (${key}) ===`);
  console.log(`打开 ${site.url} ...`);
  try {
    await page.goto(site.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  } catch (e) {
    console.log(`  ! 打开失败: ${e.message}`);
  }
  await page.waitForTimeout(3000);

  console.log('请在浏览器中确认已登录并进入对话页，然后回到这里按回车...');
  await ask('  [回车继续] ');

  const report = await page.evaluate(probeInPage);
  report.apiHits = Array.from(new Set(apiHits.map((h) => h.url))).slice(0, 10);

  console.log(`  标题: ${report.title}`);
  console.log(`  登录墙: ${report.hasLoginWall ? '疑似（未登录）' : '否'}`);
  console.log(`  输入框候选: ${report.inputs.length}`);
  report.inputs.slice(0, 5).forEach((i) => console.log(`    - ${i.selector}  ph="${i.placeholder.slice(0, 30)}"`));
  console.log(`  发送按钮候选: ${report.sends.length}`);
  report.sends.slice(0, 5).forEach((s) => console.log(`    - ${s.selector}  aria="${s.ariaLabel}"`));
  console.log(`  消息容器候选: ${report.messages.length}`);
  report.messages.slice(0, 3).forEach((m) => console.log(`    - ${m.selector}  len=${m.textLen}`));
  console.log(`  捕获 POST 接口: ${report.apiHits.length}`);
  report.apiHits.forEach((u) => console.log(`    - ${u}`));

  if (opts.send) {
    console.log('  自动发送测试消息以捕获聊天接口...');
    try {
      const sel = report.inputs[0] && report.inputs[0].selector;
      if (sel) {
        await page.click(sel, { timeout: 5000 });
        await page.fill(sel, 'hi').catch(() => page.type(sel, 'hi'));
        await page.keyboard.press('Enter');
        await page.waitForTimeout(6000);
        console.log(`  发送后新增接口: ${apiHits.length}`);
        report.apiHits = Array.from(new Set(apiHits.map((h) => h.url))).slice(0, 10);
      }
    } catch (e) {
      console.log(`  ! 自动发送失败（可忽略）: ${e.message}`);
    }
  }

  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(path.join(OUT_DIR, `${key}.json`), JSON.stringify(report, null, 2), 'utf-8');

  if (report.inputs.length) {
    const adapterPath = path.join(ROOT, 'bridge', 'adapters', `${key}.js`);
    fs.writeFileSync(adapterPath, buildAdapter(site, key, report), 'utf-8');
    console.log(`  → 已生成适配器: bridge/adapters/${key}.js`);
  } else {
    console.log('  ! 未找到输入框，疑似未登录或页面结构特殊，未生成适配器');
  }
  console.log(`  → 报告: scripts/probe-out/${key}.json`);

  await page.close();
  return report;
}

async function main() {
  const argv = process.argv.slice(2);
  const getArg = (n) => { const i = argv.indexOf(`--${n}`); return i >= 0 ? argv[i + 1] : null; };

  if (argv.includes('--list')) {
    console.log('可用站点:');
    Object.entries(SITES).forEach(([k, v]) => console.log(`  ${k.padEnd(10)} ${v.name.padEnd(10)} ${v.url}`));
    return;
  }

  const keysArg = getArg('sites') || getArg('site');
  if (!keysArg) {
    console.log('用法: node scripts/probe_site.mjs --site kimi [--send] [--sites a,b,c] [--list]');
    return;
  }
  const keys = keysArg.split(',').map((s) => s.trim()).filter((k) => SITES[k]);
  const unknown = keysArg.split(',').map((s) => s.trim()).filter((k) => !SITES[k]);
  if (unknown.length) console.log(`忽略未登记站点: ${unknown.join(', ')}`);
  if (!keys.length) { console.log('没有可探测的站点'); return; }

  const chromePath = findChrome();
  if (!chromePath) {
    console.error(`未找到 chrome.exe，请设置 CHROME_TEST_DIR（当前: ${BROWSER_DIR}）`);
    process.exit(1);
  }
  console.log(`浏览器: ${chromePath}`);
  console.log(`登录态: ${PROFILE_DIR}`);

  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
    executablePath: chromePath,
    headless: false,
    args: ['--disable-blink-features=AutomationControlled'],
    viewport: { width: 1280, height: 900 },
  });

  const opts = { send: argv.includes('--send') };
  for (const k of keys) {
    try { await probeOne(context, k, opts); }
    catch (e) { console.log(`  ! ${k} 探测异常: ${e.message}`); }
  }

  await context.close();
  console.log('\n全部完成。请人工复核生成的适配器，并在 bridge/build.py 登记后重新打包。');
}

main().catch((e) => { console.error(e); process.exit(1); });
