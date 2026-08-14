# 接入第三方 MCP 服务器

后端用官方 `mcp` SDK 接入任意标准 MCP 服务器，支持三种传输：**stdio**（本地进程）、**SSE**、**Streamable HTTP**。

## 方式一：管理控制台

打开 http://localhost:8088/console → 「MCP 服务」→ 「添加」，填表单即可。

## 方式二：直接改配置

编辑 `backend/config/mcp.json`：

```json
{
  "services": {
    "filesystem": {
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users/YourName/Documents"],
      "auto_start": true,
      "description": "本地文件系统"
    },
    "fetch": {
      "transport": "stdio",
      "command": "uvx",
      "args": ["mcp-server-fetch"],
      "auto_start": true
    },
    "remote_sse": {
      "transport": "sse",
      "url": "http://remote:8000/sse",
      "auto_start": false
    }
  },
  "server": { "host": "0.0.0.0", "port": 8088 }
}
```

改完重启后端，或经 API `POST /api/mcp` 热添加（无需重启）。

## 方式三：API 热添加

```bash
curl -X POST http://localhost:8088/api/mcp \
  -H "Content-Type: application/json" \
  -d '{"name":"fetch","transport":"stdio","command":"uvx","args":["mcp-server-fetch"],"auto_start":true}'
```

## 常用第三方 MCP

| 服务器 | command/args |
|---|---|
| filesystem | `npx -y @modelcontextprotocol/server-filesystem <目录>` |
| github | `npx -y @modelcontextprotocol/server-github`（需 GITHUB_TOKEN） |
| fetch | `uvx mcp-server-fetch` |
| memory | `npx -y @modelcontextprotocol/server-memory` |
| puppeteer | `npx -y @modelcontextprotocol/server-puppeteer` |

## 工具命名与调用

接入后工具以 `mcp.<服务名>.<工具名>` 命名。例如 filesystem 的 `read_file` → `mcp.filesystem.read_file`。

- web 端：新工具会自动出现在动态 role_card 里，下次对话即可调用。
- 本地端：自动出现在聚合 MCP server 的 list_tools 里。

## 生命周期与健康

- 服务启动失败会标记 `failed`，可经 API 重试/重启。
- 运行中掉线会自动重启（健康监控，间隔见 settings.yaml `health.check_interval`）。
