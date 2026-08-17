#!/usr/bin/env node
/**
 * 桥接主流程（防抖累积队列）回归测试 —— 验证"多调用只有第一个回填"bug 的修复。
 *
 * 运行：node bridge/selftest-flow.js
 * 覆盖场景：
 *  1. DOM 通道（流式中间态）先检测到 call1 → 流式继续 → 检测到 call1+call2
 *     → fetch 通道（完整文本）到达 → 两个调用合并为一批执行且只执行一次（不丢不重）
 *  2. 30s 内相同整批调用（用户重试）→ 被去重拦截，不重复执行
 *  3. 新一批不同调用 → 正常执行
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

// ---- 从 ds-bridge.user.js 提取主流程 IIFE（最后一个 (function () { ... }）----
const src = fs.readFileSync(path.join(__dirname, 'ds-bridge.user.js'), 'utf-8');
const marker = 'const core = window.BridgeCore;';
const idx = src.lastIndexOf(marker);
if (idx < 0) { console.error('未找到主流程标记'); process.exit(1); }
const start = src.lastIndexOf('(function () {', idx);
const mainSrc = src.slice(start);

// ---- 加载真实 core.js 的纯函数（解析/检测/格式化）----
const coreSrc = fs.readFileSync(path.join(__dirname, 'core.js'), 'utf-8');
const coreSandbox = {};
vm.createContext(coreSandbox);
vm.runInContext(coreSrc, coreSandbox);
const realCore = coreSandbox.BridgeCore;

let passed = 0, failed = 0;
function check(name, cond, extra) {
  if (cond) { passed++; console.log('  PASS  ' + name); }
  else { failed++; console.log('  FAIL  ' + name + (extra ? '  → ' + extra : '')); }
}
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function run() {
  console.log('桥接主流程自测（防抖累积队列）\n');

  // ---- mock 环境 ----
  const executedCalls = [];   // 后端实际收到的调用（按批次展平）
  const filledTexts = [];     // 回填到输入框的文本
  const statuses = [];        // toast 状态
  let fullHandler = null, domHandler = null;

  const core = {
    config: { backendUrl: 'http://localhost:8088', autoInject: false, autoSend: false, tools: [], rolecard: '' },
    loadConfig() {}, loadRolecard() { return ''; }, loadTools() { return []; },
    async health() { return true; },
    saveReply() {},
    hasToolCalls: (t) => realCore.hasToolCalls(t),
    parseToolCalls: (t) => realCore.parseToolCalls(t),
    formatResults: (r) => realCore.formatResults(r),
    // 外置文件内容端点 mock（enrichExternalized 会调它拉完整内容）
    async request(url) {
      if (String(url).includes('/api/bridge/files/')) {
        return { status: 200, data: { content: '【完整内容】' + '超长结果数据'.repeat(1000) } };
      }
      throw new Error('unexpected request: ' + url);
    },
    async callTools(calls) {
      executedCalls.push(calls.map(c => c.name));
      const results = calls.map(c => {
        // 大文件 read_file → 模拟后端外置
        if (c.name === 'read_file' && c.arguments && c.arguments.path === 'G:/big.txt') {
          return {
            externalized: true,
            note: '结果较长，完整内容已保存到 G:\\backend\\data\\tmp\\result_mock0001.json',
            summary: '【8KB摘要】',
            file: 'G:\\backend\\data\\tmp\\result_mock0001.json',
          };
        }
        return { tool: c.name, status: 'ok', output: 'done:' + c.name };
      });
      return { results };
    },
  };
  const adapter = {
    match: /chat\.deepseek\.com/,
    loadConfig() {}, setConfig() {},
    ensureStatusButton() {},
    setStatus(m) { statuses.push(m); },
    async injectRolecard() { return true; },
    onModelReply(full, dom) { fullHandler = full; domHandler = dom; },
    async fillInput(text) { filledTexts.push(text); return true; },
  };
  const sandbox = {
    window: { BridgeCore: core, DeepSeekAdapter: adapter, location: { href: 'https://chat.deepseek.com/a/b' } },
    location: { href: 'https://chat.deepseek.com/a/b' },
    setTimeout, clearTimeout, console,
  };
  vm.createContext(sandbox);
  vm.runInContext(mainSrc, sandbox);

  await sleep(300); // 等 init() 完成

  // ---- 场景 1：流式多调用合并成一批，只执行一次 ----
  const call1 = 'start:{"name":"read_file","arguments":{"path":"G:/a.txt"}}end';
  const call2 = 'start:{"name":"write_file","arguments":{"path":"G:/b.txt","content":"hi"}}end';
  // DOM 通道：流式中间态先出现 call1
  domHandler('我来看下文件\n' + call1 + '\n');
  await sleep(350); // 流式继续
  // DOM 通道：call2 也输出完
  domHandler('我来看下文件\n' + call1 + '\n' + call2 + '\n正在检查...');
  await sleep(400); // 防抖窗口内
  // fetch 通道：完整文本到达（同一回复）
  fullHandler('我来看下文件\n' + call1 + '\n' + call2 + '\n好的，以上就是结果');
  await sleep(2200); // 防抖 600ms + 执行 + 收尾

  check('多调用合并为一批执行（一次批量调用）', executedCalls.length === 1 && executedCalls[0].length === 2,
    JSON.stringify(executedCalls));
  check('执行顺序 = call1, call2', executedCalls.length === 1 &&
    executedCalls[0][0] === 'read_file' && executedCalls[0][1] === 'write_file',
    JSON.stringify(executedCalls));
  check('结果回填到输入框（含两个工具结果）', filledTexts.length === 1 &&
    filledTexts[0].includes('[工具1执行结果]') && filledTexts[0].includes('[工具2执行结果]'),
    JSON.stringify(filledTexts));

  // ---- 场景 2：30s 内相同整批调用（用户重试/重复触发）→ 去重拦截 ----
  const before = executedCalls.length;
  fullHandler('重试\n' + call1 + '\n' + call2 + '\n');
  await sleep(1500);
  check('30s 内相同调用被去重（不重复执行）', executedCalls.length === before,
    'executedCalls=' + JSON.stringify(executedCalls) + ' before=' + before);

  // ---- 场景 3：不同调用正常执行 ----
  const call3 = 'start:{"name":"read_file","arguments":{"path":"G:/c.txt"}}end';
  fullHandler('再看另一个\n' + call3 + '\n');
  await sleep(1500);
  check('新调用正常执行', executedCalls.length === 2 &&
    executedCalls[1][0] === 'read_file' && executedCalls[1].length === 1,
    JSON.stringify(executedCalls));

  // ---- 场景 4：外置结果自动补全（伪附件）----
  // 后端返回 externalized 结果 → 前端应拉取完整内容替换摘要后回填
  const call4 = 'start:{"name":"read_file","arguments":{"path":"G:/big.txt"}}end';
  const filledBefore = filledTexts.length;
  fullHandler('读大文件\n' + call4 + '\n');
  await sleep(2000);
  const lastFill = filledTexts[filledTexts.length - 1] || '';
  check('外置结果被拉取完整内容内联（不再只有8KB摘要）', filledTexts.length === filledBefore + 1 &&
    lastFill.includes('【完整内容】') && !lastFill.includes('【8KB摘要】'),
    'lastFill len=' + lastFill.length + ' head=' + lastFill.slice(0, 80));

  console.log('\n结果: ' + passed + ' 通过, ' + failed + ' 失败');
  process.exit(failed ? 1 : 0);
}

run().catch(e => { console.error('测试运行异常:', e); process.exit(1); });
