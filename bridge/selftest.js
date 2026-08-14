#!/usr/bin/env node
/**
 * 桥接链路自测工具（Node 运行，无需浏览器）：
 * 验证 core.js 的解析/检测/格式化纯函数在真实场景下的行为。
 *
 * 运行：node bridge/selftest.js
 * 覆盖：合法 JSON / SSE 转义 / Windows 路径非法转义 / 多工具 / 中文 /
 *       嵌套 JSON / 非工具文本不误判 / 流式中间态不崩溃
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

// 加载 core.js 提取 BridgeCore
const src = fs.readFileSync(path.join(__dirname, 'core.js'), 'utf-8');
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const core = sandbox.BridgeCore;

let passed = 0, failed = 0;
function check(name, cond, extra) {
  if (cond) { passed++; console.log('  PASS  ' + name); }
  else { failed++; console.log('  FAIL  ' + name + (extra ? '  → ' + extra : '')); }
}

console.log('桥接链路自测（core.js 纯函数）\n');

// 1. 合法 JSON
{
  const t = '我需要读取文件\nstart:{"name":"read_file","arguments":{"path":"G:/x.txt"}}end\n请稍等';
  const calls = core.parseToolCalls(t);
  check('合法 JSON 单工具', calls.length === 1 && calls[0].name === 'read_file', JSON.stringify(calls));
}
// 2. SSE 转义（\\"）
{
  const t = 'data: {"choices":[{"delta":{"content":"start:{\\"name\\":\\"write_file\\",\\"arguments\\":{\\"path\\":\\"a.txt\\"}}end"}}]}';
  const calls = core.parseToolCalls(t);
  check('SSE 报文转义', calls.length === 1 && calls[0].name === 'write_file', JSON.stringify(calls));
}
// 3. Windows 路径非法转义（\\D → 宽容解析）—— 核心场景
{
  const t = 'start:{"name":"mcp__windows-mcp__PowerShell","arguments":{"command":"Test-Path \'G:\\Download\'"}}end';
  const calls = core.parseToolCalls(t);
  check('Windows 路径非法转义 \\D', calls.length === 1 &&
    calls[0].arguments.command === "Test-Path 'G:\\Download'", JSON.stringify(calls));
}
// 4. 多工具
{
  const t = 'start:{"name":"a","arguments":{}}end\nstart:{"name":"b","arguments":{"x":1}}end';
  const calls = core.parseToolCalls(t);
  check('多工具', calls.length === 2 && calls[1].name === 'b', JSON.stringify(calls));
}
// 5. 中文内容
{
  const t = 'start:{"name":"write_file","arguments":{"content":"你好世界"}}end';
  const calls = core.parseToolCalls(t);
  check('中文内容', calls.length === 1 && calls[0].arguments.content === '你好世界', JSON.stringify(calls));
}
// 6. 嵌套 JSON（含数组/对象）
{
  const t = 'start:{"name":"batch","arguments":{"items":[{"id":1},{"id":2}],"meta":{"n":3}}}end';
  const calls = core.parseToolCalls(t);
  check('嵌套 JSON', calls.length === 1 && calls[0].arguments.items.length === 2 && calls[0].arguments.meta.n === 3, JSON.stringify(calls));
}
// 7. 非工具文本不误判
{
  const t = 'start 是一段正常文本 end，不是工具调用';
  check('非工具文本不误判', core.parseToolCalls(t).length === 0);
  check('hasToolCalls 非工具文本', core.hasToolCalls(t) === false);
}
// 8. 流式中间态（不完整 JSON）不崩溃
{
  const t = 'start:{"name":"mcp__windows-mcp__PowerShell","arguments":{"command":"Get-ChildItem -Pa';
  check('流式中间态不崩溃', Array.isArray(core.parseToolCalls(t)));
}
// 9. 真实场景：完整 PowerShell 大文件命令（含管道/特殊字符）
{
  const t = 'start:{"name":"mcp__windows-mcp__PowerShell","arguments":{"command":"Get-ChildItem -Path \'G:\\Download\' -File | Sort-Object Length -Descending | Select-Object -First 20"}}end';
  const calls = core.parseToolCalls(t);
  check('真实 PowerShell 命令', calls.length === 1 && calls[0].arguments.command.includes('Sort-Object'), JSON.stringify(calls && calls[0]));
}
// 10. formatResults 格式
{
  const out = core.formatResults([{ status: 'success', output: 'ok' }]);
  check('formatResults', out.includes('[工具1执行结果]') && out.includes('"output": "ok"'));
}
// 11. JSON 值内未转义引号（模型常见错误）
{
  const t = 'start:{"name":"mcp__windows-mcp__PowerShell","arguments":{"command":"Get-ChildItem -Path "G:/Download" -Recurse -File"}}end';
  const calls = core.parseToolCalls(t);
  check('值内未转义引号修复', calls.length === 1 &&
    calls[0].arguments.command === 'Get-ChildItem -Path "G:/Download" -Recurse -File', JSON.stringify(calls));
}
// 12. 嵌套未转义引号 + 路径反斜杠组合
{
  const t = 'start:{"name":"mcp__windows-mcp__PowerShell","arguments":{"command":"Get-ChildItem -Path "F:/" -Directory"}}end';
  const calls = core.parseToolCalls(t);
  check('值内引号+目录模式', calls.length === 1 && calls[0].arguments.command === 'Get-ChildItem -Path "F:/" -Directory', JSON.stringify(calls));
}
// 13. 正常转义不受影响（回归）
{
  const t = 'start:{"name":"write_file","arguments":{"path":"a.txt","content":"他说\"你好\""}}end';
  const calls = core.parseToolCalls(t);
  check('正常转义回归', calls.length === 1 && calls[0].arguments.content === '他说"你好"', JSON.stringify(calls));
}

console.log('\n结果: ' + passed + ' 通过, ' + failed + ' 失败');
process.exit(failed ? 1 : 0);
