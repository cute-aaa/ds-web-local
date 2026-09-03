#!/usr/bin/env node
/**
 * 桥接主流程（防抖累积队列 + 自动纠错）回归测试。
 *
 * 运行：node bridge/selftest-flow.js
 *
 * 直接从构建产物 ds-bridge.user.js 里抽取主流程 IIFE 运行，mock 掉 core/adapter，
 * 因此能覆盖「回复到达 → 解析 → 批量调用 → 回填」的真实调度逻辑。
 *
 * 覆盖场景：
 *  1. DOM 通道（流式中间态）先检测到 call1 → 流式继续 → 检测到 call1+call2
 *     → fetch 通道（完整文本）到达 → 两个调用合并为一批执行且只执行一次（不丢不重）
 *  2. 30s 内相同整批调用（用户重试）→ 被去重拦截，不重复执行
 *  3. 新一批不同调用 → 正常执行
 *  4. 外置结果（大文件）→ 按配置决定是否拉取全量内联
 *  5. 格式偏差（旧协议 start:{...}end）→ 不执行调用，自动生成纠错提示
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
// 重试逻辑用到 setTimeout（浏览器里必然存在，Node 的 vm sandbox 需显式注入）
const coreSandbox = { setTimeout, clearTimeout, console };
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
  console.log('桥接主流程自测（防抖累积队列 + 自动纠错）\n');

  // ---- mock 环境 ----
  const executedCalls = [];   // 后端实际收到的调用（按批次展平）
  const filledTexts = [];     // 回填到输入框的文本
  const statuses = [];        // toast 状态
  const traces = [];          // 调用追踪
  const callStates = [];      // 圆点颜色状态序列
  let fullHandler = null, domHandler = null;

  const core = {
    config: {
      backendUrl: 'http://localhost:8088',
      autoInject: false,
      autoSend: false,
      autoEnrichExternalized: false,
      autoRepair: true,
      tools: [],          // 空清单 → 跳过白名单校验（真实环境启动时会拉取）
      rolecard: '',
      callMaxRetries: 0,  // 故障注入用例无需重试退避，缩短用例耗时
      callTotalTimeoutMs: 30000,
    },
    loadConfig() {}, loadRolecard() { return ''; }, loadTools() { return []; },
    async health() { return true; },
    saveReply() {},
    trace(stage, detail) { traces.push(stage + (detail ? ' | ' + detail : '')); },
    ensureCallerId() { return 'c_mock00000001'; },
    // 最后一条命令记忆（需求1）：序列化/抽取委托真实纯函数，保存用数组记录便于断言
    lastCommands: [],
    markedResults: [],
    callsToText: (c) => realCore.callsToText(c),
    extractCommandLines: (t) => realCore.extractCommandLines(t),
    saveLastCommand(text, meta) {
      this.lastCommands.push({ text: text, meta: meta || {} });
      this._lastCommand = {
        text: text, ok: null, source: (meta && meta.source) || 'auto',
        invalid: !!(meta && meta.invalid), count: (meta && meta.count) || 1,
      };
      return this._lastCommand;
    },
    markLastCommandResult(ok, detail) {
      this.markedResults.push({ ok: ok, detail: detail || '' });
      if (this._lastCommand) this._lastCommand.ok = !!ok;
    },
    getLastCommand() { return this._lastCommand || null; },
    // callToolsWithRetry 委托真实实现，内部会调 _meta() 生成 batch_id（真实 core 有此方法）
    _meta() { return { caller_id: 'c_mock00000001', tab_id: 't_mock01', batch_id: 'b_mock01' }; },
    // 判定为幂等 → 用于验证「只读结果缓存」路径（真正实现按 registry 元数据判定）
    isIdempotentTool() { return true; },
    // 只读结果缓存（前端 20s，先于后端 30s 幂等去重命中）
    _cache: {},
    getCachedResult(name, args) {
      return this._cache[name + '|' + JSON.stringify(args || {})] || null;
    },
    cacheResult(name, args, result) {
      this._cache[name + '|' + JSON.stringify(args || {})] = result;
    },
    hasToolCalls: (t) => realCore.hasToolCalls(t),
    parseToolCalls: (t) => realCore.parseToolCalls(t),
    formatResults: (r) => realCore.formatResults(r),
    // 外置文件内容端点 mock（enrichExternalized 会调它拉完整内容）
    async request(url) {
      if (String(url).includes('/api/bridge/attachments') ||
          String(url).includes('/api/bridge/files/')) {
        return { status: 200, data: { content: '【完整内容】' + '超长结果数据'.repeat(1000) } };
      }
      throw new Error('unexpected request: ' + url);
    },
    async callTools(calls) {
      // 故障注入（场景 8）：用于验证「执行失败 → 命令留存 + 失败标记」
      if (calls.some(c => c.arguments && c.arguments.path === 'G:/boom.txt')) {
        throw new Error('backend down');
      }
      executedCalls.push(calls.map(c => c.name));
      const results = calls.map(c => {
        // 大文件 read_file → 模拟后端外置（big.txt / big2.txt 两个 request_id 各用一次）
        if (c.name === 'read_file' && c.arguments && /^G:\/big2?\.txt$/.test(c.arguments.path || '')) {
          const rid = c.arguments.path === 'G:/big2.txt' ? 'r_mock0002' : 'r_mock0001';
          // 字段与后端真实信封对齐（tool/status/truncated/summary/file 缺一不可）
          return {
            tool: 'read_file', status: 'ok', request_id: rid,
            arguments: { path: c.arguments.path },
            externalized: true, truncated: true,
            summary: '【8KB摘要】',
            file: 'G:\\backend\\data\\tmp\\result_' + rid + '.json',
          };
        }
        // arguments 回显必须有：真实后端信封会回显它，缓存 key 正是由它构成
        return { tool: c.name, status: 'ok', arguments: c.arguments || {},
          output: 'done:' + c.name };
      });
      return { results };
    },
    // 重试逻辑委托真实实现（callTools 仍是 mock，不发真实请求）：
    // 覆盖真实的「整批全幂等才重试 / 线性退避 / 总超时」判定，而非一个假包装
    callToolsWithRetry: (calls, meta) => realCore.callToolsWithRetry.call(core, calls, meta),
  };
  const adapter = {
    match: /chat\.deepseek\.com/,
    loadConfig() {}, setConfig() {},
    ensureStatusButton() {},
    setStatus(m) { statuses.push(m); },
    setCallState(s) { callStates.push(s); },   // 圆点颜色状态（蓝=执行中/绿=成功/红=失败/橙=待处理）
    async injectRolecard() { return true; },
    onModelReply(full, dom) { fullHandler = full; domHandler = dom; },
    async fillInput(text) { filledTexts.push(text); return true; },
    async autoSend() { return true; },
  };
  const listeners = {};   // 记录 init() 注册的 window 事件（会话切换用例需要主动触发）
  const sandbox = {
    window: {
      BridgeCore: core, DeepSeekAdapter: adapter,
      location: { href: 'https://chat.deepseek.com/a/b' },
      addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    },
    // 会话切换保护会劫持 history.pushState/replaceState，sandbox 必须提供；
    // 切会话的可见表现就是 location.href 变化
    history: {
      pushState() { sandbox.location.href = 'https://chat.deepseek.com/a/changed'; },
      replaceState() { sandbox.location.href = 'https://chat.deepseek.com/a/changed'; },
    },
    location: { href: 'https://chat.deepseek.com/a/b' },
    setTimeout, clearTimeout, console,
  };
  vm.createContext(sandbox);
  vm.runInContext(mainSrc, sandbox);

  await sleep(300); // 等 init() 完成

  // ---- 场景 1：流式多调用合并成一批，只执行一次 ----
  const call1 = '@@tool read_file {"path":"G:/a.txt"}';
  const call2 = '@@tool write_file {"path":"G:/b.txt","content":"hi"}';
  // DOM 通道：流式中间态先出现 call1
  domHandler('我来看下文件\n' + call1 + '\n');
  await sleep(350); // 流式继续
  // DOM 通道：call2 也输出完
  domHandler('我来看下文件\n' + call1 + '\n' + call2 + '\n正在检查...');
  await sleep(400); // 防抖窗口内
  // fetch 通道：完整文本到达（同一回复）
  fullHandler('我来看下文件\n' + call1 + '\n' + call2 + '\n好的，以上就是结果');
  await sleep(3000); // 防抖 600ms + 执行 + 静默窗口 1500ms + 余量

  check('多调用合并为一批执行（一次批量调用）', executedCalls.length === 1 && executedCalls[0].length === 2,
    JSON.stringify(executedCalls));
  check('执行顺序 = call1, call2', executedCalls.length === 1 &&
    executedCalls[0][0] === 'read_file' && executedCalls[0][1] === 'write_file',
    JSON.stringify(executedCalls));
  check('结果回填到输入框（两个工具结果各一段）', filledTexts.length === 1 &&
    (filledTexts[0].match(/【工具结果/g) || []).length === 2,
    JSON.stringify(filledTexts).slice(0, 200));
  check('回填结果附带格式提醒（对抗长对话遗忘）', filledTexts.length === 1 &&
    filledTexts[0].includes('【工具调用格式】') && filledTexts[0].includes('@@tool'),
    JSON.stringify(filledTexts[0] || '').slice(-200));
  check('圆点状态随调用流转（执行中→就绪）',
    callStates.indexOf('busy') >= 0 && callStates.indexOf('connected') >= 0,
    JSON.stringify(callStates));
  check('解析出调用即保存最后一条命令（最终含两条调用，供面板手动重试）',
    core.lastCommands.some(c => c.text.indexOf('@@tool read_file') >= 0 &&
      c.text.indexOf('@@tool write_file') >= 0),
    JSON.stringify(core.lastCommands.slice(0, 3)));

  // ---- 场景 2：30s 内相同整批调用（用户重试/重复触发）→ 去重拦截 ----
  const before = executedCalls.length;
  fullHandler('重试\n' + call1 + '\n' + call2 + '\n');
  await sleep(1500);
  // 整批相同 → 在 handleToolCalls 之前就被 30s 幂等批去重拦下（更早返回，优于缓存）
  check('重复调用不重复执行（30s 幂等批去重）', executedCalls.length === before,
    'executedCalls=' + JSON.stringify(executedCalls) + ' before=' + before);

  // ---- 场景 3：不同调用正常执行 ----
  const call3 = '@@tool read_file {"path":"G:/c.txt"}';
  fullHandler('再看另一个\n' + call3 + '\n');
  await sleep(2800);
  check('新调用正常执行', executedCalls.length === 2 &&
    executedCalls[1][0] === 'read_file' && executedCalls[1].length === 1,
    JSON.stringify(executedCalls));

  // ---- 场景 3b：新批次混入已执行过的只读调用 → 缓存按「单个调用」粒度命中 ----
  // 整批 key 与前不同（不会被 30s 批去重拦截），其中 G:/a.txt 在场景 1 已执行过，
  // 只有 G:/d.txt 需要真正打后端 —— 这正是缓存相对批去重的增量价值。
  const beforeCache = executedCalls.length;
  fullHandler('还要看\n@@tool read_file {"path":"G:/a.txt"}\n@@tool read_file {"path":"G:/d.txt"}\n');
  await sleep(2800);
  const lastBatch = executedCalls[executedCalls.length - 1] || [];
  check('缓存按调用粒度命中（只执行未缓存的新调用）',
    executedCalls.length === beforeCache + 1 && lastBatch.length === 1,
    'executedCalls=' + JSON.stringify(executedCalls));
  check('缓存命中写入追踪', traces.some((t) => t.indexOf('cache/hit') === 0),
    JSON.stringify(traces.slice(-3)));

  // ---- 场景 4：外置结果自动补全（需开启 autoEnrichExternalized）----
  const call4 = '@@tool read_file {"path":"G:/big.txt"}';
  const filledBefore = filledTexts.length;
  fullHandler('读大文件\n' + call4 + '\n');
  await sleep(2800);
  const lastFill = filledTexts[filledTexts.length - 1] || '';
  check('外置结果按开关决定是否内联（默认关：保留预览）',
    filledTexts.length === filledBefore + 1 && lastFill.includes('【8KB摘要】'),
    'lastFill len=' + lastFill.length + ' head=' + lastFill.slice(0, 80));
  check('外置大结果提示用户上传附件（需求2 用户侧提示）',
    statuses.some(s => s.indexOf('附件/📎') >= 0), JSON.stringify(statuses.slice(-3)));

  // ---- 场景 4b：开启 autoEnrichExternalized → 全量内联，不再提示上传附件 ----
  // _enriched 标记必须让「上传附件」提示消失，否则用户开了内联开关仍被反复打扰。
  core.config.autoEnrichExternalized = true;
  const call4b = '@@tool read_file {"path":"G:/big2.txt"}';
  const statusBefore4b = statuses.length;
  fullHandler('再读一个大文件\n' + call4b + '\n');
  await sleep(2800);
  const fill4b = filledTexts[filledTexts.length - 1] || '';
  check('开启内联后大结果全文回填', fill4b.includes('【完整内容】'),
    'len=' + fill4b.length + ' head=' + fill4b.slice(0, 60));
  check('开启内联后不再提示上传附件（_enriched 抑制）',
    !statuses.slice(statusBefore4b).some(s => s.indexOf('附件/📎') >= 0),
    JSON.stringify(statuses.slice(statusBefore4b)));
  core.config.autoEnrichExternalized = false;

  // ---- 场景 5：格式偏差（旧协议）→ 不执行调用，自动生成纠错提示 ----
  const execBefore = executedCalls.length;
  const fillBefore5 = filledTexts.length;
  fullHandler('start:{"name":"read_file","arguments":{"path":"G:/x.txt"}}end');
  await sleep(2500);
  const repairFill = filledTexts[filledTexts.length - 1] || '';
  check('旧协议不执行任何调用', executedCalls.length === execBefore,
    'executedCalls=' + JSON.stringify(executedCalls));
  check('旧协议触发自动纠错提示', filledTexts.length === fillBefore5 + 1 &&
    repairFill.includes('未被识别') && repairFill.includes('@@tool read_file'),
    'repairFill=' + repairFill.slice(0, 160));
  check('纠错写入调用追踪', traces.some((t) => t.indexOf('MISMATCH/未触发') === 0) &&
    traces.some((t) => t.indexOf('autoRepair') === 0),
    JSON.stringify(traces.slice(-4)));
  check('格式偏差时圆点转警告色', callStates.indexOf('warning') >= 0,
    JSON.stringify(callStates));
  check('格式偏差也会保存现场（标记 invalid，供人工修正）',
    core.lastCommands.some(c => c.meta.invalid && String(c.text).indexOf('start:') >= 0),
    JSON.stringify(core.lastCommands.slice(-1)));

  // ---- 场景 6：会话切换保护（冷却期延后执行，绝不丢调用）----
  // 冷却的真实用意是避开切会话瞬间 DOM 未稳定的窗口；若实现成「冷却期内丢弃检测」，
  // 一旦误判（URL 规范化、新建会话回写 id 都会改 href）就会永久丢掉用户的调用。
  core.config.sessionSwitchCooldownMs = 1200;   // 缩短冷却，控制用例耗时
  const execBefore6 = executedCalls.length;
  sandbox.location.href = 'https://chat.deepseek.com/a/other-session';
  (listeners['hashchange'] || []).forEach(fn => fn());
  fullHandler('切会话后\n@@tool read_file {"path":"G:/sw.txt"}\n');

  await sleep(800);   // 防抖 600ms 已过，但仍在 1200ms 冷却中 → 不应执行
  check('会话切换冷却期内调用被延后而非立即执行', executedCalls.length === execBefore6,
    'executedCalls=' + JSON.stringify(executedCalls) + ' before=' + execBefore6);

  await sleep(3500);  // 冷却结束 → 延后的调用应被执行（不是被丢弃）
  check('冷却结束后延后的调用被执行（绝不丢失）',
    executedCalls.length === execBefore6 + 1 &&
    executedCalls[executedCalls.length - 1][0] === 'read_file',
    'executedCalls=' + JSON.stringify(executedCalls) + ' before=' + execBefore6);
  check('会话切换写入调用追踪', traces.some((t) => t.indexOf('session/switch') === 0),
    JSON.stringify(traces.slice(-3)));

  // ---- 场景 7：切会话恰好发生在防抖窗口内（队列绝不能卡死）----
  // 延后方案最易踩的坑：onSessionChange 清掉定时器后若不重排，后续重复检测会因去重
  // 命中（added===0）直接 return，pendingCalls 就再也没人执行 → 永久「没反应」
  const execBefore7 = executedCalls.length;
  fullHandler('切会话前\n@@tool read_file {"path":"G:/defer.txt"}\n');
  await sleep(150);                       // 防抖 600ms 尚未触发
  sandbox.location.href = 'https://chat.deepseek.com/a/yet-another';
  (listeners['hashchange'] || []).forEach(fn => fn());   // 冷却中清掉定时器
  await sleep(4500);                      // 冷却结束 + 重排执行 + 静默窗口
  check('切会话发生在防抖窗口内时队列不卡死（调用最终仍执行）',
    executedCalls.length === execBefore7 + 1,
    'executedCalls=' + JSON.stringify(executedCalls) + ' before=' + execBefore7);

  // ---- 场景 8：执行失败 → 命令留存 + 失败标记 + 提示去面板手动重试（需求1 核心场景）----
  // 自动执行失败时用户要能「改一点再跑」，因此命令必须在解析阶段就落存；失败只更新 ok 标记，
  // 绝不丢弃命令文本。这里注入一个必失败的调用验证整条链路。
  const boom = '@@tool read_file {"path":"G:/boom.txt"}';
  fullHandler('跑个会失败的\n' + boom + '\n');
  await sleep(3000);
  check('执行失败时最后一条命令已保存（面板可手动重试）',
    core.lastCommands.some(c => String(c.text).indexOf('G:/boom.txt') >= 0),
    JSON.stringify(core.lastCommands.slice(-2)));
  check('执行失败写入失败标记（面板提示「执行失败」）',
    core.markedResults.some(r => r.ok === false),
    JSON.stringify(core.markedResults.slice(-3)));
  check('执行失败提示指向面板「手动执行」',
    statuses.some(s => s.indexOf('手动执行') >= 0), JSON.stringify(statuses.slice(-3)));

  console.log('\n结果: ' + passed + ' 通过, ' + failed + ' 失败');
  process.exit(failed ? 1 : 0);
}

run().catch(e => { console.error('测试运行异常:', e); process.exit(1); });
