/**
 * 站点适配器契约模板 — 新增 web 端照此实现
 *
 * 只需声明「站点差异」，通用能力（状态圆点 / 配置面板 / toast / 输入框注入 /
 * 工具结果回填 / 发送 / 三通道回复监听）由 core.js 的 BridgeUI 提供。
 *
 * 步骤：
 *   1. 复制本文件为 adapters/<你的站点>.js
 *   2. 填写下方声明式配置（选择器建议用 CDP/F12 实测，勿凭空猜测）
 *   3. 在 build.py 的 ADAPTERS 列表里登记（文件名 + 全局名）
 *   4. 在 build.py 的 HEADER 里追加 @match
 *   5. 运行 python bridge/build.py 重新生成
 *
 * 若某个通用方法与站点结构不符（如发送按钮定位），直接在 adapter 上定义
 * 同名方法即可覆盖 —— BridgeUI.install() 只填补 adapter 上不存在的成员。
 */
(function (global) {
  'use strict';

  const MySiteAdapter = {
    id: 'my-site',
    name: '我的站点',
    match: /example\.com/,   // 域名匹配正则

    // 以下状态字段供 BridgeUI 混入的方法读写，必须初始化
    _toast: null,
    _statusBtn: null,
    _configPanel: null,
    _replyHandler: null,
    _backendUrl: 'http://localhost:8088',
    _autoInject: true,
    _autoSend: false,

    // ---- 站点差异（多候选、按优先级容错）----

    // 输入框（textarea / input / [contenteditable="true"]）
    INPUT_SELECTORS: [
      'textarea[placeholder]',
      'textarea',
      '[contenteditable="true"]',
    ],

    // 发送按钮（SEND_STRATEGY='click' 时使用；键盘策略下仅作兜底）
    SEND_SELECTORS: [
      'button[type="submit"]',
      'div[role="button"]',
    ],

    // 消息容器（DOM 观察通道取最后一条）
    MESSAGE_SELECTORS: [
      '[class*="markdown"]',
      '[class*="message"]',
    ],

    // 聊天接口 URL 正则（fetch/XHR 劫持过滤）
    // 务必精确到「生成」接口：站点通常还有心跳/埋点轮询，
    // 若一并匹配会导致每次轮询都触发解析 + 打后端（还会撞限流）。
    API_URL_PATTERN: /chat|completion/i,

    ROLECARD_MARKER: '系统规范（必须严格遵守）',  // 注入去重标记
    SEND_STRATEGY: 'keyboard',                    // 'keyboard' | 'click'
    SEND_KEY_SEQUENCE: [{ key: 'Enter' }],        // 键盘发送的按键序列
    MAX_FILL: 60000,                              // 回填截断上限（字符）

    // ---- 可选：覆盖通用实现 ----

    // 发送按钮定位（默认按 SEND_SELECTORS 顺序查找）
    // _findSend() { return document.querySelector('...'); },
  };

  // 混入 BridgeUI 的通用实现（adapter 上已定义的成员优先保留）
  global.BridgeUI.install(MySiteAdapter);
  global.MySiteAdapter = MySiteAdapter;
})(typeof window !== 'undefined' ? window : this);
