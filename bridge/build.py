"""合并 bridge/core.js + adapters/*.js -> ds-bridge.user.js

新增站点适配器：
  1. 复制 adapters/_template.js 为 adapters/<站点>.js，填写站点差异
  2. 在下方 ADAPTERS 列表登记（文件名, 全局名）
  3. 在 HEADER 追加 @match
  4. 运行 python bridge/build.py
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# 站点适配器登记：(adapters 下的文件名, 文件导出的全局名)
# 顺序即匹配优先级——首个 match 命中当前 URL 的适配器生效。
ADAPTERS = [
    ("deepseek", "DeepSeekAdapter"),
    ("mimo", "MiMoAdapter"),
]

HEADER = """// ==UserScript==
// @name         DS Web Local Bridge
// @namespace    ds-web-local
// @version      3.4.0
// @description  网页版 DeepSeek / 小米 MiMo 桥接本地 Agent 能力（MCP/文件/技能），自动注入 role_card + 解析工具调用；面板自动留存最后一条命令，大结果提示上传附件
// @match        https://chat.deepseek.com/*
// @match        https://*.deepseek.com/*
// @match        https://ultraspeed.xiaomimimo.com/*
// @match        https://*.xiaomimimo.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @connect      localhost
// @connect      127.0.0.1
// @run-at       document-idle
// ==/UserScript==

