"""合并 bridge/core.js + adapters/*.js -> ds-bridge.user.js"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

HEADER = """// ==UserScript==
// @name         DS Web Local Bridge
// @namespace    ds-web-local
// @version      3.2.0
// @description  网页版 DeepSeek 桥接本地 Agent 能力（MCP/文件/技能），自动注入 role_card + 解析工具调用
// @match        https://chat.deepseek.com/*
// @match        https://*.deepseek.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @connect      localhost
// @connect      127.0.0.1
// @run-at       document-idle
// ==/UserScript==

"""

MAIN = """
(function () {
  'use strict';

  const core = window.BridgeCore;
  const adapter = window.DeepSeekAdapter;

  if (!core || !adapter) return;
  if (!adapter.match.test(location.href)) return;

  let pendingCalls = [];     // 待执行调用累积（流式期间模型连续输出的多个调用合并成一批执行）
  let debounceTimer = null;  // 防抖 timer：流式静止 600ms 后批量执行，期间新调用自动合并
  let busy = false;          // 批次执行中（仅防止并发执行批次，绝不拦截新到达的调用）
  let lastProcessed = null;  // {key, ts} 调用去重（30s 窗口）

  async function init() {
    core.loadConfig();
    adapter.loadConfig();
    adapter.setConfig({
      backendUrl: core.config.backendUrl,
      autoInject: core.config.autoInject,
      autoSend: core.config.autoSend,
    });
    adapter.ensureStatusButton();

    const ok = await core.health();
    if (!ok) {
      adapter.setStatus('⚠️ 本地后端未启动: ' + core.config.backendUrl + '（请先运行 backend/main.py）');
      return;
    }

    try {
      await core.loadRolecard();
      await core.loadTools();
    } catch (e) {
      adapter.setStatus('⚠️ 拉取 role_card 失败: ' + e.message);
      return;
    }

    adapter.setStatus('✅ 已连接本地后端，' + core.config.tools.length + ' 个工具可用');

    if (core.config.autoInject) {
      const okInject = await adapter.injectRolecard(core.config.rolecard);
      if (okInject) adapter.setStatus('📝 已注入 role_card，可直接开始对话');
      else adapter.setStatus('❌ role_card 注入失败（未找到输入框/发送按钮），请刷新页面重试');
    }

    adapter.onModelReply(function (text) {
      // fetch/XHR 通道：完整回复 → 保存到本地文件 + 工具调用检测（解析失败可提示）
      core.saveReply(text, 'assistant');
      checkCalls(text, true);
    }, function (domText) {
      // DOM 通道：仅工具调用检测（流式中间态，不保存、不提示）
      checkCalls(domText, false);
    });

    function checkCalls(text, notify) {
      if (!core.hasToolCalls(text)) return;
      const calls = core.parseToolCalls(text);
      if (!calls.length) {
        // 完整文本解析失败 → 提示（模型格式错误，避免"无反应"困惑）；流式中间态不提示
        if (notify) adapter.setStatus('⚠️ 模型输出的工具调用格式有误（JSON 引号未转义），请让它重试');
        return;
      }
      queueCalls(calls);
    }

    // 累积调用 + 防抖合并（修复"多调用只有第一个回填"的 bug）：
    // 旧实现用 processing 全局锁挡重复，但流式输出时 DOM 通道先检测到第 1 个调用并置锁，
    // 后续调用（含 fetch/XHR 完整文本通道）全部被 processing 拦截丢弃 → 只回填第一个。
    // 新实现：调用进入 pendingCalls 累积（按调用粒度去重，DOM 中间态与 fetch 完整文本
    // 重复检测同一调用不会重复入队），流式静止 600ms 后整批执行；执行期间新到达的
    // 调用继续累积，当前批次结束自动续跑，既不丢调用也不重复执行。
    function queueCalls(calls) {
      let added = false;
      for (const c of calls) {
        const key = JSON.stringify([c.name, c.arguments]);
        if (pendingCalls.some(function (p) { return JSON.stringify([p.name, p.arguments]) === key; })) continue;
        pendingCalls.push(c);
        added = true;
      }
      if (!added) return;
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(flushPending, 600);
    }

    async function flushPending() {
      if (busy || !pendingCalls.length) return;
      busy = true;
      try {
        const calls = pendingCalls;
        pendingCalls = [];
        const key = JSON.stringify(calls.map(function (c) { return [c.name, c.arguments]; }));
        // 去重防重复执行：仅 30 秒内的相同整批调用算重复（跨会话/用户重试的同内容调用不受影响）
        const now = Date.now();
        if (lastProcessed && lastProcessed.key === key && now - lastProcessed.ts < 30000) return;
        lastProcessed = { key: key, ts: now };
        await handleToolCalls(calls);
      } finally {
        busy = false;
        // 批次执行期间新累积的调用继续处理（不丢失）
        if (pendingCalls.length) setTimeout(flushPending, 300);
      }
    }
  }

  async function handleToolCalls(calls) {
    adapter.setStatus('🔧 正在执行 ' + calls.length + ' 个工具调用...');
    try {
      const resp = await core.callTools(calls);
      const results = (resp && resp.results) || [];
      // 外置结果自动补全（伪附件）：externalized 结果拉取完整内容内联回填，
      // 模型直接看到完整内容（等效附件），无需再调 read_file 读外置文件
      const enriched = await enrichExternalized(results);
      const formatted = core.formatResults(enriched);
      // 填入输入框（占位符），autoSend 开启时自动点击发送
      const filled = await adapter.fillInput(formatted);
      if (!filled) {
        adapter.setStatus('❌ 工具结果回填失败: 未找到输入框');
      } else if (core.config.autoSend) {
        const sent = await adapter.autoSend();
        if (sent) adapter.closeStatus();          // 成功：自动关闭提示
        else adapter.setStatus('⚠️ 自动发送失败，请手动按发送键提交');
      } else {
        // 结果已填入输入框：显示短暂指引（3.5s 自动关闭），提醒用户按发送键
        adapter.setStatus('📤 工具结果已就绪，请按发送键提交');
      }
    } catch (e) {
      adapter.setStatus('❌ 工具执行失败: ' + (e && e.message ? e.message : e));
    }
  }

  // 外置结果补全：{externalized:true, file:"...result_xxx.json"} → 拉完整内容替换 summary。
  // 失败时回退原摘要（不影响调用主流程）。
  async function enrichExternalized(results) {
    const out = [];
    for (const r of results) {
      if (r && r.externalized && r.file) {
        try {
          const name = String(r.file).split(/[\\/]/).pop();
          const res = await core.request(core.config.backendUrl + '/api/bridge/files/' + encodeURIComponent(name));
          const content = res && res.data && res.data.content;
          if (content) {
            out.push(Object.assign({}, r, { summary: content }));
            continue;
          }
        } catch (e) {
          console.warn('[bridge] 外置结果补全失败，回退摘要: ' + (e && e.message ? e.message : e));
        }
      }
      out.push(r);
    }
    return out;
  }

  init();
})();
"""


def build():
    with open(os.path.join(HERE, 'core.js'), 'r', encoding='utf-8') as f:
        core_js = f.read()
    with open(os.path.join(HERE, 'adapters', 'deepseek.js'), 'r', encoding='utf-8') as f:
        deepseek_js = f.read()
    out = HEADER + core_js + '\n' + deepseek_js + '\n' + MAIN
    out_path = os.path.join(HERE, 'ds-bridge.user.js')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(out)
    print('生成', out_path, '共', len(out), '字节')


if __name__ == '__main__':
    build()
