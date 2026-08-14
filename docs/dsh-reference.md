# DeepSeek Harness (dsh) 调研参考笔记

> 调研日期：2026-08-14 ｜ 源码：`G:\Projnew\git\deepseek-harness` @ `47f943859b`（0.1.0-rc.5，MIT）
> 本文是 v3 计划 §〇「dsh 设计吸收」的完整出处记录。v3 技术栈为 Python/FastAPI，dsh 为 Node/TS + Cordis，**只吸收设计模式，不移植代码**。

## 1. 定位

DeepSeek 官方开源的 agent harness（智能体框架）。开发者预览阶段，快速迭代中，将出现破坏性变更。
运行：`npx @deepseek-ai/dsh web` → Web UI 默认 http://127.0.0.1:3080。

核心哲学（Cordis 驱动）：
- **一切皆插件**：产品每一部分（模型适配器、工具注册表、会话日志、agent loop 本身）都是插件，可配置替换
- 无特权内核：扩展 = 把插件挂载到其他插件旁，注册是副作用，卸载时撤销
- 事件即扩展点：会话事件（持久事实）/ agent 事件（`agent/*`，携带活跃 Agent）/ 能力事件（向 seam 附加策略）

## 2. 架构要点（docs/architecture.md）

- **轮次流程**：step = 一次模型请求 + 它调用的工具；turn = 零或多个 step
  `turn/start → agent/pre-step → step/start → agent/request → llm/stream → tool/call* → tools/pre-execute → tools/execute → tools/post-execute → tool/result* → step/end → agent/turn-stopping → turn/end`
- **会话日志**：仅追加 `SessionEvent`；"模型可见即已记录"；历史/fork/transcript/遥测都从事件流派生
- **能力 seam**：Service Definition + Service Provider + Consumer 三位一体；换 provider 即换整个产品行为（如把 fs/进程 provider 指向远程沙箱，Bash/PTY/LSP 一起搬过去）
- **Profile/组合包**：启动时按序叠加配置层（profile 列出的 bundle → profile patch → home patch → --patch overlay）

## 3. MCP 客户端（packages/mcp/mcp-client）→ v3 R1-R5

- 每个 MCP 服务器一个插件实例；配置：transport(stdio|streamable-http)、serverName、command/args/env/cwd、url/headers、toolCallTimeoutMs(默认 60000)、failOnStartupError(默认 false)、reconnect.{enabled,initialDelayMs(500),maxDelayMs(30000),maxAttempts(10)}
- **工具命名**：公开名 `mcp__<serverName>__<rawName>`，与 Claude Code/Codex 相同形状；规范化为 64 字符 `[A-Za-z0-9_-]`；替换/截断改变名称时追加 `(serverName, rawName)` 的确定性 12 位十六进制 hash；名称是纯函数（连接顺序/重连不重命名）
- **世代机制**：连接时等 listTools() → 注册整代；`notifications/tools/list_changed` → 整代重同步；同步失败回滚整代（绝不部分残留）；重连成功新世代整代替换旧世代
- **重连**：supervisor 指数退避重启原始配置 → 成功后重新发现；中断期间最后一个正常世代保持注册（调用在恢复前失败）；连续失败达 maxAttempts → 注销工具 + 停止重连；连接存活超 maxDelayMs 重置预算（偶崩无限恢复，崩溃循环不无限重启）；状态可见：reconnecting(warn+尝试次数)/recovered(info)/最终失败/disabled-loss(error)
- **HMR 热重载**：编辑配置触发断开+重连；serverName 不变则工具名不变
- 结果：规范值 `{content: JsonValue[], structuredContent?}`；文本块换行连接，图片/音频/资源变占位符

## 4. 技能子系统（packages/skill）→ v3 R6-R7

- **Provider 三件套**：`SkillProvider{name, list(options), get(candidate, options)}` + 注册作用域分层（host+per-scope）+ `skills/change` 失效事件
- **发现优先级**（本地 provider）：project-dsh `<root>/.dsh/skills`(100) → project-agents `<root>/.agents/skills`(200) → custom `Config.customSkillDirs`(300) → user-dsh `<dshHome>/skills`(400) → user-agents `<agentsHome>/skills`(500) → bundled `Config.bundledSkillDir`(600)
- **格式**：kebab-case 名称 `^[a-z0-9]+(-[a-z0-9]+)*$`；目录包 `<name>/SKILL.md` + 扁平文件 `<name>.md`（不支持嵌套递归发现）
- **调用控制**：frontmatter `disable-model-invocation` / `user-invocable`（缺失默认 true）；SkillInvocationPolicy{modelInvocable, userInvocable}
- **模型契约**：catalog 只含 name+description（XML 转义、catalogDescriptionMaxLength 默认 500）注入 system prompt；`skill({name})` 工具校验 kebab-case → catalog 查 modelInvocable → 按需加载完整正文；body 变更不影响已注入 catalog
- **快照**：SkillCatalogSnapshot{skills, complete}；incomplete 不缓存（保留 last-good 重试）；digest 比对 → agent.inject() 全量替换

## 5. 凭据（packages/credentials）→ v3 R8

- 一条准则：**配置只携带对机密的引用，绝不携带机密本身**（`apiKeyEnv: GITHUB_TOKEN`）；值存凭据提供方（`.credentials.yaml` + 进程环境变量叠加）
- `resolve(ref)` 按操作解析（LLM 适配器每次模型请求解析一次），**绝不跨操作缓存** → 改凭据立即生效，无需重启
- `describe(ref)` → {configured, source, writable}，永不返回值 → 配置界面渲染"已配置"徽标
- `set/unset` + 遮蔽规则：只读来源（环境变量）遮蔽时写操作显式拒绝，界面提前渲染为只读
- `credentials/updated(ref)` 事件 → 界面刷新徽标；空存储值 = 不存在

## 6. 工具集参考（docs/tool-catalog.md）→ v3 R10

- fs 搜索：`glob`/`grep` 用打包的 `@vscode/ripgrep` 二进制（不依赖系统 rg、不走 shell 层）
- `web_search`/`web_fetch`：provider 可换，模型可见 schema 稳定
- `ask_user_question`：暂停工具调用直到 UI provider 返回人类回答
- jobs：`job_list/job_output/job_kill` 统一后台任务控制（bash 后台/PTY/subagent 共用）
- todo_write：会话所有状态，UI 渲染为 checklist
- 其他：subagent(委派/fork)、lsp、goal、schedule、session_query(5 个只读工具)、plan-mode、cordis_*（动态插件，opt-in）

## 7. Web UI（docs/user/guide）→ v3 控制台参考

- Settings → Models 输入 API key 即存即用，**无需重启**
- workspace 选择制；会话 composer 依赖 workspace
- 权限策略：需要人类批准的操作由 UI 提问确认
- Python SDK 提供编程接入

## 8. 对 v3 的决策影响（映射摘要）

| dsh 设计 | v3 落地 |
|---|---|
| mcp__server__tool 命名 | manager.py normalize_tool_name + bridge 分发 |
| 世代 + 退避重连 + 预算 | manager.py 重连状态机 |
| 配置热重载 | api/mcp.py PUT → manager.reload |
| SKILL.md + 多根发现 | skills/discovery.py + role_card 注入 + skill 工具 |
| 凭据引用模型 | credentials/store.py + resolver + env 引用 |
| pre/post 流水线 | registry 钩子 + approvals 队列 |
| 打包 rg / web 工具 / ask_user / jobs | tools/ 新增模块 |
| 仅追加会话事件 | db/session_store.py 事件表 |
