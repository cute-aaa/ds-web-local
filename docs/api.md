# API 参考

完整 OpenAPI 文档见运行后的 http://localhost:8088/docs。以下为端点清单。

## 桥接（契约 A）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/bridge/rolecard | 动态 role_card |
| GET | /api/bridge/tools | 工具清单（含 input_schema） |
| POST | /api/bridge/call | 单/多工具调用 |
| POST | /api/bridge/session | 会话开始 |

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
