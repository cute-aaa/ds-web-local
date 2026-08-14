/**
 * 站点适配器契约模板 — 新增 web 端照此实现
 * 实现 6 个方法即可接入新的 web 聊天端。
 */
const AdapterTemplate = {
  id: 'template',
  name: '模板适配器',
  match: /example\.com/,   // 域名匹配正则

  /** 注入系统提示词（role_card） */
  injectRolecard(rolecard) {
    // TODO: 如何把 role_card 注入对话（发送一次 / 写入 localStorage / 通过输入框）
  },

  /** 监听模型回复流，handler(增量文本) 被调用 */
  onModelReply(handler) {
    // TODO: 监听 SSE / DOM 变化，把回复文本增量传给 handler
  },

  /** 发送消息（回填工具结果给模型） */
  sendMessage(text) {
    // TODO: 写入输入框 + 触发发送
  },

  /** 状态提示（进度 / 错误） */
  setStatus(text) {
    // TODO: 覆盖层 / 悬浮窗提示
  },

  getConfig() { return {}; },
  setConfig(cfg) {},
};

if (typeof module !== 'undefined') module.exports = AdapterTemplate;
