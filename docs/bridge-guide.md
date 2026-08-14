# 网页版 DeepSeek 桥接指南

## 安装

1. 启动后端：`python backend/main.py`（默认 http://localhost:8088）
2. 浏览器安装 Tampermonkey 扩展
3. Tampermonkey 面板 → 新建脚本 → 粘贴 `bridge/ds-bridge.user.js` 内容（或导入该文件）
4. 打开 https://chat.deepseek.com

## 使用

脚本加载后自动：
1. 健康检查本地后端
2. 拉取动态 role_card + 工具清单
3. 注入 role_card（作为首条消息发送）
4. 监听模型回复，检测 `start:{...}end` 工具调用指令
5. 执行工具 → 结果回填输入框 → 让模型继续

你只需正常对话，模型需要工具时会自动输出工具指令，脚本自动执行并回填结果。

## 工具调用协议

模型被 role_card 约束，需要调用工具时输出：

```
start:{"name":"write_file","arguments":{"path":"out.txt","content":"内容"}}end
```

可多行并列多个调用。脚本解析后批量执行。

## 配置

脚本配置存于 Tampermonkey（GM_setValue）：
- `backendUrl`：本地后端地址（默认 http://localhost:8088）
- `autoInject`：是否自动注入 role_card（默认 true）

## 新增 web 端适配器

1. 复制 `bridge/adapters/_template.js` 为 `adapters/你的站点.js`
2. 实现契约 D 的 6 个方法（`match`、`injectRolecard`、`onModelReply`、`sendMessage`、`setStatus`、`getConfig/setConfig`）
3. 在 `build.py` 里把新 adapter 合并进 user.js
4. 运行 `python bridge/build.py` 重新生成

核心引擎 `bridge/core.js` 与站点无关，新增站点无需改动引擎。

## 常见问题

- **提示「本地后端未启动」**：确认 `python backend/main.py` 在运行，且端口 8088 未被占用。
- **工具调用没反应**：检查浏览器控制台；确认 DeepSeek 页面 DOM 结构变化时更新 adapter 选择器。
- **CORS/跨域**：脚本用 GM_xmlhttpRequest 绕过 CORS，需在 Tampermonkey 允许 localhost 连接。
