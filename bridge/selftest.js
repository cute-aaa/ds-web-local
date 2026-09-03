#!/usr/bin/env node
/**
 * 桥接链路自测工具（Node 运行，无需浏览器）：
 * 验证 core.js 的解析/检测/格式化纯函数在真实场景下的行为。
 *
 * 运行：node bridge/selftest.js
 *
 * 协议（v4 行锚定）：一条调用独占一行，行首 @@tool + 工具名 + 单行 JSON 参数，
 * **无终止符**（旧协议 start:{...}end 已废弃，其最大缺陷就是"漏写 end 即静默失效"）。
 *
 * 覆盖：无终止符 / 代码块包裹 / 跨行 JSON / SSE 解码 / Windows 路径 / 多工具 / 中文 /
 *       嵌套 JSON / 白名单 / 行首文字不误判 / 旧协议不识别 / 流式中间态 / 回显格式
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

// 加载 core.js 提取 BridgeCore
// 重试逻辑用到 setTimeout（浏览器里必然存在，Node 的 vm sandbox 需显式注入）
const src = fs.readFileSync(path.join(__dirname, 'core.js'), 'utf-8');
const sandbox = { setTimeout, clearTimeout, console };
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const core = sandbox.BridgeCore;

// 工具清单（白名单校验用例用；不传清单时不校验，见 _toolNames）
const TOOLS = [
  { name: 'read_file', idempotent: true },
  { name: 'write_file', idempotent: false },
  { name: 'list_directory', idempotent: true },
  { name: 'run_in_terminal', idempotent: false },
  { name: 'mcp__windows-mcp__PowerShell', idempotent: false },
  { name: 'mcp__windows-mcp__App', idempotent: false },
];

let passed = 0, failed = 0;
function check(name, cond, extra) {
  if (cond) { passed++; console.log('  PASS  ' + name); }
  else { failed++; console.log('  FAIL  ' + name + (extra ? '  → ' + extra : '')); }
}

console.log('桥接链路自测（core.js 纯函数 · @@tool 行锚定协议）\n');

// 1. 基本调用：没有终止符也要能识别（旧协议最高频故障）
{
  const t = '我需要读取文件\n@@tool read_file {"path":"G:/x.txt"}\n请稍等';
  const calls = core.parseToolCalls(t, TOOLS);
  check('行锚定单工具（无终止符）', calls.length === 1 && calls[0].name === 'read_file' &&
    calls[0].arguments.path === 'G:/x.txt', JSON.stringify(calls));
}
// 2. 无参数调用
{
  const t = '@@tool list_directory';
  const calls = core.parseToolCalls(t, TOOLS);
  check('无参数调用 → {}', calls.length === 1 && calls[0].name === 'list_directory' &&
    Object.keys(calls[0].arguments).length === 0, JSON.stringify(calls));
}
// 3. 代码块包裹（模型常把调用写进 ``` 里）—— v3 的致命诱因之一
{
  const t = '```\n@@tool read_file {"path":"G:/a.txt"}\n@@tool write_file {"path":"G:/b.txt","content":"hi"}\n```';
  const calls = core.parseToolCalls(t, TOOLS);
  check('代码块包裹仍可识别', calls.length === 2 &&
    calls[0].name === 'read_file' && calls[1].name === 'write_file', JSON.stringify(calls));
}
// 4. 跨行 JSON（模型 pretty-print）→ 括号平衡续读
{
  const t = '@@tool write_file {\n  "path": "G:/a.txt",\n  "content": "hi"\n}';
  const calls = core.parseToolCalls(t, TOOLS);
  check('跨行 JSON 续读', calls.length === 1 && calls[0].name === 'write_file' &&
    calls[0].arguments.content === 'hi', JSON.stringify(calls));
}
// 5. SSE 原始流 → 解码后行锚定（fetch 通道必经路径）
{
  const sse = 'data: {"v":"好的\\n"}\n\ndata: {"v":"@@tool read_file {\\"path\\":\\"G:/a.txt\\"}\\n"}';
  const decoded = core.decodeReply(sse);
  const calls = core.parseToolCalls(decoded, TOOLS);
  check('SSE 解码 + 行锚定', calls.length === 1 && calls[0].name === 'read_file' &&
    calls[0].arguments.path === 'G:/a.txt',
    'decoded=' + JSON.stringify(decoded) + ' calls=' + JSON.stringify(calls));
}
// 6. SSE 解码回退：非 SSE 文本原样返回（不丢信息）
{
  const plain = '@@tool read_file {"path":"G:/a.txt"}';
  check('非 SSE 文本原样返回', core.decodeReply(plain) === plain);
}
// 7. Windows 路径非法转义（\D → 宽容解析）—— 核心场景
{
  const t = "@@tool mcp__windows-mcp__PowerShell {\"command\":\"Test-Path 'G:\\Download'\"}";
  const calls = core.parseToolCalls(t, TOOLS);
  check('Windows 路径非法转义 \\D', calls.length === 1 &&
    calls[0].arguments.command === "Test-Path 'G:\\Download'", JSON.stringify(calls));
}
// 8. 多工具（多行）
{
  const t = '@@tool read_file {"path":"G:/a.txt"}\n@@tool write_file {"path":"G:/b.txt","content":"x"}';
  const calls = core.parseToolCalls(t, TOOLS);
  check('多工具按行解析', calls.length === 2 && calls[1].name === 'write_file', JSON.stringify(calls));
}
// 9. 中文内容
{
  const t = '@@tool write_file {"path":"G:/a.txt","content":"你好世界"}';
  const calls = core.parseToolCalls(t, TOOLS);
  check('中文内容', calls.length === 1 && calls[0].arguments.content === '你好世界', JSON.stringify(calls));
}
// 10. 嵌套 JSON（含数组/对象）
{
  const t = '@@tool mcp__windows-mcp__App {"mode":"launch","args":["-h","-v"],"meta":{"n":3}}';
  const calls = core.parseToolCalls(t, TOOLS);
  check('嵌套 JSON（数组+对象）', calls.length === 1 &&
    calls[0].arguments.args.length === 2 && calls[0].arguments.meta.n === 3, JSON.stringify(calls));
}
// 11. 白名单：清单外的工具名直接拒绝（杜绝误触发）
{
  const t = '@@tool not_a_real_tool {"x":1}';
  const calls = core.parseToolCalls(t, TOOLS);
  check('白名单拒绝清单外工具', calls.length === 0, JSON.stringify(calls));
}
// 12. 行首有文字 → 不识别（防止模型"谈论"调用时被误触发）
{
  const t = '请看这个调用：@@tool read_file {"path":"G:/a.txt"}';
  check('行首有文字不识别', core.parseToolCalls(t, TOOLS).length === 0);
}
// 13. 旧协议 start:{...}end 不再被识别（已废弃）
{
  const t = 'start:{"name":"read_file","arguments":{"path":"G:/a.txt"}}end';
  check('旧协议不再识别', core.hasToolCalls(t) === false &&
    core.parseToolCalls(t, TOOLS).length === 0);
}
// 14. 普通聊天文本不误判
{
  const t = 'start 是一段正常文本 end，不是工具调用';
  check('非工具文本不误判', core.parseToolCalls(t, TOOLS).length === 0 &&
    core.hasToolCalls(t) === false);
}
// 15. 流式中间态（JSON 未闭合）不崩溃、不误触发
{
  const t = '@@tool mcp__windows-mcp__PowerShell {"command":"Get-ChildItem -Pa';
  const calls = core.parseToolCalls(t, TOOLS);
  check('流式中间态不崩溃且不误触发', Array.isArray(calls) && calls.length === 0, JSON.stringify(calls));
}
// 16. 真实场景：完整 PowerShell 命令（管道/特殊字符）
{
  const t = '@@tool mcp__windows-mcp__PowerShell {"command":"Get-ChildItem -Path \'G:/Download\' -File | Sort-Object Length -Descending"}';
  const calls = core.parseToolCalls(t, TOOLS);
  check('真实 PowerShell 命令', calls.length === 1 &&
    calls[0].arguments.command.includes('Sort-Object'), JSON.stringify(calls[0]));
}
// 17. JSON 值内未转义引号（模型常见错误）
{
  const t = '@@tool mcp__windows-mcp__PowerShell {"command":"Get-ChildItem -Path "G:/Download" -Recurse -File"}';
  const calls = core.parseToolCalls(t, TOOLS);
  check('值内未转义引号修复', calls.length === 1 &&
    calls[0].arguments.command === 'Get-ChildItem -Path "G:/Download" -Recurse -File', JSON.stringify(calls));
}
// 18. 正常转义不受影响（回归）
{
  const t = '@@tool write_file {"path":"a.txt","content":"他说\\"你好\\""}';
  const calls = core.parseToolCalls(t, TOOLS);
  check('正常转义回归', calls.length === 1 && calls[0].arguments.content === '他说"你好"', JSON.stringify(calls));
}
// 19. formatResults ok 信封：回显必须是新协议格式（否则模型照着回显模仿 → 格式倒退）
{
  const out = core.formatResults([{
    request_id: 'r_abc123', tool: 'read_file', status: 'ok', error: null,
    approval_required: false, externalized: false, file: null,
    arguments: { path: 'G:/a.txt' }, elapsed_ms: 5,
    result: { path: 'G:/a.txt', content: 'hi' },
  }]);
  check('formatResults ok 信封（@@tool 回显）', out.includes('【工具结果 #r_abc123】read_file') &&
    out.includes('@@tool read_file {"path":"G:/a.txt"}') &&
    out.includes('"content": "hi"'), out);
}
// 20. formatResults null → 显式说明
{
  const out = core.formatResults([{
    request_id: 'r_n1', tool: 'list_directory', status: 'ok', error: null,
    approval_required: false, file: null, arguments: { path: '.' }, result: null,
  }]);
  check('formatResults null 显式', out.includes('null') && out.includes('成功') && out.trim().length > 10, out);
}
// 21. formatResults error 信封
{
  const out = core.formatResults([{
    request_id: 'r_e1', tool: 'sed_replace', status: 'error',
    error: { type: 'ValueError', message: 'pattern not found' },
    approval_required: false, file: null, arguments: { pattern: 'x' }, result: null,
  }]);
  check('formatResults error', out.includes('ValueError') && out.includes('pattern not found'), out);
}
// 22. formatResults approval_required
{
  const out = core.formatResults([{
    request_id: 'r_p1', tool: 'run_in_terminal', status: 'approval_required',
    approval_required: true, error: null, file: null, arguments: { cmd: 'x' }, result: null,
  }]);
  check('formatResults approval', out.includes('人工审批') && out.includes('r_p1'), out);
}
// 23. formatResults 大结果（truncated）：预览 + 附件路径
{
  const out = core.formatResults([{
    request_id: 'r_big', tool: 'read_file', status: 'ok', error: null,
    approval_required: false, truncated: true, externalized: true,
    file: 'G:/data/attachments/anon/2026-09-03/result_r_big.json',
    summary: '这是预览……', arguments: { path: 'G:/b.txt' }, result: '这是预览……',
  }]);
  check('formatResults truncated', out.includes('这是预览') && out.includes('result_r_big.json'), out);
}
// 24. formatResults 多信封顺序
{
  const mk = (rid, name) => ({
    request_id: rid, tool: name, status: 'ok', error: null,
    approval_required: false, file: null, arguments: {}, result: 1,
  });
  const out = core.formatResults([mk('r_a', 'read_file'), mk('r_b', 'write_file')]);
  check('formatResults 多信封顺序', out.indexOf('#r_a') < out.indexOf('#r_b') &&
    (out.match(/【工具结果/g) || []).length === 2, out);
}
// 25. markdown 特殊字符兼容性：模型输出经网页端渲染后常混入隐形字符与装饰符，
//     不可见却会打断行首锚定、破坏 JSON 解析（实测 DeepSeek 会把 '-' 渲染成 U+2212）。
{
  const LQ = String.fromCharCode(0x201C), RQ = String.fromCharCode(0x201D);
  const NBSP = String.fromCharCode(0xA0), ZWSP = String.fromCharCode(0x200B);
  const cases = [
    ['弯引号', '@@tool read_file {' + LQ + 'path' + RQ + ':' + LQ + 'G:/a.txt' + RQ + '}'],
    ['前导NBSP', NBSP + '@@tool read_file {"path":"G:/a.txt"}'],
    ['JSON内零宽空格', '@@tool read_file {"path":' + ZWSP + '"G:/a.txt"}'],
    ['有序列表', '1. @@tool read_file {"path":"G:/a.txt"}'],
    ['加粗装饰', '**@@tool** read_file {"path":"G:/a.txt"}'],
    ['表格单元', '| @@tool read_file {"path":"G:/a.txt"} |'],
    ['标题', '# @@tool read_file {"path":"G:/a.txt"}'],
  ];
  const bad = [];
  for (const [n, t] of cases) {
    const r = core.parseToolCalls(t, TOOLS);
    if (!(r.length === 1 && r[0].name === 'read_file' &&
      r[0].arguments.path === 'G:/a.txt')) {
      bad.push(n + (r.length ? '→' + JSON.stringify(r[0]) : '→未匹配'));
    }
  }
  check('markdown 特殊字符均可识别（7 种）', bad.length === 0, bad.join(' '));
}
// 26. 行首有文字仍拒绝（放宽装饰符后，防误触发的底线不能破）
{
  const t = '请看：@@tool read_file {"path":"G:/a.txt"}';
  check('行首有文字仍拒绝', core.parseToolCalls(t, TOOLS).length === 0);
}
// 27. 必填参数缺失 → 不执行（避免发出注定失败的空参数调用）
{
  const T2 = [{ name: 'run_in_terminal', idempotent: false,
    input_schema: { type: 'object', properties: { command: { type: 'string' } },
      required: ['command'] } }];
  check('必填参数缺失不执行', core.parseToolCalls('@@tool run_in_terminal', T2).length === 0);
  check('必填参数齐全时执行',
    core.parseToolCalls('@@tool run_in_terminal {"command":"echo hi"}', T2).length === 1);
}
// 28. 调用追踪 API（面板「调用追踪」区依赖）
{
  core.clearTrace();
  core.trace('stage/x', 'detail-y');
  const rows = core.getTrace();
  check('trace API', rows.length === 1 && rows[0].stage === 'stage/x' &&
    core.traceText().includes('detail-y'), JSON.stringify(rows));
  core.clearTrace();
  check('clearTrace', core.getTrace().length === 0);
}
// 26. isIdempotentTool：读 registry 元数据（幂等批去重判定）
{
  core.config.tools = [
    { name: 'read_file', idempotent: true },
    { name: 'write_file', idempotent: false },
  ];
  check('isIdempotentTool', core.isIdempotentTool('read_file') === true &&
    core.isIdempotentTool('write_file') === false &&
    core.isIdempotentTool('unknown') === false);
  core.config.tools = [];
}

// 30. 最后一条命令记忆（面板「手动执行」框自动回填的数据基础）
{
  const calls = [
    { name: 'read_file', arguments: { path: 'G:/a.txt' } },
    { name: 'mcp__windows-mcp__PowerShell', arguments: { command: "Get-ChildItem 'G:/'" } },
  ];
  const text = core.callsToText(calls);
  check('callsToText → 一行一条 @@tool（可直接回填执行）',
    text.split('\n').length === 2 && text.split('\n')[0] === '@@tool read_file {"path":"G:/a.txt"}',
    text);

  const raw = '前言\n@@tool write_file {"path":"G:/b.txt","content":"hi"}\n```\n' +
    '@@tool list_directory {"path":"G:/"}';
  const lines = core.extractCommandLines(raw);
  check('extractCommandLines 抽取 @@tool 行（解析失败时保留现场）',
    lines.split('\n').length === 2 && lines.indexOf('@@tool write_file') >= 0, lines);
}
{
  const saved = core.saveLastCommand('@@tool read_file {"path":"G:/a.txt"}',
    { source: 'auto', count: 1 });
  const got = core.getLastCommand();
  check('saveLastCommand 记忆命令并可取回',
    !!saved && !!got && got.text.indexOf('@@tool read_file') === 0, JSON.stringify(got));
  core.markLastCommandResult(false, '后端 500');
  const lc = core.getLastCommand();
  check('markLastCommandResult 标记失败但不改写命令文本',
    lc.ok === false && lc.reason === '后端 500' && lc.text.indexOf('@@tool read_file') === 0,
    JSON.stringify(lc));
  check('lastCommandLabel 含执行状态', core.lastCommandLabel().indexOf('执行失败') >= 0,
    core.lastCommandLabel());
  core.saveLastCommand('start:{"name":"read_file","arguments":{}}end',
    { source: 'auto', invalid: true, reason: '旧协议' });
  check('未识别命令同样留存并标记 invalid',
    core.getLastCommand().invalid === true &&
    core.lastCommandLabel().indexOf('未识别') >= 0, core.lastCommandLabel());
}

// 31. 大结果格式化：改走「提示用户上传附件」，不再默认让模型 read_file 全文
{
  const env = {
    tool: 'read_file', status: 'ok', request_id: 'r_1', arguments: { path: 'G:/big.txt' },
    truncated: true, externalized: true, summary: '【摘要】',
    file: 'G:/att/result_r_1.json', file_md: 'G:/att/result_r_1.md',
  };
  const s = core._formatOneResult(env);
  check('大结果提示用户上传附件（含附件路径）',
    s.indexOf('附件/📎') >= 0 && s.indexOf('result_r_1.json') >= 0, s);
  check('大结果明确不默认 read_file 全文',
    s.indexOf('不要默认自己 read_file 全文') >= 0, s);
  const enriched = core._formatOneResult(Object.assign({}, env, { _enriched: true, summary: '【全文】' }));
  check('已内联大结果（autoEnrichExternalized）不再提示上传附件',
    enriched.indexOf('附件/📎') < 0 && enriched.indexOf('【全文】') >= 0, enriched.slice(0, 120));
}

// 32. 面板手动执行框同步：无 DOM 不崩；有 DOM 时自动回填，且不覆盖用户正在编辑的内容
{
  core._manualDirty = false;
  let threw = false;
  try {
    core.saveLastCommand('@@tool read_file {"path":"G:/x.txt"}', { source: 'auto' });
  } catch (e) { threw = true; }
  check('无 DOM 环境下命令记忆不抛异常', !threw);

  // 伪造最小 DOM（_syncManualPanel 只用到 getElementById）
  const els = {
    'dsw-manual': { value: '' },
    'dsw-manual-hint': { textContent: '' },
  };
  sandbox.document = { getElementById: (id) => els[id] || null };
  core._manualDirty = false;
  core._syncManualPanel();
  check('面板自动回填最后一条命令', els['dsw-manual'].value.indexOf('@@tool read_file') === 0,
    els['dsw-manual'].value);

  core._manualDirty = true;   // 模拟用户已开始编辑
  core.saveLastCommand('@@tool list_directory {"path":"G:/"}', { source: 'auto' });
  check('用户编辑后新命令不覆盖输入框',
    els['dsw-manual'].value.indexOf('read_file') >= 0, els['dsw-manual'].value);
  check('提示标注「已修改」', els['dsw-manual-hint'].textContent.indexOf('已修改') >= 0,
    els['dsw-manual-hint'].textContent);

  delete sandbox.document;
  core._manualDirty = false;
}

// 29. 幂等重试：只有「整批全为只读工具」且「请求层失败（request reject）」才重试。
// 写入/终端/MCP 重复执行即是事故，绝不能自动重试；HTTP 200 里的业务错误
// （status:error / approval_required）是真实结果，也不应重试 —— 本方法只在
// callTools 抛异常时进入重试分支，因此天然满足这一点。
(async function () {
  const savedTools = core.config.tools;
  const savedMax = core.config.callMaxRetries;
  const savedCall = core.callTools;
  core.config.tools = TOOLS;
  core.config.callMaxRetries = 1;   // 只退避 1s，控制用例耗时

  let n1 = 0;
  core.callTools = async function () {
    n1++;
    if (n1 === 1) throw new Error('network down');
    return { results: [{ tool: 'read_file', status: 'ok' }] };
  };
  let ok1 = false;
  try {
    const r = await core.callToolsWithRetry([{ name: 'read_file', arguments: {} }]);
    ok1 = !!(r && r.results);
  } catch (e) { /* 记为失败 */ }
  check('只读工具请求失败会重试并最终成功', ok1 && n1 === 2, 'n=' + n1);

  let n2 = 0;
  core.callTools = async function () { n2++; throw new Error('boom'); };
  let threw2 = false;
  try { await core.callToolsWithRetry([{ name: 'write_file', arguments: {} }]); }
  catch (e) { threw2 = true; }
  check('写入类工具请求失败绝不重试（只调用 1 次）', threw2 && n2 === 1, 'n=' + n2);

  let n3 = 0;
  core.callTools = async function () { n3++; throw new Error('boom'); };
  let threw3 = false;
  try {
    await core.callToolsWithRetry([
      { name: 'read_file', arguments: {} },
      { name: 'run_in_terminal', arguments: {} },
    ]);
  } catch (e) { threw3 = true; }
  check('整批含非幂等工具时整批不重试', threw3 && n3 === 1, 'n=' + n3);

  // 29d. HTTP 200 携带业务错误（status:error / approval_required）→ 不重试。
  // 这是最容易写错的一条：只有请求层 reject 才该重试，业务结果必须原样交给模型。
  // （若这里重试，一次「审批未通过」会被放大成 N 次无效请求。）
  let n4 = 0;
  core.callTools = async function () {
    n4++;
    return { results: [{ tool: 'read_file', status: 'error', error: { message: 'boom' } }] };
  };
  let r4 = null;
  try { r4 = await core.callToolsWithRetry([{ name: 'read_file', arguments: {} }]); }
  catch (e) { /* 不应抛 */ }
  check('HTTP 200 里的业务错误不重试（只调用 1 次且结果原样返回）',
    n4 === 1 && r4 && r4.results && r4.results[0].status === 'error', 'n=' + n4);

  core.callTools = savedCall;
  core.config.tools = savedTools;
  core.config.callMaxRetries = savedMax;

  console.log('\n结果: ' + passed + ' 通过, ' + failed + ' 失败');
  process.exit(failed ? 1 : 0);
})();
