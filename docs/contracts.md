# 接口契约（解耦的关键）

三部分只依赖契约、不依赖彼此实现。

## 契约 A —— 后端 ⇄ 桥接层（web 端接入）

```
GET  /api/bridge/rolecard     → 动态 role_card（工具清单 + start:{...}end 输出格式）
GET  /api/bridge/tools        → 工具清单（JSON-schema）
POST /api/bridge/call         → 单/多工具调用（简化 JSON-RPC）
POST /api/bridge/session      → 会话开始（可选，审计）
GET  /api/health              → 健康检查
```

请求示例（单工具）：
```json
POST /api/bridge/call
{"tool": "write_file", "arguments": {"path": "out.txt", "content": "hi"}}
```

批量：
```json
{"calls": [{"name": "write_file", "arguments": {...}}, {"name": "read_file", "arguments": {...}}]}
```

## 契约 B —— 后端 ⇄ 管理控制台

```
/api/mcp           GET/POST         列出/新增 MCP 服务器
/api/mcp/{name}    GET/PUT/DELETE   查/改/删
/api/mcp/{name}/start|stop|restart
/api/mcp/{name}/tools
/api/skills        GET/POST、/api/skills/{name} GET/PUT/DELETE、/api/skills/{name}/execute
/api/admin/status|logs|metrics
/api/tools（= /api/bridge/tools）
```

## 契约 C —— 后端 ⇄ 本地端（标准 MCP server）

后端以标准 MCP server（stdio）暴露聚合工具。Hermes / Claude Desktop 直接接入：

```bash
hermes mcp add v3 --command "python backend/mcp_services/server.py"
```

## 契约 D —— 桥接引擎 ⇄ 站点适配器

```js
{
  match: /chat\.deepseek\.com/,   // 域名匹配
  injectRolecard(text),           // 注入系统提示词
  onModelReply(handler),          // 监听模型回复流
  sendMessage(text),              // 回填结果并发送
  setStatus(text),                // 进度/错误提示
  getConfig()/setConfig(cfg),     // 站点级配置
}
```

新增 web 端：照 `bridge/adapters/_template.js` 实现上述 6 方法即可。
