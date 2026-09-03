# API 参考

完整 OpenAPI 文档见运行后的 http://localhost:8088/docs。以下为端点清单。

## 桥接（契约 A）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/bridge/rolecard | 动态 role_card |
| GET | /api/bridge/tools | 工具清单（含 input_schema 与幂等元数据 idempotent） |
| POST | /api/bridge/call | 单/多工具调用 → **逐调用全量信封**（见下） |
| POST | /api/bridge/session | 会话开始 |
| GET | /api/bridge/tool_logs?limit=&caller_id= | 最近工具调用日志（可选按调用方过滤） |
| GET | /api/bridge/attachments?caller_id=&request_id= | 按归属读取附件文件全文（跨调用方 403） |
| GET | /api/bridge/files/{name} | 【旧】data/tmp 外置文件读取（兼容期，已弃用） |
| GET | /api/bridge/ask_user/pending?caller_id= | 挂起的人类确认请求（可选按调用方过滤） |
| POST | /api/bridge/ask_user | 回传应答（body 可带 caller_id，归属校验） |
| POST | /api/bridge/log | 保存对话历史（body 可带 caller_id） |

### /api/bridge/call —— 逐调用信封

响应不再裸返回结果/整批 500，而是给每个调用一个信封（请求 body 可选带 `caller_id`/`tab_id`/`batch_id`，旧脚本缺省用 `"anon"`）：

```json
{ "batch_id": "b_…", "results": [{
  "request_id": "r_1a2b…", "batch_id": "b_…", "tool": "read_file",
  "caller_id": "8f3a…", "arguments": {"path": "G:/a.txt"},   // 原始参数回显
  "status": "ok",                     // ok | error | approval_required
  "result": {…},                      // status=ok 时存在；null/空=成功无内容
  "error": null,                      // status=error 时 {type,message}
  "approval_required": false,
  "externalized": true, "file": "…/data/attachments/<caller>/<date>/result_<request_id>.json",
  "truncated": true,                  // 大结果：完整内容在 file 附件，result 为预览
  "elapsed_ms": 12
}]}
```

- 语义性失败（未知工具/参数错/异常）收敛为信封 `status:"error"`，HTTP 始终 200（body 非法除外）。
- **每次调用默认落盘附件**（完整信封 JSON，含完整 result/error），`data/attachments/<caller>/<date>/`，
  模型用 `read_file(file 路径)` 读全量；跨调用方读取经 `/attachments` 端点被拒（403）。
- 大结果（序列化 >20KB）只回填 `summary`（≤8000 字符）+ 附件路径，并置 `truncated:true`。
- 工具日志 `_tool_logs` 条目含 `caller_id`/`tab_id`/`request_id`，控制台可按调用方筛选。
- 附件清理策略见 `settings.yaml` 的 `bridge.attachment_retention_days`（默认 7 天）、
  `bridge.attachment_max_per_caller`（默认 500），启动时 + 每 24h 自动执行。

## MCP 管理（契约 B）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/mcp | 列出 MCP 服务器（含状态） |
| POST | /api/mcp | 新增（name/transport/command/args/url/env/auto_start） |
| GET | /api/mcp/{name} | 详情 |
| PUT | /api/mcp/{name} | 更新 |
| DELETE | /api/mcp/{name} | 删除 |
| POST | /api/mcp/{name}/start | 启动 |
| POST | /api/mcp/{name}/stop | 停止 |
| POST | /api/mcp/{name}/restart | 重启 |
| GET | /api/mcp/{name}/tools | 该服务的工具列表 |

## 技能管理
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/skills | 列出技能 |
| POST | /api/skills | 新建 |
| GET | /api/skills/{name} | 详情 |
| PUT | /api/skills/{name} | 更新 |
| DELETE | /api/skills/{name} | 删除 |
| POST | /api/skills/{name}/execute | 执行（body: {inputs:{...}}） |

## 会话 / 管理
| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | /api/sessions | 列出/新建会话 |
| DELETE | /api/sessions/{id} | 删除会话 |
| GET/POST | /api/sessions/{id}/messages | 消息历史/追加 |
| GET | /api/admin/status | 系统状态 |
| GET | /api/admin/logs?lines=100 | 日志尾部 |
| GET | /api/admin/metrics | Prometheus 文本指标 |
| GET | /health | 健康检查 |

## 内置工具

`write_file`、`read_file`、`search_replace`、`line_edit`、`list_directory`、`get_file_outline`、`grep_multi_search`、`git_status`、`git_diff`、`git_commit`、`create_terminal`、`run_in_terminal`、`read_terminal`、`list_terminals`、`delete_terminal`、`TodoWrite`、`read_todos`（共 17 个）。

MCP 工具命名规则：`mcp.<服务名>.<工具名>`，如 `mcp.filesystem.read_file`。

## 认证（可选）

在 `backend/config/settings.yaml` 设 `security.auth_enabled: true` 与 `security.auth_token`，之后请求需带 `Authorization: Bearer <token>`（/health、/docs 除外）。
