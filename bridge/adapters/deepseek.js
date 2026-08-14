/**
 * DeepSeek 网页版（chat.deepseek.com）适配器
 * 基于 v1 js2.js 验证过的机制重写：React 受控输入框 native setter 注入、
 * 输入框容器内定位发送按钮（避免误点全局按钮）、fetch/XHR 劫持 + DOM 观察
 * 双通道监听、右上角常驻状态按钮 + 配置面板。
 */
(function (global) {
  'use strict';

  const DeepSeekAdapter = {
    id: 'deepseek',
    name: 'DeepSeek',
    match: /chat\.deepseek\.com/,

    _toast: null,
    _statusBtn: null,
    _configPanel: null,
    _replyHandler: null,
    _backendUrl: 'http://localhost:8088',
    _autoInject: true,

    // 输入框选择器（多候选，容错）
    INPUT_SELECTORS: [
      'textarea._27c9245',        // v1 实测类名（保留）
      'textarea[placeholder]',
      'textarea',
      '#chat-input',
      '.chat-input',
      '[contenteditable="true"]',
    ],

    // 发送按钮选择器（容器内优先，全局兜底）
    SEND_SELECTORS: [
      'button[type="submit"]',
      'button:has(svg)',
      '.send-btn',
      '[class*="send"]',
      'div[role="button"]',
    ],

    _findInput() {
      for (const sel of this.INPUT_SELECTORS) {
        const el = document.querySelector(sel);
        if (el) return el;
      }
      return null;
    },

    // 发送按钮定位：基于输入框容器（form/父级链）内查找，避免误点搜索/附件/保存等按钮
    // DeepSeek 实测：输入框行内并排 文件按钮(左) + 发送按钮(右)，
    // 发送键特征 ds-button--iconLabelPrimary；用 x 坐标取最右侧（发送键），
    // 并排除 aria-label 含 上传/文件/attach 的按钮
    _findSend() {
      const input = this._findInput();
      if (input) {
        let node = input;
        for (let i = 0; i < 6 && node; i++) {
          node = node.parentElement;
          if (!node) continue;
          try {
            const candidates = Array.from(node.querySelectorAll('.ds-button--iconLabelPrimary, [class*="iconLabelPrimary"]'));
            const filtered = candidates.filter(b => {
              const label = (b.getAttribute && b.getAttribute('aria-label')) || '';
              return !/上传|文件|attach|voice|语音/i.test(label);
            });
            if (filtered.length) {
              // 取最右侧（x 最大）的——发送键在输入框行最右
              filtered.sort((a, b) => {
                const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
                return (rb.right || 0) - (ra.right || 0);
              });
              const btn = filtered[0];
              return btn.closest('button, div[role="button"], [class*="ds-button"]') || btn;
            }
          } catch (e) {}
        }
        // 输入框自身的 form 内 submit
        try {
          const form = input.closest('form');
          if (form) {
            const submit = form.querySelector('button[type="submit"], input[type="submit"]');
            if (submit) return submit;
          }
        } catch (e) {}
      }
      // 全局兜底：仅限明确的发送特征（iconLabelPrimary 优先，其次含 send 类名）
      try {
        const g1 = document.querySelector('.ds-button--iconLabelPrimary');
        if (g1) return g1;
      } catch (e) {}
      for (const sel of this.SEND_SELECTORS) {
        try {
          const el = document.querySelector(sel);
          if (el) return el;
        } catch (e) {}
      }
      return null;
    },

    // ---- 状态按钮 + 配置面板 ----

    ensureStatusButton() {
      if (this._statusBtn) return;
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

    _setStatusColor(state) {
      if (!this._statusBtn) return;
      const colors = {
        connected: '#10b981', error: '#ef4444', busy: '#3b82f6',
        warning: '#f59e0b', unknown: '#9ca3af',
      };
      this._statusBtn.style.background = colors[state] || colors.unknown;
      this._statusBtn.title = 'DS Bridge: ' + (state === 'connected' ? '已连接' :
        state === 'error' ? '错误' : state === 'busy' ? '执行中' :
        state === 'warning' ? '警告' : '未知') + '（点击打开配置）';
    },

    _toggleConfigPanel() {
      if (this._configPanel) {
        this._configPanel.remove();
        this._configPanel = null;
        return;
      }
      const self = this;
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
        '<label style="display:flex;align-items:center;gap:6px;margin-bottom:12px;cursor:pointer" title="工具结果就绪后自动点击发送（可能触发 DeepSeek 风控）">' +
        '<input type="checkbox" id="dsw-send" ' + (this._autoSend ? 'checked' : '') + '/> 自动发送工具结果</label>' +
        '<div id="dsw-dirs" style="margin-bottom:12px;font-size:11px;color:#6b7280">加载目录信息…</div>' +
        '<button id="dsw-save" ' +
        'style="width:100%;padding:8px;background:#4f46e5;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:13px">保存并重连</button>';
      document.body.appendChild(panel);
      this._configPanel = panel;

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
      // 保存
      panel.querySelector('#dsw-save').addEventListener('click', () => {
        this._backendUrl = (panel.querySelector('#dsw-url').value || '').trim() || 'http://localhost:8088';
        this._autoInject = panel.querySelector('#dsw-auto').checked;
        this._autoSend = panel.querySelector('#dsw-send').checked;
        try {
          if (typeof GM_setValue === 'function') {
            GM_setValue('backendUrl', this._backendUrl);
            GM_setValue('autoInject', this._autoInject);
            GM_setValue('autoSend', this._autoSend);
          }
        } catch (e) {}
        panel.remove();
        this._configPanel = null;
        location.reload();
      });
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
    async forceInject(rolecard) {
      return this.injectRolecard(rolecard, true);
    },

    // 自动发送：点击发送按钮并验证输入框清空（占位符消失=成功），失败键盘兜底
    async autoSend() {
      const input = this._findInput();
      if (!input) return false;
      const send = this._findSend();
      if (send) {
        send.click();
        await new Promise(r => setTimeout(r, 700));
        const v = this._inputValue(input);
        if (!v || !v.trim() || v === this.PLACEHOLDER) return true;  // 已发送
        console.warn('[bridge] 自动发送点击后未清空，键盘兜底');
      }
      input.focus();
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true }));
      await new Promise(r => setTimeout(r, 300));
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      await new Promise(r => setTimeout(r, 600));
      return true;
    },

    async injectRolecard(rolecard, force) {
      if (!rolecard) return false;
      try {
        // 注入去重：页面已存在系统规范消息（刷新页面/切换会话）则跳过，避免重复注入污染对话
        if (!force) {
          try {
            if ((document.body.innerText || '').includes('系统规范（必须严格遵守）')) {
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
        input.focus();
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true }));
        await new Promise(r => setTimeout(r, 300));
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
        await new Promise(r => setTimeout(r, 800));
        console.log('[bridge] role_card 已注入并发送（键盘发送）');
        return true;
      } catch (e) {
        console.warn('[bridge] 注入 role_card 失败:', e);
        return false;
      }
    },

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

    async sendMessage(text) {
      const input = this._findInput();
      if (!input) return false;
      this._setInput(input, text);
      await new Promise(r => setTimeout(r, 400));  // 等 React onChange 生效
      const send = this._findSend();
      if (send) {
        send.click();
        // 验证发送成功：输入框应被清空
        await new Promise(r => setTimeout(r, 600));
        const v = this._inputValue(input);
        if (!v || !v.trim()) return true;  // 已清空 = 发送成功
        console.warn('[bridge] 点击发送后输入框未清空，尝试键盘兜底');
      }
      // 键盘兜底（Ctrl+Enter 与 Enter 都试）
      input.focus();
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true }));
      await new Promise(r => setTimeout(r, 300));
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      await new Promise(r => setTimeout(r, 600));
      return true;
    },

    // 回填工具结果：直接填入真实结果文本（不依赖占位符+请求体替换——
    // 该机制依赖 DeepSeek 接口结构，页面更新即失效；直接回填最可靠）
    async fillInput(text) {
      const MAX = 30000;
      let display = String(text || '');
      if (display.length > MAX) display = display.slice(0, MAX) + '\n...[结果已截断，如需完整内容请分段查询]';
      const input = this._findInput();
      if (!input) return false;
      this._setInput(input, display);
      input.focus();
      // 高亮输入框提示用户按发送（10 秒）
      const prev = input.style.boxShadow;
      input.style.boxShadow = '0 0 0 3px #10b981';
      setTimeout(() => { input.style.boxShadow = prev; }, 10000);
      return true;
    },

    // 键盘发送（优先）——完全避开按钮定位（避免误点文件上传弹文件选择器）
    async sendMessage() {
      const input = this._findInput();
      if (!input) return false;
      input.focus();
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true }));
      await new Promise(r => setTimeout(r, 300));
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      await new Promise(r => setTimeout(r, 500));
      return true;
    },

    // 自动发送 = 键盘发送
    async autoSend() {
      return this.sendMessage();
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

    // 双通道监听模型回复：
    // fullHandler：fetch/XHR 通道（流结束后的完整文本，用于保存 + 工具检测）
    // domHandler：DOM 观察通道（流式中间文本，仅做工具检测，避免重复保存）
    onModelReply(fullHandler, domHandler) {
      this._replyHandler = fullHandler;
      const detect = domHandler || fullHandler;

      // 通道 1：fetch 劫持（响应监听 + 请求体占位符替换——DeepSeek 可能走 fetch 而非 XHR）
      try {
        const origFetch = window.fetch;
        if (origFetch && !window.__dswFetchHooked) {
          window.__dswFetchHooked = true;
          window.__dswHandler = fullHandler;
          const PLACEHOLDER = this.PLACEHOLDER;
          window.fetch = function (...args) {
            // 请求体替换：聊天请求里占位符 → 真实工具结果
            try {
              const url = String(args[0] && args[0].url ? args[0].url : args[0]);
              if (/chat\/completion/i.test(url) && args[1] && args[1].body &&
                  typeof args[1].body === 'string' && window.__dswPendingResults &&
                  args[1].body.includes(PLACEHOLDER)) {
                const replaced = args[1].body.split(PLACEHOLDER).join(window.__dswPendingResults);
                args[1] = Object.assign({}, args[1], { body: replaced });
                console.log('[bridge] fetch 请求体占位符已替换（' + window.__dswPendingResults.length + ' 字符）');
                const results = window.__dswPendingResults;
                setTimeout(() => {
                  if (window.__dswPendingResults === results) window.__dswPendingResults = '';
                }, 1500);
              }
            } catch (e) {}
            const p = origFetch.apply(this, args);
            try {
              const url = String(args[0] && args[0].url ? args[0].url : args[0]);
              if (/chat|completion|conversation|message|assistant|api/i.test(url)) {
                p.then((resp) => {
                  try {
                    resp.clone().text().then((t) => {
                      if (t && t.length > 20) window.__dswHandler && window.__dswHandler(t);
                    }).catch(() => {});
                  } catch (e) {}
                }).catch(() => {});
              }
            } catch (e) {}
            return p;
          };
        }
      } catch (e) { console.warn('[bridge] fetch 劫持失败:', e); }

      // 通道 2：XHR 劫持（老接口兜底 + 请求体占位符替换）
      try {
        const origOpen = XMLHttpRequest.prototype.open;
        const origSend = XMLHttpRequest.prototype.send;
        if (!window.__dswXhrHooked) {
          window.__dswXhrHooked = true;
          window.__dswHandler = fullHandler;
          const PLACEHOLDER = this.PLACEHOLDER;
          XMLHttpRequest.prototype.open = function (method, url) {
            this.__dswUrl = String(url);
            return origOpen.apply(this, arguments);
          };
          XMLHttpRequest.prototype.send = function (body) {
            // 请求体替换：聊天请求里占位符 → 真实工具结果（v1 机制，递归全树替换）
            try {
              const u = this.__dswUrl || '';
              if (/chat\/completion/i.test(u) && body && typeof body === 'string' &&
                  window.__dswPendingResults) {
                const data = JSON.parse(body);
                // 递归替换任意层级的占位符（兼容 prompt / messages[].content / 嵌套结构）
                const replaceDeep = (obj) => {
                  if (typeof obj === 'string') {
                    return obj.includes(PLACEHOLDER)
                      ? obj.split(PLACEHOLDER).join(window.__dswPendingResults) : obj;
                  }
                  if (Array.isArray(obj)) {
                    let changed = false;
                    const out = obj.map((x) => {
                      const r = replaceDeep(x);
                      if (r !== x) changed = true;
                      return r;
                    });
                    return changed ? out : obj;
                  }
                  if (obj && typeof obj === 'object') {
                    let changed = false;
                    const out = {};
                    for (const k of Object.keys(obj)) {
                      const r = replaceDeep(obj[k]);
                      if (r !== obj[k]) changed = true;
                      out[k] = r;
                    }
                    return changed ? out : obj;
                  }
                  return obj;
                };
                const newData = replaceDeep(data);
                if (newData !== data) {
                  body = JSON.stringify(newData);
                  console.log('[bridge] 占位符已替换为工具结果（' + window.__dswPendingResults.length + ' 字符）');
                  const results = window.__dswPendingResults;
                  setTimeout(() => {
                    if (window.__dswPendingResults === results) window.__dswPendingResults = '';
                  }, 1500);
                } else {
                  console.warn('[bridge] 请求体未找到占位符（可能已被发送或结构异常）');
                }
              }
            } catch (e) {}
            this.addEventListener('load', () => {
              try {
                const u = this.__dswUrl || '';
                if (/chat|completion|conversation|message|assistant|api/i.test(u)) {
                  window.__dswHandler && window.__dswHandler(this.responseText || '');
                }
              } catch (e) {}
            });
            return origSend.apply(this, arguments);
          };
        }
      } catch (e) {}

      // 通道 3：DOM 观察兜底（仅检测，不保存——流式中间文本会重复触发）
      const observer = new MutationObserver(() => {
        const msgs = document.querySelectorAll('[class*="message"], [class*="markdown"], .ds-markdown');
        if (!msgs.length) return;
        const last = msgs[msgs.length - 1];
        const text = (last.textContent || '').trim();
        if (text) detect(text);
      });
      observer.observe(document.body, { childList: true, subtree: true, characterData: true });
      return observer;
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
  };

  global.DeepSeekAdapter = DeepSeekAdapter;
})(typeof window !== 'undefined' ? window : this);
