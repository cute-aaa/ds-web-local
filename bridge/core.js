/**
 * DS Web Local — 桥接引擎（站点无关）
 * 职责：拉取 role_card / 工具清单、解析工具调用指令、调用后端、回填结果、状态提示。
 * 不绑定任何具体 web 站点，站点差异由 adapter 实现。
 */
(function (global) {
  'use strict';

  const BridgeCore = {
    config: {
      backendUrl: 'http://localhost:8088',
      autoInject: true,
      autoSend: false,   // 工具结果就绪后自动点击发送（默认关——可能触发 DeepSeek 风控，可手动开）
      autoEnrichExternalized: false,  // 大结果是否自动拉取附件全量内联（默认关：只回填预览+文件路径，模型按需 read_file）
      autoRepair: true,               // 模型输出格式不符时，自动生成纠错提示（autoSend 开则自动发送）
      rolecard: '',
      tools: [],

      // ---- 稳定性调优（会话切换冷却 / 批量聚合 / 幂等重试）----
      // 切换会话后抑制解析执行的时长：SPA 切会话时旧会话的历史 @@tool 可能仍在 DOM 中，
      // 不抑制会被当作新调用重复执行（写入类工具重复执行即为事故）。
      sessionSwitchCooldownMs: 5000,
      // 一批调用全部完成后，再静默等待一段时间确认没有新调用，才合并回填。
      // 原硬编码 600ms 偏短：模型流式输出的间隔可能更长，导致一批被拆成多次回填。
      batchQuiesceMs: 1500,
      // 回填完成到自动发送之间的间隔，给站点解析输入框内容留出时间。
      autoSendDelayMs: 1000,
      // 幂等（只读）工具调用失败后的最大重试次数；非幂等工具恒为 0，绝不自动重试。
      callMaxRetries: 2,
      // 含重试在内的总超时上限。
      callTotalTimeoutMs: 30000,
    },

    // ---- 调用方身份（多 profile 场景，docs/tool-call-reliability-spec.md §2.1）----
    // caller_id 按浏览器 profile 持久化（GM 存储，profile 内恒定）；tab_id 每页加载随机。
    // 后端按 caller 隔离 ask_user/附件/工具日志归属。
    _callerId: '',
    _tabId: '',
    _randHex(len) {
      let s = '';
      for (let i = 0; i < len; i++) s += '0123456789abcdef'.charAt(Math.floor(Math.random() * 16));
      return s;
    },
    ensureCallerId() {
      if (this._callerId) return this._callerId;
      try {
        if (typeof GM_getValue === 'function') this._callerId = GM_getValue('callerId', '');
      } catch (e) {}
      if (!this._callerId) {
        this._callerId = 'c_' + this._randHex(12);
        try { if (typeof GM_setValue === 'function') GM_setValue('callerId', this._callerId); } catch (e) {}
      }
      if (!this._tabId) this._tabId = this._randHex(6);
      return this._callerId;
    },
    // 一次调用请求的元信息（caller/tab 恒定，batch 每次新生成）
    _meta() {
      return { caller_id: this.ensureCallerId(), tab_id: this._tabId, batch_id: 'b_' + this._randHex(12) };
    },
    // 工具是否幂等（只读/可安全去重）：以 registry 元数据为准（loadTools 拉取），缺省 false
    isIdempotentTool(name) {
      const t = (this.config.tools || []).find(function (x) { return x.name === name; });
      return !!(t && t.idempotent);
    },

    // ---- 调用追踪（环形缓冲，面板「调用追踪」区可见）----
    // 用途：工具调用链路横跨「网页输出 → 解析 → 后端 → 回填」，任一环静默失败都表现为
    // 「没反应」。把各环节流水记下来，排查时不用开 DevTools 也能看出断在哪一步。
    _trace: [],
    _traceMax: 120,   // 上限，防止长会话占内存（面板只渲染末尾 40 条）
    _traceSeq: 0,
    trace(stage, detail) {
      try {
        const d = new Date();
        const pad = function (n) { return String(n).length < 2 ? '0' + n : String(n); };
        this._trace.push({
          seq: ++this._traceSeq,
          t: pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()),
          stage: String(stage || ''),
          detail: detail == null ? '' : String(detail),
        });
        if (this._trace.length > this._traceMax) this._trace.shift();
      } catch (e) { /* 追踪失败绝不影响主流程 */ }
    },
    getTrace() { return this._trace.slice(); },
    clearTrace() { this._trace = []; },

    // ---- 只读结果缓存（减少重复调用）----
    // 幂等工具（只读）在短时间内以相同参数重复调用时直接复用上次结果，省掉一次后端往返。
    // 只缓存 status=ok 的结果——错误必须真实重试，不能被缓存掩盖。
    // TTL 20s，刻意短于后端 30s 幂等去重：前端先命中，后端去重仍作为第二道防线。
    _readCache: {},
    _readCacheTtl: 20000,
    _readCacheMax: 200,
    _readCacheKey(name, args) {
      try { return String(name) + '|' + JSON.stringify(args || {}); } catch (e) { return ''; }
    },
    getCachedResult(name, args) {
      const key = this._readCacheKey(name, args);
      if (!key) return null;
      const hit = this._readCache[key];
      if (!hit) return null;
      if (Date.now() - hit.ts > this._readCacheTtl) { delete this._readCache[key]; return null; }
      return hit.result;
    },
    cacheResult(name, args, result) {
      const key = this._readCacheKey(name, args);
      if (!key) return;
      this._readCache[key] = { ts: Date.now(), result: result };
      const keys = Object.keys(this._readCache);
      if (keys.length > this._readCacheMax) {
        keys.sort((a, b) => this._readCache[a].ts - this._readCache[b].ts);
        for (const k of keys.slice(0, keys.length - 100)) delete this._readCache[k];
      }
    },
    clearReadCache() { this._readCache = {}; },
    // 追踪文本（复制/上报用）：[时间] 阶段 | 详情
    traceText() {
      return this._trace.map(function (r) {
        return '[' + r.t + '] ' + r.stage + (r.detail ? ' | ' + r.detail : '');
      }).join('\n');
    },

    // ---- 最后一条命令记忆（面板「手动执行」框自动回填）----
    //
    // 动机：自动解析/执行失败时，用户往往只需「原样改一点再执行」。若面板不留存刚发出的那条
    // 命令，用户只能凭记忆重敲——MCP 长参数（base64、JSON 串）实际等于无法手动兜底。
    // 因此命令在「解析阶段」就落存（无论后续解析/执行成败都覆盖为最新一条），并持久化到
    // GM 存储跨刷新可用；执行结果只更新其 ok 标记，不改写命令文本。
    _lastCommand: null,
    _cmdMaxLen: 20000,   // 持久化上限，防止把整段超长参数写进 GM 存储
    _manualDirty: false, // 面板手动框是否被用户改过（改过后新命令不再覆盖其编辑内容）

    // 调用数组 → 可回填的命令文本（一行一条，与 @@tool 协议格式一致）
    callsToText(calls) {
      const lines = [];
      for (const c of (calls || [])) {
        if (!c || !c.name) continue;
        let args = '{}';
        try { args = JSON.stringify(c.arguments || {}); } catch (e) { args = '{}'; }
        lines.push('@@tool ' + c.name + ' ' + args);
      }
      return lines.join('\n');
    },

    // 从原始回复里抽取 @@tool 行（解析失败时保留现场，供人工修正后重发）
    extractCommandLines(text) {
      const out = [];
      const lines = this._normalizeText(text).split('\n');
      for (const line of lines) {
        if (/^[\s>#|`*_+~]*(?:\d+[.)][\s]*)?(?:```[a-zA-Z]*[\s]*)?@@tool/i.test(line)) {
          out.push(line.trim());
        }
      }
      return out.join('\n');
    },

    _nowText() {
      const d = new Date();
      const pad = function (n) { return String(n).length < 2 ? '0' + n : String(n); };
      return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' +
        pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    },

    // 保存「最后一条命令」。source: auto|manual；invalid: 该命令未能解析出可执行调用；
    // reason: 未识别/失败原因（面板据此提示用户从哪一步接手）。
    saveLastCommand(text, meta) {
      const s = String(text == null ? '' : text).trim();
      if (!s) return null;
      const m = meta || {};
      const clipped = s.length > this._cmdMaxLen
        ? s.slice(0, this._cmdMaxLen) + '\n...[命令过长，已截断存储]' : s;
      this._lastCommand = {
        text: clipped,
        ts: Date.now(),
        time: this._nowText(),
        count: m.count || clipped.split('\n').filter(function (l) { return /@@tool/i.test(l); }).length || 1,
        source: m.source || 'auto',
        invalid: !!m.invalid,
        reason: m.reason ? String(m.reason) : '',
        ok: null,   // null=未执行/未知；true=执行成功；false=执行失败
      };
      this._persistLastCommand();
      this._syncManualPanel();
      return this._lastCommand;
    },

    // 执行完成后回填结果标记（只改 ok，不覆盖命令文本，避免把用户手动修正的内容冲掉）
    markLastCommandResult(ok, detail) {
      if (!this._lastCommand) return;
      this._lastCommand.ok = !!ok;
      if (detail) this._lastCommand.reason = String(detail).slice(0, 200);
      this._persistLastCommand();
      this._syncManualPanel();
    },

    getLastCommand() { return this._lastCommand; },

    _persistLastCommand() {
      try {
        if (typeof GM_setValue === 'function' && this._lastCommand) {
          GM_setValue('lastCommand', JSON.stringify(this._lastCommand));
        }
      } catch (e) { /* 存储失败不影响主流程 */ }
    },

    // 启动时恢复上次会话的最后一条命令（跨刷新可用）
    loadLastCommand() {
      try {
        if (typeof GM_getValue !== 'function') return;
        const raw = GM_getValue('lastCommand', '');
        if (!raw) return;
        const info = typeof raw === 'string' ? JSON.parse(raw) : raw;
        if (info && info.text) {
          info.ok = null;   // 上次会话的执行结果对本次无意义，复位为未知
          this._lastCommand = info;
        }
      } catch (e) { /* 损坏的存储值直接忽略 */ }
    },

    // 面板提示文案（时间 · 条数 · 来源/状态）
    lastCommandLabel() {
      const c = this._lastCommand;
      if (!c) return '（暂无自动保存的命令）';
      const flags = [];
      if (c.source === 'manual') flags.push('手动执行');
      if (c.invalid) flags.push('未识别');
      if (c.ok === true) flags.push('执行成功');
      else if (c.ok === false) flags.push('执行失败');
      return (c.time || '') + ' · ' + (c.count || 1) + ' 条' +
        (flags.length ? ' · ' + flags.join('/') : '');
    },

    // 把最后一条命令同步到面板手动执行框：面板未打开 / 用户正在编辑 → 不动输入框，只更新提示
    _syncManualPanel() {
      try {
        if (typeof document === 'undefined') return;
        const hint = document.getElementById('dsw-manual-hint');
        if (hint) {
          const c = this._lastCommand;
          hint.textContent = c
            ? ('已自动保存：' + this.lastCommandLabel() +
              (c.reason ? ' · ' + c.reason : '') +
              (this._manualDirty ? '（你已修改，新命令不再覆盖）' : ''))
            : '自动解析出的命令会保存在这里，失败时可修改后重试。';
        }
        if (this._manualDirty) return;
        const box = document.getElementById('dsw-manual');
        if (box && this._lastCommand) box.value = this._lastCommand.text;
      } catch (e) { /* 面板不在页面时静默 */ }
    },

    // ---- 通用请求（GM_xmlhttpRequest 优先，绕过 CORS；fallback fetch）----
    request(url, options) {
      const opts = options || {};
      const timeoutMs = opts.timeout || 45000;  // 默认 45s 超时（防调用卡住挂"执行中"状态）
      // 默认头必须在两个分支共用：fetch 回退分支若不带 Content-Type，
      // 后端 pydantic 会把 JSON 字符串当成 body 原样接收 → 422
      // （无 Tampermonkey / GM_xmlhttpRequest 不可用时的回退路径会踩到）
      const headers = opts.headers || { 'Content-Type': 'application/json' };
      return new Promise((resolve, reject) => {
        if (typeof GM_xmlhttpRequest === 'function') {
          GM_xmlhttpRequest({
            method: opts.method || 'GET',
            url: url,
            data: opts.body || undefined,
            headers: headers,
            timeout: timeoutMs,
            onload: (resp) => {
              try { resolve({ status: resp.status, data: JSON.parse(resp.responseText) }); }
              catch (e) { resolve({ status: resp.status, data: resp.responseText }); }
            },
            onerror: (e) => reject(new Error('网络错误: ' + (e && e.error))),
            ontimeout: () => reject(new Error('请求超时 (' + timeoutMs + 'ms)')),
          });
        } else {
          const ctrl = (typeof AbortController === 'function') ? new AbortController() : null;
          const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
          fetch(url, Object.assign({}, opts, { headers: headers },
            ctrl ? { signal: ctrl.signal } : {}))
            .then((r) => r.json()).then((data) => resolve({ status: 200, data }))
            .catch((e) => reject(new Error('请求超时或失败: ' + (e && e.message))))
            .finally(() => { if (timer) clearTimeout(timer); });
        }
      });
    },

    async loadRolecard() {
      const r = await this.request(this.config.backendUrl + '/api/bridge/rolecard');
      this.config.rolecard = (r.data && r.data.rolecard) || '';
      return this.config.rolecard;
    },

    async loadTools() {
      const r = await this.request(this.config.backendUrl + '/api/bridge/tools');
      this.config.tools = (r.data && r.data.tools) || [];
      return this.config.tools;
    },

    // 健康检查
    async health() {
      try {
        const r = await this.request(this.config.backendUrl + '/health');
        return r.status === 200 && r.data && r.data.status === 'ok';
      } catch (e) { return false; }
    },

    // 解析文本里的全部工具调用 → [{name, arguments}]
    // tools 可选：传入工具清单即启用白名单校验（清单外的调用直接忽略，杜绝误触发）
    parseToolCalls(text, tools) {
      if (!text) return [];
      const schema = this._toolMap(tools);
      const lines = this._normalizeText(text).split('\n');
      const re = this._toolLineRe();
      const calls = [];
      for (let i = 0; i < lines.length; i++) {
        const m = lines[i].match(re);
        if (!m) continue;
        const name = String(m[1]);
        let rest = m[2] || '';
        // JSON 可能跨行（模型 pretty-print）→ 括号未平衡时续读后续行；
        // 撞上下一个 @@tool 行或代码块围栏即止，防止把后续正文吞进参数
        let j = i;
        while (j + 1 < lines.length && !this._balanced(rest)) {
          const next = lines[j + 1];
          if (/@@tool/i.test(next) || /^[ \t]*```/.test(next)) break;
          rest += '\n' + next;
          j += 1;
        }
        i = j;
        const args = this._parseArgs(rest);
        if (args === null) {
          this.trace('parse/fail', name + ' 参数 JSON 解析失败: ' +
            String(rest).replace(/\s+/g, ' ').slice(0, 200));
          continue;
        }
        if (schema && !Object.prototype.hasOwnProperty.call(schema, name)) {
          this.trace('parse/reject', '工具名不在清单内，已忽略: ' + name);
          continue;
        }
        // 必填参数缺失 → 不执行。发出一个注定失败（甚至带副作用）的空调用比不执行更糟
        // （实测出现过 @@tool PowerShell 未带 command 就被发出去的情况）
        const required = (schema && schema[name] && schema[name].required) || [];
        const missing = required.filter(function (k) {
          const v = args[k];
          return v === undefined || v === null || v === '';
        });
        if (missing.length) {
          this.trace('parse/missing-args', name + ' 缺少必填参数 [' + missing.join(', ') +
            '] → 未执行，请补全参数后重发');
          continue;
        }
        // 参数为空但工具声明了入参：多半是模型漏写。仍执行（确实存在无参工具），
        // 真正的拦截交给上面的 required 校验；这里只留痕，便于事后排查空调用。
        if (schema && schema[name] && schema[name].properties &&
          Object.keys(schema[name].properties).length && !Object.keys(args).length) {
          this.trace('parse/empty-args', name + ' 未带任何参数（该工具有声明入参），若失败请让模型补全');
        }
        this._warnSuspiciousChars(name, args);
        calls.push({ name: name, arguments: args });
      }
      return calls;
    },

    // 工具清单 → {工具名: input_schema}；清单为空返回 null（跳过校验，避免未加载时误杀）
    _toolMap(tools) {
      const list = tools || this.config.tools || [];
      if (!list.length) return null;
      const map = {};
      for (const t of list) {
        if (typeof t === 'string') { map[t] = null; continue; }
        if (t && t.name) map[t.name] = t.input_schema || null;
      }
      return map;
    },

    // 参数解析：从行内剩余文本取 JSON 对象（括号平衡切片 + 宽松解析）
    // null = 解析失败；{} = 无参数调用
    _parseArgs(rest) {
      const s = String(rest || '').trim().replace(/```+$/, '').trim();
      const i = s.indexOf('{');
      // 有剩余文本却不含 JSON → 判定失败（避免把"@@tool x 说明文字"当无参数调用执行）
      if (i < 0) return s ? null : {};
      return this._parseJsonLoose(this._balancedSlice(s, i));
    },

    // 括号平衡切片：从 s[start]='{' 起扫到配对的 '}'（跳过字符串字面量内的括号）
    _balancedSlice(s, start) {
      let depth = 0, inStr = false, esc = false;
      for (let i = start; i < s.length; i++) {
        const ch = s[i];
        if (inStr) {
          if (esc) { esc = false; continue; }
          if (ch === '\\') { esc = true; continue; }
          if (ch === '"') inStr = false;
          continue;
        }
        if (ch === '"') { inStr = true; continue; }
        if (ch === '{') depth += 1;
        else if (ch === '}') {
          depth -= 1;
          if (depth === 0) return s.slice(start, i + 1);
        }
      }
      return s.slice(start);   // 未闭合（流式中间态）→ 返回剩余，交由宽松解析判失败
    },

    // 括号是否平衡（判断是否需续读下一行）；无 JSON 时视为平衡
    _balanced(s) {
      let depth = 0, inStr = false, esc = false;
      for (let i = 0; i < s.length; i++) {
        const ch = s[i];
        if (inStr) {
          if (esc) { esc = false; continue; }
          if (ch === '\\') { esc = true; continue; }
          if (ch === '"') inStr = false;
          continue;
        }
        if (ch === '"') { inStr = true; continue; }
        if (ch === '{') depth += 1;
        else if (ch === '}') depth -= 1;
      }
      return depth <= 0;
    },

    // JSON 宽松解析：常规 → SSE 反转义 → Windows 单反斜杠修复 → 值内未转义引号修复
    _parseJsonLoose(raw) {
      const asArgs = (v) => (v && typeof v === 'object' && !Array.isArray(v)) ? v : null;
      try { return asArgs(JSON.parse(raw)); } catch (e) { /* 逐级降级 */ }
      // 降级 1：SSE 报文里模型输出的 JSON 引号可能被转义（\"）→ 反转义后重试
      try { return asArgs(JSON.parse(raw.replace(/\\"/g, '"').replace(/\\\\/g, '\\'))); }
      catch (e) { /* 继续降级 */ }
      // 降级 2：Windows 路径单反斜杠是非法 JSON 转义（\D → \\D），宽容修复后重试
      try { return asArgs(JSON.parse(this._fixBackslash(raw))); } catch (e) { /* 继续降级 */ }
      // 降级 3：JSON 值内未转义引号（模型常见错误：command 值里嵌套 " 未转义）
      try { return asArgs(JSON.parse(this._fixInnerQuotes(this._fixBackslash(raw)))); }
      catch (e) { /* 继续降级 */ }
      // 降级 4：Unicode 减号/破折号变体（U+2212 等）——网页端渲染把 ASCII '-' 换成数学减号
      // 后命令串失效（实测 DeepSeek 把 'Get-Process' 渲染成 'Get−Process'）。
      // 仅在解析失败的降级路径替换，避免在正常内容里误改合法字符。
      try {
        return asArgs(JSON.parse(this._fixDashes(this._fixInnerQuotes(this._fixBackslash(raw)))));
      } catch (e) { /* 彻底失败 */ }
      return null;
    },

    // Unicode 减号/破折号变体 → ASCII '-'（只在降级路径使用）
    _fixDashes(s) {
      return String(s).replace(/[\u2212\u2010\u2011\u2012\u2013\u2014\u2015\u207b\u208b\uff0d]/g, '-');
    },

    // 解析成功后仍可能含 Unicode 变体字符（能解析，但命令执行会失败）。
    // 只记录提示不改内容——改内容有风险，而模型看到工具返回的错误会自行纠正。
    _warnSuspiciousChars(name, args) {
      try {
        const bad = JSON.stringify(args).match(/[\u2212\u2010-\u2015\uff0d]/g);
        if (bad) {
          this.trace('parse/suspicious', name + ' 参数含 Unicode 减号变体 [' +
            [...new Set(bad)].join(' ') + ']（疑似网页渲染替换了 \'-\'），若执行失败请改用 ASCII 减号');
        }
      } catch (e) { /* 检查失败不影响主流程 */ }
    },

    _fixBackslash(raw) {
      return raw
        .replace(/\\"/g, '"')
        .replace(/\\\\/g, '\\')
        .replace(/\\([^"\\\/bfnrtu])/g, '\\\\$1');  // \X(非法) → \\X
    },

    // 值内未转义引号修复：状态机——字符串值内遇到 " 且后跟非 , } : 字符 → 转义为 \"
    _fixInnerQuotes(fixed) {
      const chars = fixed.split('');
      let inStr = false, i = 0;
      const out = [];
      while (i < chars.length) {
        const ch = chars[i];
        if (ch === '\\') { out.push(ch); if (i + 1 < chars.length) { out.push(chars[i + 1]); i += 2; } else i++; continue; }
        if (ch === '"') {
          if (!inStr) { inStr = true; out.push(ch); }
          else {
            const next = chars[i + 1];
            if (next === undefined || /[,}:]/.test(next)) { inStr = false; out.push(ch); }
            else { out.push('\\'); out.push(ch); }  // 值内引号 → 转义
          }
          i++;
          continue;
        }
        if (ch === ':' && !inStr) { out.push(ch); i++; continue; }
        out.push(ch);
        i++;
      }
      return out.join('');
    },

    // ---- 工具调用解析（v4 行锚定格式）----
    //
    // 格式：行首 @@tool <工具名> <单行 JSON 参数>
    //     @@tool read_file {"path":"C:/a.txt"}
    //
    // 为什么弃用 start:{...}end：
    //   1) 终止符依赖：模型漏写 end → 整条不匹配 → 静默"没反应"（实测最高频故障）；
    //   2) 正则边界脆弱：[\s\S]*? 遇嵌套 } 提前截断，只能靠多级降级补丁续命；
    //   3) start: 是常见英文词，模型在正文里"谈论"格式时易误触发。
    // 行锚定 + 括号平衡：调用边界由行首锚点与 JSON 自身括号决定，不存在"可以漏写的结尾"。
    TOOL_MARKER: '@@tool',

    // SSE 响应文本 → 模型实际输出文本。
    // 网页端回复以 SSE 流返回（每行 `data: {...}`），模型输出被包在 JSON 字符串里且带转义，
    // 直接喂给解析器会因「@@tool 不在行首」而漏检 —— 行锚定协议必须先还原原文再解析。
    // （旧协议正则不要求行首所以能凑合，这是换协议后必须补的一环。）
    decodeReply(raw) {
      const s = String(raw || '');
      if (!/(^|\n)\s*data:/.test(s)) return s;   // 非 SSE（普通 JSON/文本）→ 原样返回
      let out = '';
      for (const line of s.split('\n')) {
        const m = line.match(/^\s*data:\s*(.*)$/);
        if (!m) continue;
        const payload = m[1].trim();
        if (!payload || payload === '[DONE]') continue;
        let obj;
        try { obj = JSON.parse(payload); } catch (e) { continue; }
        out += this._sseText(obj);
      }
      return out.length ? out : s;   // 解码无果 → 回退原文，避免丢信息
    },

    // 从单个 SSE 事件对象里取文本片段（站点结构不同，adapter 可覆盖本方法）
    _sseText(obj) {
      if (obj == null) return '';
      if (typeof obj === 'string') return obj;
      const v = obj.v;
      if (typeof v === 'string') return v;
      // OpenAI 兼容格式：{"choices":[{"delta":{"content":"..."}}]}
      if (Array.isArray(obj.choices) && obj.choices.length) {
        const d = obj.choices[0] && obj.choices[0].delta;
        if (d && typeof d.content === 'string') return d.content;
      }
      return '';
    },

    // 隐形字符归一化：模型输出经网页端 markdown 渲染后常混入零宽字符与弯引号。
    // 它们不可见，却会破坏行首锚定和 JSON 解析——实测 DeepSeek 会把 '-' 渲染成 U+2212、
    // 引号渲染成弯引号、分段处插入零宽空格，不归一化调用就会"凭空消失"。
    _normalizeText(text) {
      return String(text == null ? '' : text)
        .replace(/[\u200b\u200c\u200d\ufeff]/g, '')                  // 零宽/不可见 → 删除
        .replace(/[\u201c\u201d\u201e\u201f\u2033\u275d\u275e]/g, '"')  // 弯双引号 → 直引号
        .replace(/[\u2018\u2019\u201a\u201b\u275b\u275c]/g, "'");       // 弯单引号 → 直引号
    },

    // 行首锚定：容许 markdown 装饰（标题 #、引用 >、列表 -/1.、表格 |、加粗 **/行内代码 `、
    // 代码块围栏 ```）与各类空白（含 NBSP）；@@tool 之前不允许出现实际文字。
    _toolLineRe() {
      // 工具名字符集必须保留下划线与连字符（read_file / mcp__windows-mcp__PowerShell），
      // 只排除 markdown 装饰用的反引号、星号、波浪号与空白
      return /^[\s>#|`*_+~]*(?:\d+[.)][\s]*)?(?:```[a-zA-Z]*[\s]*)?@@tool[`*_~\s:]*([^\s`*~]+)[\s]*([^\n]*)$/i;
    },

    // 检测文本里是否含工具调用指令
    hasToolCalls(text) {
      if (!text) return false;
      const lines = this._normalizeText(text).split('\n');
      const re = /^[\s>#|`*_+~]*(?:\d+[.)][\s]*)?(?:```[a-zA-Z]*[\s]*)?@@tool[`*_~\s:]*\S+/i;
      for (let i = 0; i < lines.length; i++) if (re.test(lines[i])) return true;
      return false;
    },

    // 单个工具调用（meta 可选：{caller_id, tab_id, batch_id}）
    async callTool(toolName, args, meta) {
      const m = Object.assign({}, this._meta(), meta || {});
      const r = await this.request(this.config.backendUrl + '/api/bridge/call', {
        method: 'POST',
        body: JSON.stringify({
          tool: toolName, arguments: args || {},
          caller_id: m.caller_id, tab_id: m.tab_id, batch_id: m.batch_id,
        }),
      });
      this._checkApprovalRequired(r.data);
      return r.data;
    },

    // 批量工具调用（meta 可选：{caller_id, tab_id, batch_id}；同批共享一个 batch_id）
    async callTools(calls, meta, timeoutMs) {
      const m = Object.assign({}, this._meta(), meta || {});
      const r = await this.request(this.config.backendUrl + '/api/bridge/call', {
        method: 'POST',
        timeout: timeoutMs || undefined,   // 由 callToolsWithRetry 按剩余预算下发
        body: JSON.stringify({
          calls: calls.map(c => ({ name: c.name, arguments: c.arguments || {} })),
          caller_id: m.caller_id, tab_id: m.tab_id, batch_id: m.batch_id,
        }),
      });
      this._checkApprovalRequired(r.data);
      return r.data;
    },

    // 带重试的批量调用。两条硬约束，缺一不可：
    // 1) 仅当整批全部为幂等（只读）工具时才重试 —— 写入/终端/MCP 工具重复执行即事故。
    //    （MCP 工具的 idempotent 由后端统一标注为 false，因此天然被排除。）
    // 2) 只重试「请求层失败」（网络错误 / 超时，即 request reject）。
    //    HTTP 200 里携带的业务结果（status:error、approval_required）是真实结果，
    //    绝不重试 —— 否则一次审批拒绝会被放大成 N 次无效请求。
    async callToolsWithRetry(calls, meta) {
      const list = calls || [];
      const allIdempotent = list.length > 0 &&
        list.every(function (c) { return c && this.isIdempotentTool(c.name); }, this);
      // 非法/负值一律收敛为 0：否则 `i <= max` 恒假，会一次请求都不发却抛出 null，
      // 上层只会看到「❌ 工具执行失败: null」，极难排查
      const raw = allIdempotent ? this.config.callMaxRetries : 0;
      const max = Math.max(0, typeof raw === 'number' ? Math.floor(raw) : (raw == null ? 2 : 0));
      const deadline = Date.now() + (this.config.callTotalTimeoutMs || 30000);
      // meta 只生成一次：让同批重试共享 batch_id，后端日志才能把几次重试串起来
      const m = Object.assign({}, this._meta(), meta || {});
      let lastErr = null;
      for (let i = 0; i <= max; i++) {
        if (i > 0) {
          const backoff = 1000 * i;   // 线性退避：1s、2s
          if (Date.now() + backoff > deadline) break;
          await new Promise(function (r) { setTimeout(r, backoff); });
          if (Date.now() >= deadline) break;   // 退避完成后复检：退避本身也可能吃掉预算
          this.trace('call/retry', '第 ' + i + ' 次重试（整批均为只读工具）n=' + list.length);
        }
        const remain = deadline - Date.now();
        if (remain <= 0) break;
        try {
          // 剩余预算同时约束单次请求：否则 request 默认 45s 会让「总超时」形同虚设
          return await this.callTools(list, m, remain);
        } catch (e) {
          lastErr = e;
        }
      }
      throw lastErr || new Error('调用未发起（重试预算不足）');
    },

    // 检测结果里的 approval_required → 提示用户去控制台批准后重试
    _checkApprovalRequired(data) {
      const results = (data && data.results) || [];
      const blocked = results.filter(x => x && x.approval_required);
      if (!blocked.length) return;
      const names = blocked.map(x => x.tool + '#' + x.request_id).join(', ');
      const msg = '[bridge] 工具调用需要人工审批: ' + names +
        '\n请在后端审批队列（POST /api/approvals/<id>/approve，或审批控制台）批准后，再重试该调用。';
      console.warn(msg);
      try { if (typeof alert === 'function') alert(msg); } catch (e) {}
    },

    // 轮询后端挂起的人类确认请求（每 2s，最多 60s），发现后弹确认框并回传应答
    // question 为提示词，用于优先匹配；只在本 caller 的挂起列表里找，不跨 caller 代答（多 profile 隔离）
    async askUser(question) {
      const callerId = this.ensureCallerId();
      const tabId = this._tabId;   // 同 profile 多标签：只认领本标签发起的确认请求
      const deadline = Date.now() + 60000;
      while (Date.now() < deadline) {
        let pending = [];
        try {
          const q = 'caller_id=' + encodeURIComponent(callerId) +
            '&tab_id=' + encodeURIComponent(tabId);
          const r = await this.request(this.config.backendUrl + '/api/bridge/ask_user/pending?' + q);
          pending = (r.data && r.data.pending) || [];
        } catch (e) { /* 网络抖动：继续轮询 */ }
        const target = pending.find(p => question && p.question === question) || (pending.length ? pending[0] : null);
        if (target) {
          const answer = this._promptForAnswer(target.question);
          try {
            await this.request(this.config.backendUrl + '/api/bridge/ask_user', {
              method: 'POST',
              body: JSON.stringify({
                request_id: target.request_id, answer: answer,
                caller_id: callerId, tab_id: tabId,
              }),
            });
          } catch (e) { console.warn('[bridge] ask_user 应答回传失败:', e); }
          return { request_id: target.request_id, question: target.question, answer: answer };
        }
        await new Promise(res => setTimeout(res, 2000));
      }
      return { error: '等待人类确认超时（60s），未发现本账号的挂起请求' };
    },

    // 弹确认框：优先浏览器原生 prompt()/confirm()；取消 → "用户取消"
    _promptForAnswer(question) {
      const q = question || '请确认以下请求';
      const yesNo = /[?？]|确认|是否|yes|no/i.test(q);
      try {
        if (yesNo) {
          const ok = (typeof confirm === 'function') ? confirm(q + '\n\n确认请点「确定」，拒绝请点「取消」') : true;
          return ok ? '是' : '否';
        }
        const input = (typeof prompt === 'function') ? prompt(q) : null;
        return (input === null || input === undefined) ? '用户取消' : String(input);
      } catch (e) {
        return '用户取消';
      }
    },

    // 把后端「逐调用全量信封」渲染成给模型的「工具调用结果」文本：
    // 每信封一段（request_id + 工具名 + 原始调用回显 + 状态），null/错误/审批/大结果均有显式说明，
    // 满足「任何输出（含 null）都必须返回以触发下一步」，并让模型能把结果对应回原始命令。
    formatResults(results) {
      if (!Array.isArray(results)) results = results || [];
      const blocks = [];
      for (const r of results) {
        const b = this._formatOneResult(r);
        if (b) blocks.push(b);
      }
      if (!blocks.length) blocks.push('【工具结果】调用未返回结果（无）。');
      return blocks.join('\n\n');
    },

    _formatOneResult(r) {
      if (!r || typeof r !== 'object') return '【工具结果】未收到有效信封。';
      const rid = r.request_id || '';
      const tool = r.tool || 'unknown';
      const head = '【工具结果' + (rid ? ' #' + rid : '') + '】' + tool;
      // 原始命令回显（由信封回显的 arguments 重建，模型可据此区分同批多次调用）。
      // 必须用当前协议格式回显：模型会把回显当模板模仿，回显旧格式会造成格式倒退。
      let original = tool;
      try {
        original = '@@tool ' + tool + ' ' + JSON.stringify(r.arguments || {});
      } catch (e) { /* 保留 tool 名 */ }
      const lines = [head, '原始调用: ' + original];
      // 缓存命中：明确告知模型结果来自复用，避免它误以为是新执行的结果
      if (r._cached) lines.push('（复用 20s 内相同调用的缓存结果，未重复执行）');
      const status = r.status || 'ok';
      if (status === 'error') {
        const err = r.error || {};
        lines.push('❌ 执行失败: ' + (err.type || 'Error') +
          (err.message ? ': ' + String(err.message).slice(0, 500) : '') +
          '。请根据错误信息修正参数后重试（同一参数最多重试一次）。');
        return lines.join('\n');
      }
      if (status === 'approval_required' || r.approval_required) {
        lines.push('⏸ 该调用需要人工审批（request_id=' + rid + '）。请在审批控制台批准后，重新发起同一调用。');
        return lines.join('\n');
      }
      // status === 'ok'
      const v = r.result;
      if (r.truncated && r.file) {
        const preview = (r.summary != null ? String(r.summary) : '');
        if (r._enriched) {
          // 高级开关 autoEnrichExternalized：完整内容已内联到 preview，无需再提示上传
          lines.push('完整结果（已内联，共 ' + preview.length + ' 字符，附件: ' + r.file + '）：');
          lines.push(preview || '(内容为空)');
          return lines.join('\n');
        }
        // 大结果：预览 + 附件路径。完整内容的取得方式刻意改为「提示用户上传附件」：
        // 网页输入框对超长回填会截断，附件上传则不受此限；让模型默认 read_file 全文会多耗
        // 一轮上下文，且长回填仍可能被截断。read_file 仅作为用户无法上传时的兜底。
        lines.push('结果较长，已截断预览（' + preview.length + ' 字符）：');
        lines.push(preview || '(预览为空)');
        lines.push('完整结果已保存为附件: ' + r.file);
        if (r.file_md) lines.push('（排版更清晰的 Markdown 版: ' + r.file_md + '）');
        lines.push('请提示用户：在网页输入框点击「附件/📎」上传该文件，即可基于完整内容继续；' +
          '不要要求“再发一次完整内容”，也不要默认自己 read_file 全文。' +
          '仅当用户明确要求你直接读取、或明确无法上传附件时，才用 read_file 读取（可按 limit/offset 分段）。');
        return lines.join('\n');
      }
      if (v === null || v === undefined) {
        lines.push('✅ 返回 null（调用成功，无内容），请继续下一步。');
        return lines.join('\n');
      }
      lines.push('结果:');
      try {
        lines.push(typeof v === 'string' ? v : JSON.stringify(v, null, 2));
      } catch (e) {
        lines.push(String(v));
      }
      return lines.join('\n');
    },

    // 保存模型输出到后端（data/conversations/*.log），供调试/审计（内容 hash 去重）
    _loggedHashes: {},
    async saveReply(text, role) {
      if (!text || String(text).length < 10) return;
      const s = String(text);
      // 跳过非聊天响应（DeepSeek 风控 challenge 等）
      if (/challenge|DeepSeekHashV1|biz_code/i.test(s) && !/start\s*:\s*\{/.test(s)) return;
      let h = 0;
      for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
      const key = (role || 'assistant') + ':' + h;
      if (this._loggedHashes[key]) return;
      this._loggedHashes[key] = 1;
      try {
        await this.request(this.config.backendUrl + '/api/bridge/log', {
          method: 'POST',
          body: JSON.stringify({
            role: role || 'assistant',
            content: s.slice(0, 20000),
            caller_id: this.ensureCallerId(),
          }),
        });
      } catch (e) { /* 保存失败静默（不影响对话） */ }
    },

    // 配置存取（Tampermonkey 持久化）
    loadConfig() {
      try {
        if (typeof GM_getValue === 'function') {
          this.config.backendUrl = GM_getValue('backendUrl', this.config.backendUrl);
          this.config.autoInject = GM_getValue('autoInject', this.config.autoInject);
          this.config.autoSend = GM_getValue('autoSend', this.config.autoSend);
          this.config.autoEnrichExternalized =
            GM_getValue('autoEnrichExternalized', this.config.autoEnrichExternalized);
          this.config.autoRepair = GM_getValue('autoRepair', this.config.autoRepair);
          this.config.sessionSwitchCooldownMs =
            GM_getValue('sessionSwitchCooldownMs', this.config.sessionSwitchCooldownMs);
          this.config.batchQuiesceMs =
            GM_getValue('batchQuiesceMs', this.config.batchQuiesceMs);
          this.config.autoSendDelayMs =
            GM_getValue('autoSendDelayMs', this.config.autoSendDelayMs);
          this.config.callMaxRetries = GM_getValue('callMaxRetries', this.config.callMaxRetries);
          this.config.callTotalTimeoutMs =
            GM_getValue('callTotalTimeoutMs', this.config.callTotalTimeoutMs);
        }
      } catch (e) {}
      this.ensureCallerId();
      this.loadLastCommand();   // 恢复上次会话自动保存的最后一条命令（面板手动兜底用）
    },
    saveConfig() {
      try {
        if (typeof GM_setValue === 'function') {
          GM_setValue('backendUrl', this.config.backendUrl);
          GM_setValue('autoInject', this.config.autoInject);
          GM_setValue('autoSend', this.config.autoSend);
          GM_setValue('autoEnrichExternalized', this.config.autoEnrichExternalized);
          GM_setValue('autoRepair', this.config.autoRepair);
          GM_setValue('sessionSwitchCooldownMs', this.config.sessionSwitchCooldownMs);
          GM_setValue('batchQuiesceMs', this.config.batchQuiesceMs);
          GM_setValue('autoSendDelayMs', this.config.autoSendDelayMs);
          GM_setValue('callMaxRetries', this.config.callMaxRetries);
          GM_setValue('callTotalTimeoutMs', this.config.callTotalTimeoutMs);
        }
      } catch (e) {}
    },

    // ---- 控制面板数据/动作 ----

    // 重载全部 MCP 服务
    async reloadMcp() {
      const r = await this.request(this.config.backendUrl + '/api/mcp/reload-all', { method: 'POST' });
      return (r.data && r.data.results) || {};
    },
    // 强制刷新技能目录
    async reloadSkills() {
      const r = await this.request(this.config.backendUrl + '/api/skills/reload', { method: 'POST' });
      return (r.data && r.data) || {};
    },
    // 技能发现目录列表
    async getSkillDirs() {
      const r = await this.request(this.config.backendUrl + '/api/skills/dirs');
      return (r.data && r.data.dirs) || [];
    },
    // MCP 服务状态列表
    async getMcpState() {
      const r = await this.request(this.config.backendUrl + '/api/mcp');
      return (r.data && r.data.services) || (r.data && r.data) || [];
    },
  };

  /**
   * 站点无关的「呈现 / 注入」层（v3.3.0）
   *
   * 由来：这部分逻辑原写在 adapters/deepseek.js 中，与 DeepSeek 专属选择器混杂。
   * 接入小米 MiMo 时若整份复制会产生约 300 行重复代码，且后续任何 UI 修复都要改两处，
   * 故上提为共享层。
   *
   * 用法：adapter 声明站点差异后，在文件末尾调用 BridgeUI.install(MyAdapter) 混入通用方法。
   *       adapter 上已存在的同名成员优先保留（用于覆盖站点专属行为，
   *       例如 DeepSeek 的发送按钮定位逻辑）。
   *
   * adapter 需声明的站点差异：
   *   INPUT_SELECTORS    输入框候选选择器（按优先级）
   *   SEND_SELECTORS     发送按钮候选选择器（SEND_STRATEGY='click' 时使用）
   *   MESSAGE_SELECTORS  消息容器候选选择器（DOM 观察通道取最后一条）
   *   API_URL_PATTERN    聊天接口 URL 正则（fetch/XHR 劫持过滤，务必精确到生成接口，
   *                      否则心跳轮询会持续触发 fullHandler → 浪费后端调用并撞限流）
   *   ROLECARD_MARKER    注入去重标记文本
   *   SEND_STRATEGY      'keyboard' | 'click'
   *   SEND_KEY_SEQUENCE  键盘发送的按键序列（keyboard 策略）
   *   MAX_FILL           回填截断上限（字符）
   */
  const BridgeUI = {
    // 回填内容所有权标记：判断输入框里的内容是「桥接回填的工具结果」还是「用户草稿」。
    // 含标记的内容可按轮次清残留/续接；用户草稿（无标记）绝不清除。
    RESULT_MARKER: '【工具结果',

    // ---- 状态按钮 + 配置面板 ----

    ensureStatusButton() {
      if (this._statusBtn) return;
      this._ensurePulseStyle();
      const btn = document.createElement('div');
      btn.style.cssText = [
        'position:fixed', 'top:16px', 'right:16px', 'z-index:2147483646',
        'width:14px', 'height:14px', 'border-radius:50%', 'background:#9ca3af',
        'cursor:pointer', 'box-shadow:0 2px 8px rgba(0,0,0,.35)', 'border:2px solid #fff',
        'transition:transform .2s',
      ].join(';');
      btn.title = 'DS Bridge 状态（点击打开配置）';
      btn.addEventListener('mouseenter', () => { btn.style.transform = 'scale(1.2)'; });
      btn.addEventListener('mouseleave', () => { btn.style.transform = 'scale(1)'; });
      btn.addEventListener('click', () => this._toggleConfigPanel());
      document.body.appendChild(btn);
      this._statusBtn = btn;
      this._setStatusColor('unknown');
    },

    // 工具调用状态 → 圆点颜色（主流程在关键节点显式驱动，不依赖 toast 文案前缀）
    STATE_STYLE: {
      connected: { color: '#10b981', label: '已连接' },
      busy: { color: '#3b82f6', label: '工具调用执行中' },
      error: { color: '#ef4444', label: '调用失败' },
      warning: { color: '#f59e0b', label: '需人工审批 / 格式待修正' },
      unknown: { color: '#9ca3af', label: '未知' },
    },
    setCallState(state) { this._setStatusColor(state); },

    // 执行中状态的呼吸动画（keyframes 全局注入一次）
    _ensurePulseStyle() {
      try {
        if (document.getElementById('dsw-pulse-style')) return;
        const st = document.createElement('style');
        st.id = 'dsw-pulse-style';
        st.textContent = '@keyframes dsw-pulse{0%,100%{opacity:1;transform:scale(1)}' +
          '50%{opacity:.4;transform:scale(1.35)}}';
        document.head.appendChild(st);
      } catch (e) { /* 样式注入失败不影响主流程 */ }
    },

    _setStatusColor(state) {
      if (!this._statusBtn) return;
      const s = this.STATE_STYLE[state] || this.STATE_STYLE.unknown;
      this._statusBtn.style.background = s.color;
      this._statusBtn.title = 'DS Bridge: ' + s.label + '（点击打开配置）';
      // 执行中：呼吸动画，一眼可辨"正在跑"；其余状态静止
      this._statusBtn.style.animation =
        (state === 'busy') ? 'dsw-pulse 1s ease-in-out infinite' : '';
    },

    _toggleConfigPanel() {
      if (this._configPanel) {
        this._stopTraceTimer();
        this._configPanel.remove();
        this._configPanel = null;
        window.BridgeCore._manualDirty = false;   // 关面板即复位编辑标记，下次打开重新回填最后一条命令
        return;
      }
      const self = this;
      // 大结果自动内联开关初值来自 BridgeCore.config（loadConfig 已从 GM 读取）
      let enrichOn = false;
      try {
        enrichOn = !!(window.BridgeCore && window.BridgeCore.config &&
          window.BridgeCore.config.autoEnrichExternalized);
      } catch (e) {}
      // 自动纠错开关初值（默认开）
      let repairOn = true;
      try {
        repairOn = !!(window.BridgeCore && window.BridgeCore.config &&
          window.BridgeCore.config.autoRepair);
      } catch (e) {}
      const panel = document.createElement('div');
      panel.style.cssText = [
        'position:fixed', 'top:38px', 'right:16px', 'z-index:2147483646',
        'width:320px', 'max-height:80vh', 'overflow:auto', 'background:#fff',
        'border-radius:12px', 'box-shadow:0 8px 32px rgba(0,0,0,.22)', 'padding:14px',
        'font:13px/1.5 system-ui,-apple-system,sans-serif', 'color:#1f2937',
      ].join(';');
      panel.innerHTML =
        '<div style="font-weight:600;margin-bottom:10px">⚙️ DS Bridge 控制</div>' +
        '<div id="dsw-status" style="margin-bottom:10px;padding:8px 10px;background:#f3f4f6;border-radius:8px;font-size:12px">' +
        '连接中…</div>' +
        '<div style="display:flex;gap:6px;margin-bottom:12px">' +
        '<button id="dsw-init" style="flex:1;padding:7px;background:#4f46e5;color:#fff;border:none;border-radius:8px;cursor:pointer">📝 手动初始化</button>' +
        '<button id="dsw-mcp" style="flex:1;padding:7px;background:#059669;color:#fff;border:none;border-radius:8px;cursor:pointer">🔌 重载 MCP</button>' +
        '<button id="dsw-skills" style="flex:1;padding:7px;background:#d97706;color:#fff;border:none;border-radius:8px;cursor:pointer">📚 刷新技能</button>' +
        '</div>' +
        '<div style="margin-bottom:6px">后端地址</div>' +
        '<input id="dsw-url" value="' + this._backendUrl + '" ' +
        'style="width:100%;padding:7px;border:1px solid #d1d5db;border-radius:8px;box-sizing:border-box;margin-bottom:10px;font-size:13px"/>' +
        '<label style="display:flex;align-items:center;gap:6px;margin-bottom:8px;cursor:pointer">' +
        '<input type="checkbox" id="dsw-auto" ' + (this._autoInject ? 'checked' : '') + '/> 自动注入 role_card</label>' +
        '<label style="display:flex;align-items:center;gap:6px;margin-bottom:8px;cursor:pointer" title="工具结果就绪后自动发送（可能触发站点风控）">' +
        '<input type="checkbox" id="dsw-send" ' + (this._autoSend ? 'checked' : '') + '/> 自动发送工具结果</label>' +
        '<label style="display:flex;align-items:center;gap:6px;margin-bottom:12px;cursor:pointer" title="大结果默认回填预览+附件文件路径，模型按需 read_file；开启后自动拉取附件全量内联（更占上下文）">' +
        '<input type="checkbox" id="dsw-enrich" ' + (enrichOn ? 'checked' : '') + '/> 自动内联外置大结果</label>' +
        '<label style="display:flex;align-items:center;gap:6px;margin-bottom:12px;cursor:pointer" title="模型输出的工具调用格式不符时，自动生成纠错提示填入输入框；开启「自动发送」时会一并发送，让链路自动收敛">' +
        '<input type="checkbox" id="dsw-repair" ' + (repairOn ? 'checked' : '') + '/> 自动纠错格式偏差</label>' +
        '<div id="dsw-dirs" style="margin-bottom:12px;font-size:11px;color:#6b7280">加载目录信息…</div>' +
        // ---- 调用追踪：模型回复/解析/后端调用/回填各环节流水，排查「没反应」的第一现场 ----
        '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">' +
        '<span style="font-weight:600">📋 调用追踪</span>' +
        '<span style="font-size:11px;color:#6b7280">1.5s 刷新</span></div>' +
        '<div id="dsw-trace" style="max-height:200px;overflow:auto;background:#0f172a;color:#e5e7eb;' +
        'border-radius:8px;padding:8px;margin-bottom:6px;font:11px/1.55 ui-monospace,Menlo,Consolas,monospace;' +
        'white-space:pre-wrap;word-break:break-all">载入中…</div>' +
        '<div style="display:flex;gap:6px;margin-bottom:12px">' +
        '<button id="dsw-trace-copy" style="flex:1;padding:6px;background:#374151;color:#fff;border:none;' +
        'border-radius:8px;cursor:pointer;font-size:12px">📄 复制追踪</button>' +
        '<button id="dsw-trace-clear" style="flex:1;padding:6px;background:#9ca3af;color:#fff;border:none;' +
        'border-radius:8px;cursor:pointer;font-size:12px">🗑 清空</button>' +
        '</div>' +
        // ---- 手动执行：绕过模型直接调用工具（调试工具 / 模型格式失败时的人工兜底）----
        // 命令来源：自动解析出的最后一条命令会自动填入（GM 持久化，跨刷新仍在），失败时
        // 用户改参数即可重试；「填入最后一条」用于用户改乱后一键还原。
        '<div style="display:flex;align-items:center;justify-content:space-between;margin:10px 0 6px">' +
        '<span style="font-weight:600">⚡ 手动执行</span>' +
        '<button id="dsw-manual-recall" title="把自动保存的最后一条命令填入下方输入框" ' +
        'style="padding:3px 8px;background:#eef2ff;color:#4338ca;border:1px solid #c7d2fe;' +
        'border-radius:6px;cursor:pointer;font-size:11px">↩ 填入最后一条</button></div>' +
        '<textarea id="dsw-manual" rows="2" placeholder="@@tool read_file {&quot;path&quot;:&quot;C:/a.txt&quot;}" ' +
        'style="width:100%;padding:7px;border:1px solid #d1d5db;border-radius:8px;box-sizing:border-box;' +
        'margin-bottom:4px;font:12px ui-monospace,Menlo,Consolas,monospace;resize:vertical"></textarea>' +
        '<div id="dsw-manual-hint" style="font-size:11px;color:#6b7280;margin-bottom:6px;line-height:1.4">' +
        '自动解析出的命令会保存在这里，失败时可修改后重试。</div>' +
        '<div style="display:flex;gap:6px;margin-bottom:6px">' +
        '<button id="dsw-run" style="flex:1;padding:6px;background:#4f46e5;color:#fff;border:none;' +
        'border-radius:8px;cursor:pointer;font-size:12px">▶ 执行</button>' +
        '<button id="dsw-run-fill" style="flex:1;padding:6px;background:#059669;color:#fff;border:none;' +
        'border-radius:8px;cursor:pointer;font-size:12px">▶ 执行并回填</button>' +
        '</div>' +
        '<div id="dsw-manual-out" style="max-height:160px;overflow:auto;background:#0f172a;color:#e5e7eb;' +
        'border-radius:8px;padding:8px;margin-bottom:12px;font:11px/1.55 ui-monospace,Menlo,Consolas,monospace;' +
        'white-space:pre-wrap;word-break:break-all">（执行结果显示在这里）</div>' +
        '<button id="dsw-save" ' +
        'style="width:100%;padding:8px;background:#4f46e5;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:13px">保存并重连</button>';
      document.body.appendChild(panel);
      this._configPanel = panel;

      // 手动执行框：面板一打开就回填「最后一条命令」（自动解析时已落存，GM 持久化跨刷新可用）。
      // 用户一旦改动即标记 dirty —— 此后新到达的命令不再覆盖其编辑内容，避免"正在改参数时
      // 被自动填入的命令冲掉"。改乱后可用「↩ 填入最后一条」一键还原。
      const manualBox = panel.querySelector('#dsw-manual');
      const manualOut = panel.querySelector('#dsw-manual-out');
      window.BridgeCore._manualDirty = false;
      try {
        window.BridgeCore.loadLastCommand();
        const last = window.BridgeCore.getLastCommand();
        if (last && last.text) manualBox.value = last.text;
        window.BridgeCore._syncManualPanel();
      } catch (e) { /* 回填失败不影响面板其他功能 */ }
      manualBox.addEventListener('input', () => {
        window.BridgeCore._manualDirty = true;
        try { window.BridgeCore._syncManualPanel(); } catch (e) {}
      });

      // 状态 + 目录信息（异步加载）
      const statusEl = panel.querySelector('#dsw-status');
      const dirsEl = panel.querySelector('#dsw-dirs');
      (async () => {
        try {
          const core = window.BridgeCore;
          const [mcpState, skillDirs, health] = await Promise.all([
            core.getMcpState().catch(() => []),
            core.getSkillDirs().catch(() => []),
            core.health(),
          ]);
          const mcpList = Array.isArray(mcpState) ? mcpState
            : Object.keys(mcpState || {}).map((k) => ({ name: k, status: mcpState[k] && mcpState[k].status }));
          statusEl.innerHTML = (health ? '🟢 已连接' : '🔴 未连接') + ' · ' +
            core.config.tools.length + ' 个工具' +
            ' · MCP: ' + (mcpList.length ? mcpList.map((s) => s.name + (s.status === 'running' ? '🟢' : '⚪')).join(' ') : '无') +
            '<br/><span style="color:#6b7280">' + (skillDirs.map((d) => '📁 ' + d.source + ': ' + d.path).join('<br/>') || '') + '</span>';
        } catch (e) {
          statusEl.textContent = '状态加载失败: ' + (e && e.message || e);
        }
      })();

      // 手动初始化
      panel.querySelector('#dsw-init').addEventListener('click', async () => {
        try {
          const core = window.BridgeCore;
          await core.loadRolecard();
          const ok = await self.forceInject(core.config.rolecard);
          self.setStatus(ok ? '📝 已手动注入 role_card' : '❌ 手动注入失败');
        } catch (e) {
          self.setStatus('❌ 手动注入异常: ' + (e && e.message || e));
        }
      });
      // 重载 MCP
      panel.querySelector('#dsw-mcp').addEventListener('click', async () => {
        try {
          self.setStatus('🔧 正在重载 MCP 服务…');
          const results = await window.BridgeCore.reloadMcp();
          const ok = Object.values(results).filter((v) => v === 'reloaded').length;
          self.setStatus('✅ MCP 重载完成（' + ok + '/' + Object.keys(results).length + '）');
        } catch (e) {
          self.setStatus('❌ MCP 重载失败: ' + (e && e.message || e));
        }
      });
      // 刷新技能
      panel.querySelector('#dsw-skills').addEventListener('click', async () => {
        try {
          const r = await window.BridgeCore.reloadSkills();
          self.setStatus('✅ 技能目录已刷新（' + r.count + ' 个技能）');
        } catch (e) {
          self.setStatus('❌ 技能刷新失败: ' + (e && e.message || e));
        }
      });
      // 调用追踪：复制（优先剪贴板，不可用时回退控制台，保证任何环境都拿得到）
      panel.querySelector('#dsw-trace-copy').addEventListener('click', () => {
        const core = window.BridgeCore;
        const text = (core && core.traceText) ? core.traceText() : '';
        if (!text) { self.setStatus('⚠️ 暂无追踪记录'); return; }
        const fallback = () => { console.log('[bridge] 调用追踪:\n' + text); };
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(
              () => self.setStatus('✅ 追踪记录已复制（' + text.split('\n').length + ' 行）'),
              () => { fallback(); self.setStatus('⚠️ 剪贴板不可用，已输出到控制台'); }
            );
          } else { fallback(); self.setStatus('⚠️ 剪贴板不可用，已输出到控制台'); }
        } catch (e) { fallback(); }
      });
      // 调用追踪：清空
      panel.querySelector('#dsw-trace-clear').addEventListener('click', () => {
        const core = window.BridgeCore;
        if (core && core.clearTrace) core.clearTrace();
        this._renderTrace();
        self.setStatus('🗑 追踪记录已清空');
      });

      // 手动执行：绕过模型直接把调用发给后端 —— 调试工具，或模型输出格式失败时的人工兜底
      async function runManual(fillBack) {
        const core = window.BridgeCore;
        const text = (manualBox.value || '').trim();
        if (!text) {
          manualOut.textContent = '请输入调用，例如：@@tool read_file {"path":"C:/a.txt"}';
          return;
        }
        // 传 [] 跳过白名单：手动调试时工具名可能不在已加载清单内，是否存在交给后端校验
        let calls = core.parseToolCalls(text, []);
        if (!calls.length) {
          const m = text.match(/^@@tool\s+(\S+)/i);
          if (m) calls = [{ name: String(m[1]).replace(/[`"']+$/, ''), arguments: {} }];
        }
        if (!calls.length) {
          manualOut.textContent = '未识别出调用。格式：@@tool 工具名 {"参数":"值"}';
          // 保留现场：用户改错格式后仍能一键取回原始文本继续修正
          core.saveLastCommand(text, { source: 'manual', invalid: true, reason: '未识别出可执行调用' });
          return;
        }
        // 手动命令同样纳入「最后一条命令」：面板重开后仍是这一条，便于反复调试
        core.saveLastCommand(core.callsToText(calls), { source: 'manual', count: calls.length });
        window.BridgeCore._manualDirty = false;
        manualOut.textContent = '执行中…';
        core.trace('manual/run', '[' + calls.map(function (c) { return c.name; }).join(', ') +
          '] fillBack=' + (fillBack ? 'yes' : 'no'));
        try {
          const resp = await core.callTools(calls);
          const results = (resp && resp.results) || [];
          const rendered = core.formatResults(results);
          manualOut.textContent = rendered;
          const failed = results.some(function (r) {
            return r && (r.status === 'error' || r.approval_required);
          });
          core.markLastCommandResult(!failed, failed ? '手动执行有失败调用' : '');
          if (fillBack) {
            const ok = await self.fillInput(rendered + '\n\n（以上为手动执行的结果）', 'manual');
            core.trace('manual/fill', ok ? '已回填 ' + rendered.length + ' 字符'
              : '回填失败：未找到输入框');
          }
        } catch (e) {
          manualOut.textContent = '执行失败: ' + ((e && e.message) || e);
          core.markLastCommandResult(false, (e && e.message) || String(e));
          core.trace('manual/error', (e && e.message) || String(e));
        }
      }
      panel.querySelector('#dsw-run').addEventListener('click', () => runManual(false));
      panel.querySelector('#dsw-run-fill').addEventListener('click', () => runManual(true));
      // 一键取回自动保存的最后一条命令（用户改乱后可还原）
      panel.querySelector('#dsw-manual-recall').addEventListener('click', () => {
        try {
          const last = window.BridgeCore.getLastCommand();
          if (!last || !last.text) { manualOut.textContent = '暂无自动保存的命令。'; return; }
          manualBox.value = last.text;
          window.BridgeCore._manualDirty = false;
          window.BridgeCore._syncManualPanel();
          manualOut.textContent = '已填入最后一条命令（' + window.BridgeCore.lastCommandLabel() + '）';
        } catch (e) {}
      });

      // 面板开启期间定时刷新追踪（1.5s），保证对话进行中也能看到最新流水
      this._renderTrace();
      this._startTraceTimer();

      // 保存
      panel.querySelector('#dsw-save').addEventListener('click', () => {
        this._backendUrl = (panel.querySelector('#dsw-url').value || '').trim() || 'http://localhost:8088';
        this._autoInject = panel.querySelector('#dsw-auto').checked;
        this._autoSend = panel.querySelector('#dsw-send').checked;
        const enrich = panel.querySelector('#dsw-enrich').checked;
        const repair = panel.querySelector('#dsw-repair').checked;
        try {
          if (window.BridgeCore) {
            window.BridgeCore.config.autoEnrichExternalized = enrich;
            window.BridgeCore.config.autoRepair = repair;
          }
        } catch (e) {}
        try {
          if (typeof GM_setValue === 'function') {
            GM_setValue('backendUrl', this._backendUrl);
            GM_setValue('autoInject', this._autoInject);
            GM_setValue('autoSend', this._autoSend);
            GM_setValue('autoEnrichExternalized', enrich);
            GM_setValue('autoRepair', repair);
          }
        } catch (e) {}
        this._stopTraceTimer();
        panel.remove();
        this._configPanel = null;
        location.reload();
      });
    },

    // ---- 调用追踪渲染 ----

    _traceTimer: null,
    _startTraceTimer() {
      const self = this;
      this._stopTraceTimer();
      this._traceTimer = setInterval(function () { self._renderTrace(); }, 1500);
    },
    _stopTraceTimer() {
      if (this._traceTimer) { clearInterval(this._traceTimer); this._traceTimer = null; }
    },
    // 渲染最近 40 条（面板高度有限，全量可点「复制追踪」导出）
    _renderTrace() {
      const el = document.getElementById('dsw-trace');
      if (!el) return;
      const core = window.BridgeCore;
      const rows = (core && core.getTrace) ? core.getTrace() : [];
      if (!rows.length) {
        el.textContent = '（暂无记录）\n模型回复 / 工具调用 / 回填各环节会自动记到这里。\n' +
          '若模型已输出工具调用而此处仍为空，说明回复未被脚本捕获。';
        return;
      }
      el.textContent = rows.slice(-40).map(function (r) {
        return '[' + r.t + '] ' + r.stage + (r.detail ? '\n    ' + r.detail : '');
      }).join('\n');
      el.scrollTop = el.scrollHeight;   // 自动贴底，始终看最新
    },

    // ---- 轻量 toast ----

    setStatus(text) {
      if (!this._toast) {
        this._toast = document.createElement('div');
        this._toast.style.cssText = [
          'position:fixed', 'top:72px', 'right:16px', 'z-index:2147483647',
          'padding:9px 14px', 'border-radius:12px', 'font-size:13px',
          'font-family:system-ui,-apple-system,sans-serif', 'line-height:1.45',
          'max-width:380px', 'box-shadow:0 6px 24px rgba(0,0,0,0.18)',
          'backdrop-filter:blur(8px)', 'border:1px solid rgba(255,255,255,0.12)',
          'transition:opacity .25s, transform .25s', 'white-space:pre-wrap',
          'opacity:0', 'transform:translateY(-6px)', 'pointer-events:none',
        ].join(';');
        document.body.appendChild(this._toast);
      }
      let bg = 'rgba(30,41,59,0.92)';
      if (text.indexOf('✅') === 0) bg = 'rgba(16,122,87,0.95)';
      else if (text.indexOf('❌') === 0) bg = 'rgba(185,28,28,0.95)';
      else if (text.indexOf('⚠️') === 0) bg = 'rgba(146,64,14,0.95)';
      else if (text.indexOf('🔧') === 0) bg = 'rgba(29,78,216,0.95)';
      this._toast.style.background = bg;
      this._toast.textContent = text;
      this._toast.style.opacity = '1';
      this._toast.style.transform = 'translateY(0)';
      clearTimeout(this._toast._t);
      // 执行中（🔧）保持显示直到 closeStatus() 显式关闭（60s 兜底防悬挂）；
      // 其余类型自动消失
      const isBusy = text.indexOf('🔧') === 0 || text.indexOf('…') >= 0;
      if (isBusy) {
        this._toast._t = setTimeout(() => this.closeStatus(), 60000);
      } else {
        this._toast._t = setTimeout(() => {
          this._toast.style.opacity = '0';
          this._toast.style.transform = 'translateY(-6px)';
          this._setStatusColor('connected');  // 提示消失后圆点恢复已连接色
        }, 3500);
      }
      // 同步状态按钮颜色
      if (text.indexOf('✅') === 0) this._setStatusColor('connected');
      else if (text.indexOf('❌') === 0) this._setStatusColor('error');
      else if (text.indexOf('⚠️') === 0) this._setStatusColor('warning');
      else if (text.indexOf('🔧') === 0) this._setStatusColor('busy');
    },

    // 立即关闭当前 toast（调用结束后自动关闭提示）
    closeStatus() {
      if (!this._toast) return;
      this._toast.style.opacity = '0';
      this._toast.style.transform = 'translateY(-6px)';
      clearTimeout(this._toast._t);
      // 状态圆点恢复为已连接色（避免残留"调用中"蓝色）
      this._setStatusColor('connected');
    },

    // ---- 注入 ----

    // 强制注入（跳过注入去重检查）——供面板「手动初始化」按钮
    forceInject(rolecard) {
      return this.injectRolecard(rolecard, true);
    },

    async injectRolecard(rolecard, force) {
      if (!rolecard) return false;
      try {
        // 注入去重：页面已存在系统规范消息（刷新页面/切换会话）则跳过，避免重复注入污染对话
        const marker = this.ROLECARD_MARKER || '系统规范（必须严格遵守）';
        if (!force) {
          try {
            if ((document.body.innerText || '').includes(marker)) {
              console.log('[bridge] 检测到已注入 role_card，跳过重复注入');
              return true;
            }
          } catch (e) {}
        }
        const input = await this._waitFor(() => this._findInput(), 8000);
        if (!input) { console.warn('[bridge] 未找到输入框，注入失败'); return false; }
        this._setInput(input, rolecard);
        await new Promise(r => setTimeout(r, 400));  // 等 React onChange 生效
        // 键盘发送（避免点击按钮——可能误点文件上传弹文件选择器）
        const sent = await this.sendMessage();
        console.log('[bridge] role_card 已注入并发送（键盘发送）');
        return sent;
      } catch (e) {
        console.warn('[bridge] 注入 role_card 失败:', e);
        return false;
      }
    },

    // React 受控输入框：必须用原型上的 native setter 赋值，再派发 input/change，
    // 否则 React 无法感知变更（value 会被后续渲染覆盖回旧值）
    _setInput(input, text) {
      if (input.tagName === 'TEXTAREA' || input.tagName === 'INPUT') {
        const proto = input.tagName === 'TEXTAREA'
          ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        const nativeSetter = Object.getOwnPropertyDescriptor(proto, 'value').set;
        nativeSetter.call(input, text);
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      } else {
        input.focus();
        input.textContent = text;
        input.dispatchEvent(new InputEvent('input', { bubbles: true, data: text }));
      }
    },

    // 回填工具结果：直接填入真实结果文本（不依赖占位符+请求体替换——
    // 该机制依赖站点接口结构，页面更新即失效；直接回填最可靠）
    // turn 参数标识“轮次”：同一轮内多次回填续接追加；新轮开始前若输入框残留上一轮的
    // 未发送结果（含回填标记）则先清空重置，保证上下文干净；用户草稿（无标记）绝不清除。
    async fillInput(text, turn) {
      const MAX = this.MAX_FILL || 60000;
      let display = String(text || '');
      if (!display.trim()) return false;
      if (display.length > MAX) display = display.slice(0, MAX) + '\n...[结果已截断；完整内容已存为附件，请在网页输入框点「附件/📎」上传该文件以获取全文]';
      const input = this._findInput();
      if (!input) return false;
      const cur = this._inputValue(input) || '';
      const markerRe = /【工具结果|\[工具\d+执行结果\]/;
      const owned = cur.trim() && markerRe.test(cur);   // 桥接回填产物（非用户草稿）
      if (owned) {
        const prevTurn = (this._fillTurn === undefined) ? 0 : this._fillTurn;
        const curTurn = (turn === undefined) ? prevTurn : turn;
        if (curTurn === prevTurn) {
          // 同一轮：续接，不覆盖（多批结果合并成一条消息）
          display = cur + '\n\n' + display;
          if (display.length > MAX) display = display.slice(0, MAX) + '\n...[结果已截断；完整内容已存为附件，请在网页输入框点「附件/📎」上传该文件以获取全文]';
        } else {
          // 新轮：上一轮残留尚未发送 → 重置后再写入，确保上下文干净
        }
        this._fillTurn = curTurn;
      } else if (cur.trim()) {
        // 用户草稿：保留草稿，结果续接其后（不销毁用户输入）
        display = cur + '\n\n' + display;
        if (display.length > MAX) display = display.slice(0, MAX) + '\n...[结果已截断；完整内容已存为附件，请在网页输入框点「附件/📎」上传该文件以获取全文]';
      }
      this._setInput(input, display);
      input.focus();
      // 高亮输入框提示用户按发送（10 秒）
      const prev = input.style.boxShadow;
      input.style.boxShadow = '0 0 0 3px #10b981';
      setTimeout(() => { input.style.boxShadow = prev; }, 10000);
      return true;
    },

    // ---- 发送 ----

    sendMessage() {
      if (this.SEND_STRATEGY === 'click') return this._clickSend();
      return this._keyboardSend();
    },

    // 自动发送 = 普通发送
    autoSend() {
      return this.sendMessage();
    },

    // 键盘发送（优先）——完全避开按钮定位（避免误点文件上传弹文件选择器）
    async _keyboardSend() {
      const input = this._findInput();
      if (!input) return false;
      input.focus();
      const seq = this.SEND_KEY_SEQUENCE || [{ key: 'Enter' }];
      for (const init of seq) {
        input.dispatchEvent(new KeyboardEvent('keydown',
          Object.assign({ bubbles: true, cancelable: true }, init)));
        await new Promise(r => setTimeout(r, 300));
      }
      await new Promise(r => setTimeout(r, 500));
      return true;
    },

    // 点击发送（键盘发送不生效时的兜底）
    async _clickSend() {
      const input = this._findInput();
      if (input) input.focus();
      const btn = this._findSend();
      if (!btn) return false;
      btn.click();
      await new Promise(r => setTimeout(r, 500));
      return true;
    },

    // ---- 模型回复监听（三通道）----
    //
    // fullHandler：fetch/XHR 通道（流结束后的完整文本，用于保存 + 工具检测）
    // domHandler：DOM 观察通道（流式中间文本，仅做工具检测，避免重复保存）
    onModelReply(fullHandler, domHandler) {
      this._replyHandler = fullHandler;
      const detect = domHandler || fullHandler;
      const apiPattern = this.API_URL_PATTERN;
      const self = this;
      // SSE 原始流 → 模型输出文本：行锚定协议要求 @@tool 处于行首，而 SSE 把文本包在
      // JSON 字符串里，不还原就会漏检（非 SSE 文本原样返回，不丢信息）
      const decode = function (t) {
        try { return self.decodeReply ? self.decodeReply(t) : t; } catch (e) { return t; }
      };

      // 通道 1：fetch 劫持（响应监听）
      try {
        const origFetch = window.fetch;
        if (origFetch && !window.__dswFetchHooked) {
          window.__dswFetchHooked = true;
          window.__dswHandler = fullHandler;
          window.fetch = function (...args) {
            const p = origFetch.apply(this, args);
            try {
              const url = String(args[0] && args[0].url ? args[0].url : args[0]);
              if (apiPattern.test(url)) {
                p.then((resp) => {
                  try {
                    resp.clone().text().then((t) => {
                      if (t && t.length > 20) window.__dswHandler && window.__dswHandler(decode(t));
                    }).catch(() => {});
                  } catch (e) {}
                }).catch(() => {});
              }
            } catch (e) {}
            return p;
          };
        }
      } catch (e) { console.warn('[bridge] fetch 劫持失败:', e); }

      // 通道 2：XHR 劫持（老接口兜底）
      try {
        const origOpen = XMLHttpRequest.prototype.open;
        const origSend = XMLHttpRequest.prototype.send;
        if (!window.__dswXhrHooked) {
          window.__dswXhrHooked = true;
          window.__dswHandler = fullHandler;
          XMLHttpRequest.prototype.open = function (method, url) {
            this.__dswUrl = String(url);
            return origOpen.apply(this, arguments);
          };
          XMLHttpRequest.prototype.send = function (body) {
            this.addEventListener('load', () => {
              try {
                const u = this.__dswUrl || '';
                if (apiPattern.test(u)) {
                  window.__dswHandler && window.__dswHandler(decode(this.responseText || ''));
                }
              } catch (e) {}
            });
            return origSend.apply(this, arguments);
          };
        }
      } catch (e) { console.warn('[bridge] XHR 劫持失败:', e); }

      // 通道 3：DOM 观察兜底（仅检测，不保存——流式中间文本会重复触发）
      // 文本指纹：内容未变化时跳过，避免每次字符更新都全量 querySelectorAll + 正则；
      // 指纹带 30s 时间桶，同一文本 30 秒后重新检测（不遗漏长时间后的相同消息）
      const msgSel = (this.MESSAGE_SELECTORS || []).join(', ');
      if (!msgSel) return null;
      let lastTextKey = null;
      const observer = new MutationObserver(() => {
        const msgs = document.querySelectorAll(msgSel);
        if (!msgs.length) return;
        const last = msgs[msgs.length - 1];
        const text = (last.textContent || '').trim();
        const key = Math.floor(Date.now() / 30000) + ':' + text.length + ':' + text.slice(-300);
        if (key === lastTextKey) return;
        lastTextKey = key;
        if (text) detect(text);
      });
      observer.observe(document.body, { childList: true, subtree: true, characterData: true });
      return observer;
    },

    // ---- DOM 工具 ----

    _findInput() {
      for (const sel of this.INPUT_SELECTORS) {
        try {
          const el = document.querySelector(sel);
          if (el) return el;
        } catch (e) {}
      }
      return null;
    },

    // 发送按钮定位（通用版）：按候选选择器顺序查找。
    // 站点结构复杂时（如 DeepSeek 输入框行内并排多个按钮）由 adapter 覆盖本方法。
    _findSend() {
      for (const sel of this.SEND_SELECTORS) {
        try {
          const el = document.querySelector(sel);
          if (el) return el;
        } catch (e) {}
      }
      return null;
    },

    _inputValue(input) {
      try {
        if (input.tagName === 'TEXTAREA' || input.tagName === 'INPUT') return input.value;
        return (input.textContent || '');
      } catch (e) { return ''; }
    },

    _waitFor(selectorFn, timeoutMs) {
      return new Promise((resolve) => {
        const t0 = Date.now();
        const tick = () => {
          const el = selectorFn();
          if (el) return resolve(el);
          if (Date.now() - t0 > timeoutMs) return resolve(null);
          setTimeout(tick, 200);
        };
        tick();
      });
    },

    // ---- 配置存取 ----

    loadConfig() {
      try {
        if (typeof GM_getValue === 'function') {
          this._backendUrl = GM_getValue('backendUrl', this._backendUrl);
          this._autoInject = GM_getValue('autoInject', this._autoInject);
          this._autoSend = GM_getValue('autoSend', this._autoSend);
        }
      } catch (e) {}
    },

    getConfig() {
      return { backendUrl: this._backendUrl, autoInject: this._autoInject, autoSend: this._autoSend };
    },
    setConfig(cfg) {
      if (cfg && cfg.backendUrl) this._backendUrl = cfg.backendUrl;
      if (cfg && typeof cfg.autoInject === 'boolean') this._autoInject = cfg.autoInject;
      if (cfg && typeof cfg.autoSend === 'boolean') this._autoSend = cfg.autoSend;
    },

    // 把通用实现混入 adapter（adapter 自有成员优先，便于覆盖站点专属行为）
    install(adapter) {
      for (const k of Object.keys(this)) {
        if (k === 'install') continue;
        if (adapter[k] === undefined) adapter[k] = this[k];
      }
      return adapter;
    },
  };

  global.BridgeCore = BridgeCore;
  global.BridgeUI = BridgeUI;
})(typeof window !== 'undefined' ? window : this);