"""

MAIN = r"""
(function () {
  'use strict';

  const core = window.BridgeCore;

  // 适配器注册表（由 build.py 注入）：取首个 match 命中当前 URL 的适配器；
  // 未命中说明站点未适配，静默退出。
  window.BridgeAdapters = [__ADAPTER_GLOBALS__];
  const adapter = window.BridgeAdapters.filter(function (a) {
    return a && a.match && a.match.test(location.href);
  })[0];

  if (!core || !adapter) return;

  let pendingCalls = [];     // 待执行调用累积（流式期间模型连续输出的多个调用合并成一批执行）
  let debounceTimer = null;  // 防抖 timer：流式静止 600ms 后批量执行，期间新调用自动合并
  let busy = false;          // 批次执行中（仅防止并发执行批次，绝不拦截新到达的调用）
  let lastProcessed = null;  // {key, ts} 幂等批去重（30s 窗口，仅整批全为幂等工具时生效）
  let turnResults = [];      // 当前轮累计的结果文本（多批合并成一次回填，避免逐批覆盖/残留）
  let turnAttachments = [];  // 当前轮结果落盘的附件路径（回填失败时指引用，保证结果不丢）
  let turnExternalized = []; // 当前轮「大结果」附件路径（非截断结果不入选）→ 提示用户上传附件以取全文
  let quiesceTimer = null;   // 批次全部完成后静默定时器 → 结束本轮、统一回填
  let turnSeq = 0;           // 轮次自增标识（fillInput 据此重置上一轮残留，保证上下文干净）
  let suppressUntil = 0;     // 会话切换冷却截止时间戳：SPA 切会话期间延后执行，
                             // 用于避开 DOM 未稳定的窗口（只延后，绝不丢弃调用）
  let rolecardTimer = null;  // 切会话后重注入 role_card 的定时器（防连续切换叠加注入）

  // 每次回填工具结果都附一行格式提醒（加固 4）：role_card 只在会话开头注入一次，
  // 长对话中模型会逐渐"忘记"输出规范，逐轮提醒是成本最低、见效最快的纠偏手段。
  const FORMAT_HINT = '\n\n【工具调用格式】继续调用工具请每行一条：@@tool 工具名 {"参数":"值"}' +
    '（行首 @@tool，参数单行 JSON，无终止符，勿用代码块包裹）';

  async function init() {
    core.loadConfig();
    adapter.loadConfig();
    adapter.setConfig({
      backendUrl: core.config.backendUrl,
      autoInject: core.config.autoInject,
      autoSend: core.config.autoSend,
    });
    adapter.ensureStatusButton();
    // 身份先行入追踪：后端日志/附件目录按 caller 隔离，排查时需用它对齐
    core.trace('identity', 'caller=' + core.ensureCallerId() + ' tab=' + core._tabId +
      ' backend=' + core.config.backendUrl);

    const ok = await core.health();
    if (!ok) {
      core.trace('health/fail', '后端不可达: ' + core.config.backendUrl);
      adapter.setStatus('⚠️ 本地后端未启动: ' + core.config.backendUrl + '（请先运行 backend/main.py）');
      return;
    }

    try {
      await core.loadRolecard();
      await core.loadTools();
    } catch (e) {
      core.trace('rolecard/fail', e && e.message || String(e));
      adapter.setStatus('⚠️ 拉取 role_card 失败: ' + e.message);
      return;
    }

    core.trace('ready', 'tools=' + core.config.tools.length + ' autoInject=' + core.config.autoInject +
      ' autoSend=' + core.config.autoSend);
    adapter.setStatus('✅ 已连接本地后端，' + core.config.tools.length + ' 个工具可用');

    if (core.config.autoInject) {
      const okInject = await adapter.injectRolecard(core.config.rolecard);
      core.trace('inject', okInject ? 'role_card 已注入并发送' : '注入失败（未找到输入框/发送按钮）');
      if (okInject) adapter.setStatus('📝 已注入 role_card，可直接开始对话');
      else adapter.setStatus('❌ role_card 注入失败（未找到输入框/发送按钮），请刷新页面重试');
    }

    let domTicks = 0;   // DOM 通道高频触发，追踪按采样记录（命中工具调用时无条件记录）
    adapter.onModelReply(function (text) {
      // fetch/XHR 通道：完整回复 → 保存到本地文件 + 工具调用检测（解析失败可提示）
      core.trace('reply/fetch', 'len=' + (text || '').length +
        ' hasToolCalls=' + core.hasToolCalls(text));
      core.saveReply(text, 'assistant');
      checkCalls(text, true);
    }, function (domText) {
      // DOM 通道：仅工具调用检测（流式中间态，不保存、不提示）
      domTicks += 1;
      const hit = core.hasToolCalls(domText);
      if (hit || domTicks % 20 === 1) {
        core.trace('reply/dom', 'len=' + (domText || '').length + ' hasToolCalls=' + hit +
          (hit ? '' : '（采样第 ' + domTicks + ' 次）'));
      }
      checkCalls(domText, false);
    });

    // ---- 会话切换保护 ----
    // 站点多为 SPA：切会话不触发脚本重载，而旧会话的历史 @@tool 文本可能仍残留在 DOM 中，
    // 被 DOM 通道重新检测到 → 当作新调用重复执行（写入/终端类重复执行即是事故）。
    // 冷却期内丢弃一切检测结果，并清空本轮累积状态，避免跨会话串扰。
    let lastHref = location.href;
    function onSessionChange() {
      // URL 未变则不是会话切换：站点初始化时的 pushState/replaceState 不应触发冷却，
      // 否则首次进入就被冷却，第一次工具调用会被无谓吞掉。
      if (location.href === lastHref) return;
      lastHref = location.href;
      const cooldown = core.config.sessionSwitchCooldownMs || 5000;
      suppressUntil = Date.now() + cooldown;
      // pendingCalls 刻意不清空：冷却只是「延后执行」，清空就等于丢弃用户已发出的调用
      // 旧会话未回填的结果文本丢弃（后端已落盘附件，数据不丢），避免污染新会话上下文
      turnResults = [];
      turnAttachments = [];
      turnExternalized = [];
      // 批去重记录必须重置：否则新会话里首次发出的、与旧会话恰好相同的只读调用
      // 会被 30s 去重窗口吞掉，表现为「一切换会话，工具就莫名其妙不执行了」
      lastProcessed = null;
      if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = null; }
      if (quiesceTimer) { clearTimeout(quiesceTimer); quiesceTimer = null; }
      // 冷却期必须保证队列仍留有「触发者」：若只清定时器而不重排，后续重复检测会因去重
      // 命中（added===0）直接 return，pendingCalls 就再也没人执行，调用永久卡死。
      if (pendingCalls.length && !busy) {
        debounceTimer = setTimeout(flushPending, Math.max(600, cooldown));
      }
      core.trace('session/switch', '已切换会话，冷却 ' + cooldown + 'ms → ' + location.href);
    }

    // hash 路由 SPA（如 MiMo 的 #/）：切换会话不会触发脚本重载，
    // 需在新会话就绪后重新注入 role_card（injectRolecard 内含去重检查，天然幂等）
    window.addEventListener('hashchange', function () {
      onSessionChange();
      if (!core.config.autoInject) return;
      // 连续切会话会叠加多次注入（2s 延迟内再切一次就有两次），必须先取消上一次未触发的
      if (rolecardTimer) clearTimeout(rolecardTimer);
      rolecardTimer = setTimeout(function () {
        rolecardTimer = null;
        adapter.injectRolecard(core.config.rolecard);
      }, 2000);
    });
    window.addEventListener('popstate', onSessionChange);
    // path 路由 SPA（如 DeepSeek）切会话走 history.pushState —— 它不触发 popstate，
    // 因此必须劫持才能捕获；replaceState 同理。仅包装转发，不改变站点行为。
    // 劫持属于增强能力，绝不能成为 init 的单点故障：失败只降级为「捕获不到 pushState 切换」
    // （hashchange/popstate 仍生效），绝不让整个桥接初始化崩掉。
    try {
      const _pushState = history.pushState;
      const _replaceState = history.replaceState;
      history.pushState = function () {
        const r = _pushState.apply(this, arguments);
        onSessionChange();
        return r;
      };
      history.replaceState = function () {
        const r = _replaceState.apply(this, arguments);
        onSessionChange();
        return r;
      };
    } catch (e) {
      core.trace('session/hook-fail', 'history 劫持失败，已降级: ' + ((e && e.message) || e));
    }

    function checkCalls(text, notify) {
      // 会话切换冷却中：照常解析入队，执行由 flushPending 延后到冷却结束。
      // 绝不在此处丢弃 —— href 变化并不等同于切会话（URL 规范化、新建会话回写 id 都会改
      // href），一旦误冷却又丢弃，用户这次调用就永久消失了（DOM 通道有指纹去重，之后不会
      // 再被重新检测）。「延迟执行」的代价远小于「丢失调用」。
      if (!core.hasToolCalls(text)) {
        // 未命中：多数是正常聊天文本，也可能是模型格式偏差。
        // 后者表现为"模型明明写了调用却毫无反应"，必须自证，否则排查只能靠猜。
        // 只在完整回复通道诊断（DOM 流式中间态会频繁误报）。
        if (notify) diagnoseNearMiss(text);
        return;
      }
      const calls = core.parseToolCalls(text);
      if (!calls.length) {
        // 命中 @@tool 行但拿不到可执行的调用（JSON 解析失败 / 工具名不在清单）
        if (notify) {
          core.trace('parse/none', '命中 @@tool 行但没有可执行的调用');
          // 留存现场：解析失败也要把这行原样存进面板，用户才能在手动执行框里改参数重试
          const raw = core.extractCommandLines(text) || String(text).slice(0, 4000);
          core.saveLastCommand(raw, { source: 'auto', invalid: true, reason: '调用行解析失败或工具名不在清单' });
          autoRepair('调用行解析失败，或工具名不在工具清单内', text);
        }
        return;
      }
      core.trace('parse/ok', 'matched=' + calls.length + ' [' +
        calls.map(function (c) { return c.name; }).join(', ') + ']');
      // 命令记忆：一解析出调用即落存（此时尚未执行，之后无论成败都保留这条，便于手动兜底）
      core.saveLastCommand(core.callsToText(calls), { source: 'auto', count: calls.length });
      queueCalls(calls);
    }

    // 近似命中诊断：文本里疑似有工具调用却未被识别 → 记录片段并触发自动纠错。
    // 常见偏差：@@tool 未独占行首、沿用已弃用的 start:{...}end、把调用写进 ```json 代码块。
    function diagnoseNearMiss(text) {
      if (!text) return;
      const s = String(text);
      let idx = -1, reason = '';
      if (/@@tool/i.test(s)) {
        idx = s.search(/@@tool/i);
        reason = '@@tool 未独占行首（行首不能有其他文字）';
      } else if (/start\s*:\s*\{/.test(s)) {
        idx = s.search(/start\s*:\s*\{/);
        reason = '使用了已弃用的旧协议 start:{...}end';
      } else if (/```(?:json)?[ \t]*\r?\n\s*\{\s*"name"\s*:/i.test(s)) {
        idx = s.search(/```(?:json)?/i);
        reason = '代码块里的 JSON 缺少 @@tool 行首标记';
      }
      if (idx < 0) return;
      const snippet = s.slice(Math.max(0, idx - 40), idx + 240).replace(/\s+/g, ' ');
      core.trace('MISMATCH/未触发', reason + ' → 片段: ' + snippet);
      // 留存现场：格式偏差时把原文里的 @@tool 行（或片段）存入面板手动执行框，供人工修正重发
      core.saveLastCommand(core.extractCommandLines(s) || s.slice(idx, idx + 4000),
        { source: 'auto', invalid: true, reason: reason });
      autoRepair(reason, snippet);
    }

    // 自动纠错（加固 3）：格式不符时不再只是提示，而是把「正确格式 + 重试指令」直接填回
    // 输入框；autoSend 开启时一并发送，让链路自动收敛，无需人工介入。
    function autoRepair(reason, snippet) {
      if (!core.config.autoRepair) {
        adapter.setCallState('warning');   // 圆点：橙色（格式待修正）
        adapter.setStatus('⚠️ 工具调用未识别（' + reason + '），详见面板「调用追踪」');
        return;
      }
      const hint = '⚠️ 上一轮的工具调用未被识别：' + reason + '。\n' +
        '请严格按下面格式重新输出（每条一行，行首 @@tool，参数单行 JSON，' +
        '不要代码块包裹，不要任何终止符）：\n\n' +
        '@@tool read_file {"path":"C:/完整/路径/文件.txt"}\n\n' +
        '现在，请只输出你要调用的工具（可多行，不要其他内容）。';
      core.trace('autoRepair', '已生成纠错提示: ' + reason +
        (snippet ? ' | 片段: ' + String(snippet).slice(0, 160) : ''));
      adapter.setCallState('warning');   // 圆点：橙色（已生成纠错提示，待发送）
      adapter.fillInput(hint, 'repair');
      if (core.config.autoSend) {
        adapter.autoSend().then(function (sent) {
          core.trace('autoRepair/send', sent ? '已自动发送' : '发送失败');
        });
      } else {
        adapter.setStatus('⚠️ 调用未识别，已生成纠错提示，请按发送键让模型重发');
      }
    }

    // 累积调用 + 防抖合并（修复"多调用只有第一个回填"的 bug）：
    // 旧实现用 processing 全局锁挡重复，但流式输出时 DOM 通道先检测到第 1 个调用并置锁，
    // 后续调用（含 fetch/XHR 完整文本通道）全部被 processing 拦截丢弃 → 只回填第一个。
    // 新实现：调用进入 pendingCalls 累积（按调用粒度去重，DOM 中间态与 fetch 完整文本
    // 重复检测同一调用不会重复入队），流式静止 600ms 后整批执行；执行期间新到达的
    // 调用继续累积，当前批次结束自动续跑，既不丢调用也不重复执行。
    function queueCalls(calls) {
      let added = 0;
      for (const c of calls) {
        const key = JSON.stringify([c.name, c.arguments]);
        if (pendingCalls.some(function (p) { return JSON.stringify([p.name, p.arguments]) === key; })) continue;
        pendingCalls.push(c);
        added += 1;
      }
      if (!added) {
        // 去重命中：若队列非空却没有任何待触发的定时器（例如刚被会话切换清掉），
        // 必须兜底重排 —— 否则这批调用会永远卡在队列里，用户只看到「没反应」
        if (pendingCalls.length && !busy && !debounceTimer) {
          debounceTimer = setTimeout(flushPending, 600);
          core.trace('queue/revive', '队列无触发者，已兜底重排 pending=' + pendingCalls.length);
        }
        return;
      }
      core.trace('queue', 'new=' + added + ' dup=' + (calls.length - added) +
        ' pending=' + pendingCalls.length + ' → 600ms 后批量执行');
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(flushPending, 600);
    }

    async function flushPending() {
      if (busy || !pendingCalls.length) return;
      // 会话切换冷却中：既不执行也不丢弃，延后到冷却结束后再来。
      // 冷却的用意是避开切会话瞬间 DOM 尚未稳定的窗口，而不是取消用户的调用。
      if (Date.now() < suppressUntil) {
        if (debounceTimer) clearTimeout(debounceTimer);
        const wait = Math.max(200, suppressUntil - Date.now());
        core.trace('session/defer', '冷却中，延后 ' + wait + 'ms 执行 ' +
          pendingCalls.length + ' 个调用');
        debounceTimer = setTimeout(flushPending, wait);
        return;
      }
      busy = true;
      try {
        const calls = pendingCalls;
        pendingCalls = [];
        // 幂等批 30s 去重：仅当整批全部为幂等（只读）工具时生效——避免把“修正参数后的
        // 写入/终端重试”当重复吞掉；同流内字面重复仍由 queueCalls 的按调用去重兜底。
        const allIdempotent = calls.length > 0 &&
          calls.every(function (c) { return core.isIdempotentTool(c.name); });
        if (allIdempotent) {
          const key = JSON.stringify(calls.map(function (c) { return [c.name, c.arguments]; }));
          const now = Date.now();
          if (lastProcessed && lastProcessed.key === key && now - lastProcessed.ts < 30000) {
            core.trace('dedupe', '30s 内相同的只读调用，已去重跳过 [' +
              calls.map(function (c) { return c.name; }).join(', ') + ']');
            adapter.setStatus('🔁 检测到 30s 内已执行过的相同只读调用，已去重跳过');
            return;
          }
          lastProcessed = { key: key, ts: now };
        }
        core.trace('batch/start', 'n=' + calls.length + ' [' +
          calls.map(function (c) { return c.name; }).join(', ') + ']');
        await handleToolCalls(calls);
      } finally {
        busy = false;
        // 批次执行期间新累积的调用继续处理（不丢失）；全部完成 → 静默后统一回填结束本轮
        if (pendingCalls.length) setTimeout(flushPending, 300);
        else scheduleFinalize();
      }
    }

    function scheduleFinalize() {
      if (quiesceTimer) clearTimeout(quiesceTimer);
      // 原硬编码 600ms 偏短：模型流式输出的间隔可能更长，会把一批调用拆成多次回填
      quiesceTimer = setTimeout(finalizeTurn, core.config.batchQuiesceMs || 1500);
    }

    // 本轮结束：把累计的多批结果合并成一条文本回填（fillInput 用新轮次 token 先清残留再写入，
    // 保证上下文干净），autoSend 开启时自动发送，否则提示用户按发送键。
    async function finalizeTurn() {
      if (quiesceTimer) { clearTimeout(quiesceTimer); quiesceTimer = null; }
      if (busy || pendingCalls.length || !turnResults.length) return;
      const text = turnResults.join('\n\n') + FORMAT_HINT;
      turnResults = [];
      const files = turnAttachments.slice();
      turnAttachments = [];
      const externals = turnExternalized.slice();
      turnExternalized = [];
      // 大结果提示（用户侧）：完整内容已在本地附件里，网页输入框点「附件/📎」上传即可取全文，
      // 既避免超长回填被站点截断，也省掉「模型 read_file 全文」的一轮往返。
      const attachNote = externals.length
        ? '📎 本轮有 ' + externals.length + ' 个大结果已存为附件；如需完整内容，' +
          '请在输入框点「附件/📎」上传该文件：\n' + externals.slice(-3).join('\n')
        : '';
      turnSeq += 1;
      try {
        core.trace('fill/start', 'chars=' + text.length + ' turn=' + turnSeq +
          (externals.length ? ' externalized=' + externals.length : ''));
        const filled = await adapter.fillInput(text, turnSeq);
        core.trace('fill/done', filled ? '已写入输入框' : '失败：未找到输入框');
        if (!filled) {
          // 回填失败（未找到输入框 / 页面结构变更）：结果不丢——后端已落盘附件，直接给路径
          adapter.setCallState('error');   // 圆点：红色
          core.trace('fill/fallback', '结果回落附件，本轮 ' + files.length + ' 个附件');
          adapter.setStatus('❌ 回填失败（未找到输入框）。结果已存入附件:\n' +
            (files.length ? files.slice(-3).join('\n')
              : '见后端 data/attachments/<caller>/<日期>/calls_<日期>.jsonl'));
        } else if (core.config.autoSend) {
          // 回填后给站点留出解析输入框内容的时间，再触发发送（避免内容未就绪就提交）
          const delay = core.config.autoSendDelayMs || 1000;
          if (delay > 0) await new Promise(function (r) { setTimeout(r, delay); });
          const sent = await adapter.autoSend();
          core.trace('autosend', sent ? '已提交' : '提交失败');
          if (sent) {
            adapter.setCallState('connected');   // 圆点：绿色
            if (attachNote) adapter.setStatus(attachNote);   // 有附件提示时不静默关闭
            else adapter.closeStatus();                      // 成功：自动关闭提示
          } else {
            adapter.setCallState('error');
            adapter.setStatus('⚠️ 自动发送失败，请手动按发送键提交');
          }
        } else {
          // 结果已填入输入框：圆点转绿，提示用户按发送键（有大结果附件时追加附件上传提示）
          adapter.setCallState('connected');
          adapter.setStatus('📤 工具结果已就绪，请按发送键提交' + (attachNote ? '\n' + attachNote : ''));
        }
      } catch (e) {
        core.trace('fill/error', (e && e.message) ? e.message : String(e));
        adapter.setStatus('❌ 工具结果回填异常: ' + (e && e.message ? e.message : e));
      }
    }
  }

  // 执行一批调用并把结果文本累积进 turnResults（不在批内逐批回填，统一在 turn 结束时合并）
  async function handleToolCalls(calls) {
    // 只读结果缓存（优化调用次数）：幂等工具同参数 20s 内直接复用上次结果，
    // 省掉一次后端往返与一轮模型等待。TTL 短于后端 30s 幂等去重，后端去重仍是第二道防线。
    const cached = [];
    const toRun = [];
    for (const c of calls) {
      if (core.isIdempotentTool(c.name)) {
        const hit = core.getCachedResult(c.name, c.arguments);
        if (hit) { cached.push(Object.assign({}, hit, { _cached: true })); continue; }
      }
      toRun.push(c);
    }
    if (cached.length) {
      core.trace('cache/hit', cached.length + ' 个只读调用命中 20s 缓存，未打后端 [' +
        cached.map(function (r) { return r.tool; }).join(', ') + ']');
    }
    adapter.setCallState('busy');   // 圆点：蓝色呼吸
    adapter.setStatus('🔧 正在执行 ' + (toRun.length + cached.length) + ' 个工具调用...');

    if (!toRun.length) {
      // 全部命中缓存：无需请求，直接渲染结果
      turnResults.push(core.formatResults(cached));
      collectFiles(cached);
      collectExternalized(cached);
      core.markLastCommandResult(true, '');   // 缓存命中同样视为执行成功
      return;
    }

    try {
      core.trace('call/send', 'POST /api/bridge/call n=' + toRun.length);
      const resp = await core.callToolsWithRetry(toRun);
      const results = (resp && resp.results) || [];
      // 逐调用信封摘要：tool#request_id:status，可与后端 /api/bridge/tool_logs、附件目录直接对齐
      core.trace('call/recv', results.map(function (r) {
        return (r && (r.tool + '#' + r.request_id + ':' + r.status)) || 'null';
      }).join(' | ') || '(空结果)');
      // 只缓存成功结果——错误必须真实重试，不能被缓存掩盖
      for (const r of results) {
        if (r && r.status === 'ok' && core.isIdempotentTool(r.tool)) {
          core.cacheResult(r.tool, r.arguments, r);
        }
      }
      const list = cached.concat(results);
      // 大结果自动内联是高级开关（默认关：回填预览 + 附件路径，提示用户上传附件取全文）
      const rendered = core.config.autoEnrichExternalized
        ? await enrichExternalized(list) : list;
      turnResults.push(core.formatResults(rendered));
      collectFiles(results);
      collectExternalized(rendered);   // 用渲染后的列表判定，已内联(_enriched)的不再提示上传
      const failed = results.some(function (r) {
        return r && (r.status === 'error' || r.approval_required);
      });
      // 命令记忆回填执行结果：失败时面板提示会显示「执行失败」，用户一眼知道该接手哪一条
      core.markLastCommandResult(!failed, failed ? '存在失败/待审批的调用' : '');
      if (results.some(function (r) { return r && r.approval_required; })) {
        adapter.setCallState('warning');   // 圆点：橙色（待人工处理）
        adapter.setStatus('⏸ 部分调用需人工审批：请在审批控制台批准后，重发原提示让模型重试' +
          '\n原命令已存入面板「手动执行」，可修改后直接重试');
      }
    } catch (e) {
      // 兜底：即使整批失败也产出一条结果文本回填，满足“任何输出（含错误）都必须返回”
      adapter.setCallState('error');   // 圆点：红色
      core.trace('call/error', (e && e.message) ? e.message : String(e));
      core.markLastCommandResult(false, (e && e.message) ? e.message : String(e));
      turnResults.push('❌ 工具执行失败: ' + (e && e.message ? e.message : e) +
        '。请检查后端与调用参数后重试该调用。');
      // 自动执行失败 → 明确告诉用户去面板手动重试（命令已自动填入，改完点「执行」即可）
      adapter.setStatus('❌ 工具执行失败：' + (e && e.message ? e.message : e) +
        '\n原命令已自动存入面板「手动执行」框，可修改后点「▶ 执行」重试');
    }
  }

  // 收集本轮结果落盘的附件路径：回填失败时据此指引用户，保证结果不丢
  function collectFiles(results) {
    for (const r of results) {
      if (r && r.file) turnAttachments.push(r.file);
    }
  }

  // 收集本轮「大结果」附件（仅 truncated 且未内联）：据此提示用户在网页端上传附件取全文。
  // 小结果是完整回填，无需打扰；已内联（_enriched）的完整内容已在上下文里，也不提示。
  function collectExternalized(results) {
    for (const r of results) {
      if (r && r.truncated && r.file && !r._enriched && turnExternalized.indexOf(r.file) < 0) {
        turnExternalized.push(r.file);
      }
    }
  }

  // 外置结果补全（高级开关 autoEnrichExternalized）：
  // 按 caller+request_id 拉取附件文件全文替换预览。失败时回退原预览（不影响主流程）。
  async function enrichExternalized(results) {
    const out = [];
    for (const r of results) {
      if (r && r.externalized && r.file && r.request_id) {
        try {
          const q = 'caller_id=' + encodeURIComponent(r.caller_id || 'anon') +
            '&request_id=' + encodeURIComponent(r.request_id);
          const res = await core.request(core.config.backendUrl + '/api/bridge/attachments?' + q);
          const content = res && res.data && res.data.content;
          if (content) {
            // _enriched 标记：已内联全文，_formatOneResult 据此不再提示「上传附件」
            out.push(Object.assign({}, r, { summary: content, _enriched: true }));
            continue;
          }
        } catch (e) {
          console.warn('[bridge] 外置结果补全失败，回退预览: ' + (e && e.message ? e.message : e));
        }
      }
      out.push(r);
    }
    return out;
  }

  init();
})();
"""


def verify_syntax(out_path: str) -> bool:
    """构建产物语法自检：调用 node --check；未安装 node 时跳过并返回 True。

    防御动机：MAIN 等 JS 模板写在 Python 字符串里，一旦出现转义泄漏（如 '\\n'
    被 Python 变成真实换行），产物会出现跨行字符串字面量 → 整份脚本 SyntaxError
    → 安装后一行都不执行（无状态圆点、无注入、无调用，表现为"静默失效"）。
    构建期自检可即时拦截，不依赖 CI（提交未推送时 CI 根本不会跑）。
    """
    import shutil
    import subprocess

    node = shutil.which('node')
    if not node:
        print('[警告] 未检测到 node，跳过语法自检（建议安装 Node.js 以获得构建期校验）')
        return True
    proc = subprocess.run([node, '--check', out_path],
                          capture_output=True, text=True,
                          encoding='utf-8', errors='replace')
    if proc.returncode == 0:
        print('语法自检: 通过（node --check）')
        return True
    print('[错误] 产物未通过语法自检，请勿安装该脚本:')
    print((proc.stderr or proc.stdout or '').strip())
    return False


def build():
    with open(os.path.join(HERE, 'core.js'), 'r', encoding='utf-8') as f:
        core_js = f.read()

    parts = [core_js]
    for filename, _global in ADAPTERS:
        with open(os.path.join(HERE, 'adapters', filename + '.js'), 'r', encoding='utf-8') as f:
            parts.append(f.read())

    # 适配器全局名注入到注册表（core.js 必须在前，BridgeUI.install 依赖它）
    globals_js = ', '.join('window.' + g for _, g in ADAPTERS)
    main_js = MAIN.replace('__ADAPTER_GLOBALS__', globals_js)
    parts.append(main_js)

    out = HEADER + '\n'.join(parts)
    out_path = os.path.join(HERE, 'ds-bridge.user.js')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(out)
    print('生成', out_path, '共', len(out), '字节')
    print('适配器:', ', '.join(name for name, _ in ADAPTERS))
    # 坏产物 = 整份脚本静默失效，必须在构建期拦下（非零退出，便于 CI/本地感知）
    if not verify_syntax(out_path):
        raise SystemExit(1)


if __name__ == '__main__':
    build()
