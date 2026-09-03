/**
 * 小米 MiMo Ultra（ultraspeed.xiaomimimo.com）适配器
 *
 * 站点特征（2026-09 实测，React + Tailwind + MobX SPA，hash 路由 #/）：
 *   - 输入框：页面内唯一 textarea，placeholder「想做什么，尽管说，Shift + Enter 换行」
 *     → 由 placeholder 文案可知：Enter 发送、Shift+Enter 换行，故用键盘策略，
 *       只需派发单个 Enter（DeepSeek 需要 Ctrl+Enter 再 Enter，此处不能照搬，
 *       否则会先触发一次空提交）。
 *   - 发送按钮：圆形按钮，类名含 rounded-full + bg-black/90（仅作 click 兜底）。
 *   - 消息容器：助手回复渲染在 DIV.markdown-prose.select-text.Markdown_markdown__<hash>，
 *     其中 markdown-prose 是语义类名，Markdown_markdown__ 是 CSS Module 哈希类名
 *     （站点重新构建后哈希会变，故两者都列，取其一命中即可）。
 *   - 聊天接口：POST /fastchat/open-apis/bot/chat，走 fetch + SSE；
 *     XHR 与 EventSource 均未被使用（实测 xhr=0 / es=0）。
 *
 * 注意：API_URL_PATTERN 必须精确到 /open-apis/bot/chat，
 * 站点还有 heartbeat / metrics / genTitle 等高频轮询接口，
 * 若一并匹配会导致每次心跳都触发 fullHandler（打后端 saveReply 并消耗限流额度）。
 */
(function (global) {
  'use strict';

  const MiMoAdapter = {
    id: 'mimo',
    name: 'Xiaomi MiMo Ultra',
    match: /xiaomimimo\.com/,

    _toast: null,
    _statusBtn: null,
    _configPanel: null,
    _replyHandler: null,
    _backendUrl: 'http://localhost:8088',
    _autoInject: true,
    _autoSend: false,

    // 输入框（实测页面仅 1 个 textarea，用 placeholder 优先定位）
    INPUT_SELECTORS: [
      'textarea[placeholder]',
      'textarea',
      '[contenteditable="true"]',
    ],

    // 发送按钮（键盘策略下的兜底：圆形黑色按钮）
    SEND_SELECTORS: [
      'button[class*="rounded-full"][class*="bg-black"]',
      'button[type="submit"]',
      'div[role="button"]',
    ],

    // 消息容器：markdown-prose 为语义类名，Markdown_markdown__ 为 CSS Module 哈希兜底。
    // 注意不要直接用 [class*="prose"]——站点内代码块带 not-prose 类名会被误匹配。
    MESSAGE_SELECTORS: [
      '[class*="markdown-prose"]',
      '[class*="Markdown_markdown"]',
    ],

    // 仅匹配真正的生成接口（排除 heartbeat / metrics / genTitle 轮询）
    API_URL_PATTERN: /open-apis\/bot\/chat/,

    ROLECARD_MARKER: '系统规范（必须严格遵守）',
    SEND_STRATEGY: 'keyboard',
    // MiMo 实测：单个 Enter 即发送（Shift+Enter 才是换行）
    SEND_KEY_SEQUENCE: [{ key: 'Enter' }],
    MAX_FILL: 60000,
  };

  // 混入 BridgeUI 的通用实现（状态圆点 / 配置面板 / toast / 注入 / 回填 / 三通道监听）
  global.BridgeUI.install(MiMoAdapter);
  global.MiMoAdapter = MiMoAdapter;
})(typeof window !== 'undefined' ? window : this);
