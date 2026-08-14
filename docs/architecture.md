# 架构设计

## 总览

三部分通过接口契约解耦，各自可独立替换/扩展：

```
 网页端(DeepSeek/ChatGPT/…)          本地端(Hermes/Claude Desktop/自建 agent)
        │  [契约A: 桥接 API]                  │  [契约C: 标准 MCP server]
        ▼                                     ▼
   bridge/ 桥接层 ──[契约A]──►  backend/ 本地后端 ◄──[契约B]──  console/ 管理控制台
                                (MCP client 接入外部 MCP 服务器)
```

## 后端（backend/）

MCP 双角色工具聚合中心：

- **MCP 客户端**（`mcp_services/client.py` + `manager.py`）：用官方 `mcp` SDK 接入外部标准 MCP 服务器（stdio/SSE/Streamable HTTP），维护生命周期与健康监控。
- **MCP 服务器**（`mcp_services/server.py`）：把「内置工具 + 已接入 MCP 工具」聚合为标准 MCP server，供本地 agent 接入。
- **统一工具注册表**（`tools/registry.py`）：内置工具统一注册与分派。
- **内置工具**（`tools/`）：文件操作、搜索、Git、终端、任务管理。
- **技能引擎**（`skills/`）：占位符解析 + 工具流水线 + 输出模板。
- **动态 role_card**（`rolecard/generator.py`）：从注册表实时生成工具清单，供 web 端「脑」消费。

分层：`core`（配置/日志/安全/指标/错误）→ `mcp_services`/`tools`/`skills`/`rolecard`（能力层）→ `api`（接口层）→ `main.py`（装配）。

## 桥接层（bridge/）

- `core.js`：站点无关引擎（拉 role_card、解析 `start:{...}end`、调用后端、回填结果、状态提示）。
- `adapters/`：站点适配器（每个 web 端一个，实现契约 D 的 6 个方法）。
- `build.py`：把 core + 适配器合并为单文件 `ds-bridge.user.js`。

## 管理控制台（console/）

React + TS + Vite + Tailwind SPA，只依赖后端 REST API（契约 B）。

## 关键设计决策

| 决策 | 理由 |
|---|---|
| 后端 MCP 双角色 | 一份工具能力，web 端与本地端都能复用 |
| 动态 role_card | 接入新 MCP 后 web 端自动可见新工具（无需改脚本） |
| 引擎 + 适配器分离 | 新增 web 端只写一个 adapter |
| SQLite + JSON 配置 | 零额外依赖；配置可手改、可 UI 编辑、支持热重载 |
