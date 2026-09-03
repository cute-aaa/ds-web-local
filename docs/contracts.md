# 接口契约（解耦的关键）

三部分只依赖契约、不依赖彼此实现。

## 契约 A —— 后端 ⇄ 桥接层（web 端接入）

```
GET  /api/bridge/rolecard      → 动态 role_card（工具清单 + 函数调用模式说明）
GET  /api/bridge/tools         → 工具清单（JSON-schema + idempotent 幂等元数据）
POST /api/bridge/call          → 单/多工具调用 → 逐调用全量信封（见下）
POST /api/bridge/session       → 会话开始（可选，审计）
GET  /api/bridge/tool_logs     → 最近工具调用日志（?caller_id= 可过滤）
GET  /api/bridge/attachments   → 按 caller+request_id 读附件全文（跨调用方 403）
GET  /api/bridge/files/{name}  → 【旧】data/tmp 外置文件（兼容期，已弃用）
GET  /api/bridge/ask_user/pending → 挂起人类确认（?caller_id= 隔离）
POST /api/bridge/ask_user      → 回传应答（带 caller_id 归属校验，跨 profile 403）
POST /api/bridge/log           → 保存对话历史（可带 caller_id）
GET  /api/health               → 健康检查
```

调用请求（body 可选带 `caller_id`/`tab_id`/`batch_id`，用于多 profile 归属与审计；缺省 `"anon"`）：
```json
POST /api/bridge/call
{"tool": "write_file", "arguments": {"path": "out.txt", "content": "hi"},
 "caller_id": "c_8f3a…", "tab_id": "t1", "batch_id": "b_1"}
```

批量：
```json
{"calls": [{"name": "write_file", "arguments": {...}}, {"name": "read_file", "arguments": {...}}]}
```

**响应 = 逐调用全量信封**（每个调用必有明确结果，禁止 500/无返回导致流程停滞）：
```json
{"batch_id": "b_1", "results": [{
  "request_id": "r_1a2b…", "tool": "write_file", "caller_id": "c_8f3a…",
  "arguments": {"path": "out.txt", "content": "hi"},        // 原始参数回显
  "status": "ok",                       // ok | error | approval_required
  "result": {"path": "out.txt"},        // null/空 = 成功无内容（正常完成，继续下一步）
  "error": null,                        // status=error 时 {type,message}
  "approval_required": false,
  "externalized": true, "file": "…/data/attachments/c_8f3a…/2026-09-03/result_r_1a2b….json",
  "truncated": true,                    // 大结果：result 为预览，完整内容在 file 附件
  "elapsed_ms": 12
}]}
```

- 语义性失败一律信封 `status:"error"`（HTTP 200），不再单工具 500 / 404。
- **每次调用默认落盘附件**（完整信封 JSON，含完整结果/错误详情），模型用 `read_file(file)` 读全量。
- 浏览器把信封渲染为「工具调用结果」消息回填（含 request_id + 原始命令回显），一次调用 = 一次必有返回。

> 本契约的详细设计、去重与多 profile 隔离规则见 `docs/tool-call-reliability-spec.md`。

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
