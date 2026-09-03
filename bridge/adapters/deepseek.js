/**
 * DeepSeek 网页版（chat.deepseek.com）适配器
 *
 * 通用 UI / 注入 / 监听逻辑已上提至 core.js 的 BridgeUI，本文件只保留
 * DeepSeek 站点专属部分：选择器、发送按键序列，以及发送按钮定位
 * （DeepSeek 输入框行内并排多个按钮，需按 x 坐标取最右侧的发送键）。
 *
 * 基于 v1 js2.js 验证过的机制：React 受控输入框 native setter 注入、
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
    _autoSend: false,

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

    // 消息容器（DOM 观察通道取最后一条）
    MESSAGE_SELECTORS: [
      '[class*="message"]',
      '[class*="markdown"]',
      '.ds-markdown',
    ],

    // 聊天接口 URL 过滤（fetch/XHR 劫持）
    API_URL_PATTERN: /chat|completion|conversation|message|assistant|api/i,

    ROLECARD_MARKER: '系统规范（必须严格遵守）',
    SEND_STRATEGY: 'keyboard',
    // DeepSeek 实测：先 Ctrl+Enter 再单独 Enter 才可靠提交
    SEND_KEY_SEQUENCE: [{ key: 'Enter', ctrlKey: true }, { key: 'Enter' }],
    MAX_FILL: 60000,

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
  };

  // 混入 BridgeUI 的通用实现（adapter 上已定义的 _findSend 等优先保留）
  global.BridgeUI.install(DeepSeekAdapter);
  global.DeepSeekAdapter = DeepSeekAdapter;
})(typeof window !== 'undefined' ? window : this);
