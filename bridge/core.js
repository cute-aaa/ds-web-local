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
      rolecard: '',
      tools: [],
    },

    // ---- 通用请求（GM_xmlhttpRequest 优先，绕过 CORS；fallback fetch）----
    request(url, options) {
      const opts = options || {};
      const timeoutMs = opts.timeout || 45000;  // 默认 45s 超时（防调用卡住挂"执行中"状态）
      return new Promise((resolve, reject) => {
        if (typeof GM_xmlhttpRequest === 'function') {
          GM_xmlhttpRequest({
            method: opts.method || 'GET',
            url: url,
            data: opts.body || undefined,
            headers: opts.headers || { 'Content-Type': 'application/json' },
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
          fetch(url, Object.assign({}, opts, ctrl ? { signal: ctrl.signal } : {}))
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

    // 解析 start:{...}end 工具调用（支持多行、多工具、嵌套 JSON、SSE 转义容错）
    parseToolCalls(text) {
      if (!text) return [];
      const calls = [];
      const regex = /start\s*:\s*(\{[\s\S]*?\})\s*end/g;
      let m;
      while ((m = regex.exec(text)) !== null) {
        let raw = m[1];
        try { calls.push(JSON.parse(raw)); continue; }
        catch (e) { /* 逐级降级解析 */ }
        // 降级 1：SSE 报文里模型输出的 JSON 引号可能被转义（\"）→ 反转义后重试
        try { calls.push(JSON.parse(raw.replace(/\\"/g, '"').replace(/\\\\/g, '\\'))); continue; }
        catch (e) { /* 继续降级 */ }
        // 降级 2：Windows 路径单反斜杠是非法 JSON 转义（\D → \\D），宽容修复后重试
        try {
          const fixed = raw
            .replace(/\\"/g, '"')
            .replace(/\\\\/g, '\\')
            .replace(/\\([^"\\\/bfnrtu])/g, '\\\\$1');  // \X(非法) → \\X
          calls.push(JSON.parse(fixed));
          continue;
        }
        catch (e) { /* 继续降级 */ }
        // 降级 3：JSON 值内未转义引号（模型常见错误：command 值里嵌套 " 未转义）
        // 状态机：在字符串值内遇到 " 且后跟非 , } : 字符 → 转义为 \"
        try {
          const fixed = raw
            .replace(/\\"/g, '"')
            .replace(/\\\\/g, '\\')
            .replace(/\\([^"\\\/bfnrtu])/g, '\\\\$1');
          const chars = fixed.split('');
          let inStr = false, i = 0;
          const out = [];
          while (i < chars.length) {
            const ch = chars[i];
            if (ch === '\\') { out.push(ch); if (i + 1 < chars.length) { out.push(chars[i + 1]); i += 2; } else i++; continue; }
            if (ch === '"') {
              if (!inStr) { inStr = true; out.push(ch); }
              else {
                // 值内引号：后跟非结构字符 → 转义（字符串值中间）
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
          calls.push(JSON.parse(out.join('')));
          continue;
        }
        catch (e) { if (raw.length > 40) console.debug('[bridge] 解析失败（可能是流式中间态，等待完整文本）:', raw.slice(0, 60)); }
      }
      return calls;
    },

    // 检测文本里是否含工具调用指令
    hasToolCalls(text) {
      return /start\s*:\s*\{[\s\S]*?\}\s*end/.test(text || '');
    },

    // 单个工具调用
    async callTool(toolName, args) {
      const r = await this.request(this.config.backendUrl + '/api/bridge/call', {
        method: 'POST',
        body: JSON.stringify({ tool: toolName, arguments: args || {} }),
      });
      this._checkApprovalRequired(r.data);
      return r.data;
    },

    // 批量工具调用
    async callTools(calls) {
      const r = await this.request(this.config.backendUrl + '/api/bridge/call', {
        method: 'POST',
        body: JSON.stringify({ calls: calls.map(c => ({ name: c.name, arguments: c.arguments || {} })) }),
      });
      this._checkApprovalRequired(r.data);
      return r.data;
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
    // question 为提示词，用于优先匹配；不匹配时取第一个挂起请求
    async askUser(question) {
      const deadline = Date.now() + 60000;
      while (Date.now() < deadline) {
        let pending = [];
        try {
          const r = await this.request(this.config.backendUrl + '/api/bridge/ask_user/pending');
          pending = (r.data && r.data.pending) || [];
        } catch (e) { /* 网络抖动：继续轮询 */ }
        const target = pending.find(p => question && p.question === question) || pending[0];
        if (target) {
          const answer = this._promptForAnswer(target.question);
          try {
            await this.request(this.config.backendUrl + '/api/bridge/ask_user', {
              method: 'POST',
              body: JSON.stringify({ request_id: target.request_id, answer: answer }),
            });
          } catch (e) { console.warn('[bridge] ask_user 应答回传失败:', e); }
          return { request_id: target.request_id, question: target.question, answer: answer };
        }
        await new Promise(res => setTimeout(res, 2000));
      }
      return { error: '等待人类确认超时（60s），未发现挂起请求' };
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

    // 格式化工具结果，回填给模型
    formatResults(results) {
      if (!Array.isArray(results)) results = [results];
      return results.map((r, i) => '[工具' + (i + 1) + '执行结果]\n' + JSON.stringify(r, null, 2)).join('\n\n');
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
          body: JSON.stringify({ role: role || 'assistant', content: s.slice(0, 20000) }),
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
        }
      } catch (e) {}
    },
    saveConfig() {
      try {
        if (typeof GM_setValue === 'function') {
          GM_setValue('backendUrl', this.config.backendUrl);
          GM_setValue('autoInject', this.config.autoInject);
          GM_setValue('autoSend', this.config.autoSend);
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

  global.BridgeCore = BridgeCore;
})(typeof window !== 'undefined' ? window : this);
