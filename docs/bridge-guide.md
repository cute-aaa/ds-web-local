# 网页版聊天桥接指南

## 安装

1. 启动后端：`python backend/main.py`（默认 http://localhost:8088）
2. 浏览器安装 Tampermonkey 扩展
3. Tampermonkey 面板 → 新建脚本 → 粘贴 `bridge/ds-bridge.user.js` 内容（或导入该文件）
4. 打开任一受支持的聊天站点（见下表）

## 支持的站点

| 站点 | URL | 适配器 | 发送方式 |
| --- | --- | --- | --- |
| DeepSeek | https://chat.deepseek.com | `adapters/deepseek.js` | 键盘（Ctrl+Enter → Enter） |
| 小米 MiMo Ultra | https://ultraspeed.xiaomimimo.com | `adapters/mimo.js` | 键盘（Enter） |

脚本在页面加载时自动挑选适配器：取 `window.BridgeAdapters` 中首个 `match` 命中当前 URL 的适配器；未命中则静默退出。

## 使用

脚本加载后自动：
1. 健康检查本地后端
2. 拉取动态 role_card + 工具清单
3. 注入 role_card（作为首条消息发送）
4. 监听模型回复，检测行首 `@@tool` 的工具调用行
5. 执行工具 → 结果以「工具调用结果」消息回填输入框（每次调用必有返回，含 null/空/错误）→ 让模型继续

你只需正常对话，模型需要工具时会自动输出工具指令，脚本自动执行并回填结果。

## 工具调用协议（函数调用模式）

模型被 role_card 约束，需要调用工具时，**每条调用独占一行、行首为 `@@tool`**：

```
@@tool write_file {"path":"out.txt","content":"内容"}
```

**没有终止符**（不要写 end／分号），参数写成单行 JSON。
旧协议 `start:{...}end` 已废弃——它依赖终止符，模型漏写 `end` 会导致整条调用静默失效。

脚本把每个调用打包成一次请求，后端返回**逐调用信封**，脚本再把信封渲染成
「工具调用结果」消息回填（含 request_id + 原始命令回显）：

- 每次调用都**必有返回**：返回 null / 无内容 = 调用成功、可继续；失败会给出 error 类型与信息。
- 一批多个调用按序逐条返回，模型可用 request_id / 原始命令区分每一次。
- 调用前若输入框残留上一轮未发送的结果会自动**清空重置**；连续多批结果合并成一条消息，
  不会逐批覆盖、也不会无限拼接污染上下文。
- **完整内容在附件文件**：每次调用的结果都落盘为
  `backend/data/attachments/<caller>/<日期>/result_<request_id>.json`
  （一次调用一个文件，内含本次命令、结果、状态、耗时）。
  结果较大时只回填预览 + 附件路径，并提示用户**在网页输入框点「附件/📎」上传该文件**获取全文
  （附件上传不受输入框长度限制，避免超长回填被站点截断）；`read_file` 仅作为用户无法上传时的兜底。
  阈值由后端配置 `bridge.inline_max_chars` / `bridge.summary_max_chars` 控制（默认 20000/8000）：
  调小更省上下文，但模型要多一次 `read_file` 往返。
- **调用流水**：同目录下另有 `calls_<日期>.jsonl`，按时间追加、一行一个 `request_id`，
  命令与结果同在——便于按时间顺序审计与回放「哪条命令得到了什么结果」。
- **减少重复调用（两级）**：前端只读结果缓存（幂等工具同参数 20s 内复用，按**单个调用**粒度）
  + 后端 30s 幂等批去重（整批相同）。写入/终端等有副作用调用**不会被**静默去重，
  失败结果也不进缓存（错误必须真实重试）。
- 大结果是否自动拉取全量内联由配置 `autoEnrichExternalized` 控制（默认关）。

后端侧本协议的完整设计见 `docs/contracts.md` 与 `docs/architecture.md`。

## 配置

脚本配置存于 Tampermonkey（GM_setValue）：
- `backendUrl`：本地后端地址（默认 http://localhost:8088）
- `autoInject`：是否自动注入 role_card（默认 true）
- `autoSend`：工具结果就绪后自动发送（默认 false）
- `autoEnrichExternalized`：大结果自动拉取附件全量内联（默认 false）
- `autoRepair`：模型输出格式不符时自动生成纠错提示填入输入框（默认 true）
- `callerId`：当前浏览器 profile 的调用方身份（首启自动生成并持久化，用于多 profile 隔离与审计；
  同一浏览器 profile 内所有页面共享，切换账号/开新窗口不影响）

**状态圆点颜色**即工具调用状态：🟢 已连接 / 🔵 执行中（呼吸动画）/ 🔴 调用失败 /
🟠 需人工审批或格式待修正 / ⚪ 未知。悬停可看文字说明。

右上角状态圆点可打开控制面板，手动初始化 / 重载 MCP / 刷新技能 / 修改配置（面板含
「自动内联外置大结果」「自动纠错格式偏差」开关），另有两个排查/调试区：

- **📋 调用追踪**：模型回复 / 解析 / 后端调用 / 回填各环节流水（1.5s 刷新，可复制、可清空），
  排查「没反应」的第一现场。
- **⚡ 手动执行**：直接输入 `@@tool read_file {"path":"C:/a.txt"}` 执行，结果即时显示在面板，
  可选「执行并回填」写入输入框。用于调试工具，或模型输出格式失败时的人工兜底。
  **命令自动保存**：脚本解析出的最后一条命令（含解析失败/格式偏差时的原文）会自动填入该输入框，
  并持久化到 Tampermonkey 存储——刷新页面后仍在。框内提示会显示命令来源、条数与执行结果
  （执行成功 / 执行失败 / 未识别）；用户手动改动后新命令不再覆盖其编辑内容，可用
  「↩ 填入最后一条」一键还原。因此自动执行失败时，打开面板改参数后点「▶ 执行」即可，无需重敲。

多账号场景：每个浏览器 profile（或不同浏览器用户配置）各自安装一份脚本即可——后端按
`callerId` 隔离附件、工具日志与 ask_user 确认请求，互不串扰。
同一 profile 开多个标签页时再按 `tabId` 隔离：每个标签只看到自己会话发起的确认请求，
跨标签应答会被拒绝（403），不会弹错会话的确认框。

## 新增 web 端适配器

> **首选：用实测探测器自动生成**，不要手写猜测选择器。
>
> ```bash
> npm i -D playwright-core
> node scripts/probe_site.mjs --list                  # 查看已登记站点
> node scripts/probe_site.mjs --site kimi             # 打开站点，登录后回车，自动探测
> node scripts/probe_site.mjs --site kimi --send      # 额外自动发一条消息以捕获聊天接口
> node scripts/probe_site.mjs --sites kimi,qwen,doubao
> ```
>
> 浏览器与登录态默认保存在仓库内 `.chrome-test/shared-profile`（首次使用需在该浏览器里登录站点）；
> 也可用 `CHROME_TEST_DIR` / `CHROME_PROFILE` 指向已有的 Chrome 用户数据目录。探测结果写入
> `scripts/probe-out/<site>.json`，并**直接生成** `bridge/adapters/<site>.js`（选择器均为实测值）。
> 生成后仍需人工复核，再在 `build.py` 登记。

手工方式（探测器不可用时的兜底）：

1. 复制 `bridge/adapters/_template.js` 为 `adapters/<站点>.js`
2. 填写**站点差异**声明（选择器建议用 F12 或 CDP 实测，勿凭空猜测）：
   `INPUT_SELECTORS` / `SEND_SELECTORS` / `MESSAGE_SELECTORS` / `API_URL_PATTERN` /
   `ROLECARD_MARKER` / `SEND_STRATEGY` / `SEND_KEY_SEQUENCE` / `MAX_FILL`
3. 文件末尾调用 `BridgeUI.install(YourAdapter)` 混入通用能力
4. 在 `build.py` 的 `ADAPTERS` 列表登记（文件名 + 全局名），并在 `HEADER` 追加 `@match`
5. 运行 `python bridge/build.py` 重新生成

分层约定：

- `core.js` → `BridgeCore`：站点无关**引擎**（请求、解析、调用、格式化、落盘、配置持久化）
- `core.js` → `BridgeUI`：站点无关**呈现与注入**（状态圆点、配置面板、toast、
  React 受控输入框注入、键盘/点击发送、截断回填、三通道回复监听）
- `adapters/*.js`：只声明**站点差异**；若某通用方法与站点结构不符，
  在 adapter 上定义同名方法即可覆盖（`BridgeUI.install()` 只填补不存在的成员）
- `build.py` 的 `MAIN`：注册表匹配 + 编排（防抖合并、批次去重、外置结果补全）

## 自定义 role_card（已 md 化，改文件即可）

提示词模板不再硬编码在 Python/JS 里，而是外部化为 Markdown：

| 文件 | 作用 |
| --- | --- |
| `backend/rolecard/template.md` | 内置默认模板（随代码发布，**不要直接改**） |
| `backend/config/rolecard.md` | 用户副本，**改这个**；首次运行自动生成、不入库（仓库只保留 template） |

- **改完即生效**：按文件 mtime 热重载，无需重启后端、无需改 JS、无需重新打包油猴脚本。
- **占位符**：`<!--TOOLS-->` 替换为可用工具清单，`<!--SKILLS-->` 替换为技能说明，**不要删除**。
- **花括号无需转义**：采用显式占位符替换（旧实现用 `str.format`，模板里每个 `{` 都得写成 `{{`，
  漏写就会运行时崩溃）。现在可以直接写 `{"path":"..."}`。
- 若用户模板缺失，后端会**自动从内置模板重新生成一份**，不会因误删导致桥接失效。
- 路径可用 `settings.yaml` 的 `rolecard.template` 覆盖（相对路径基于 config 目录）。

## 工具结果附件（.md + .json 双视图）

每次调用除原有的 `result_<request_id>.json` 外，再落盘一份同名 `.md`：

- `.json` — 程序解析用（脚本取结构化字段）
- `.md` — **模型阅读 / 人工查看用**：含状态、耗时、原始调用（可直接复制重试）、结果与错误，
  层次清晰、无需脑内解析转义

两者都在 `backend/data/attachments/<caller>/<日期>/` 下，保留策略（7 天 / 每 caller 500 个）
对两种格式同时生效。大结果外置时，`.md` 里是**完整内容**（在截断前落盘），消息里只回预览，
并提示用户上传该附件以取全文（见上文「完整内容在附件文件」）。

## 常见问题

- **提示「本地后端未启动」**：确认 `python backend/main.py` 在运行，且端口 8088 未被占用。
- **工具调用没反应**：检查浏览器控制台；确认页面 DOM 结构变化时更新 adapter 选择器。
- **CORS/跨域**：脚本用 GM_xmlhttpRequest 绕过 CORS，需在 Tampermonkey 允许 localhost 连接。
  后端已开放 `allow_origins=["*"]`，即便回退到原生 `fetch` 也可用。
- **多 profile / 多账号连同一后端**：脚本自动携带按浏览器 profile 生成的 `callerId`。
  ask_user 确认框、工具日志、结果附件都按调用方隔离——A 账号看不到也代答不了 B 账号的
  挂起确认（后端返回 403）；A 也读不了 B 的附件。若要彻底隔离请用不同浏览器用户配置。
- **附件文件会一直增加吗**：后端默认保留 7 天、每个调用方最多 500 个文件，启动时与每 24h
  自动清理（见 `settings.yaml` 的 `bridge.attachment_*`），也可手动删 `backend/data/attachments/`。
- **想让大结果一次内联、别让模型多读一次文件**：配置面板勾选「自动内联外置大结果」
  （或设 GM `autoEnrichExternalized=true`），代价是回填体积更大、更易触发站点限流。

### 小米 MiMo 专属注意事项

- **`API_URL_PATTERN` 必须精确到 `/open-apis/bot/chat`**。
  站点还有 `heartbeat` / `metrics` / `genTitle` 等高频轮询接口，
  若一并匹配会导致每次心跳都触发解析并打后端（还会撞后端 60/min 限流）。
- **回复走 fetch + SSE**（XHR 与 EventSource 均未被使用），载荷形如
  `data: {"v":{...}}` 的增量补丁；该通道先经 `decodeReply()` 把 SSE 还原成模型输出原文
  （行锚定协议要求 `@@tool` 处于行首），再由 `parseToolCalls` 按行解析；
  JSON 引号被转义等情况由四级降级链处理。
- **hash 路由 SPA**：URL 为 `#/`，切换会话不会触发脚本重载。
  脚本已监听 `hashchange` 并在 2s 后重新注入 role_card（内含去重检查，幂等）。
- **UltraSpeed 有额度限制**：页面显示「本次体验剩余时间」，且存在「对话已到上限」，
  而 role_card 约 1.2 万字符，会占用可观上下文 —— 长任务建议新开对话。
- **站点自身限流**：若模型回复「请求过于频繁，请稍后重试」，这是 **MiMo 侧**限流，
  与本地后端无关（后端限流的表现是 `/api/*` 返回 429 JSON）。等待片刻或新建对话即可。

### 连不上本地后端（请求一直挂起，既不成功也不报错）

常见原因是**系统代理把 loopback 请求也劫持走了**。

实测：系统代理指向 `127.0.0.1:7897` 时，页面内 `fetch('http://127.0.0.1:8088/health')`
与 `fetch('http://localhost:8088/health')` 双双超时挂起（网络面板只有 `requestWillBeSent`，
既无响应也无失败事件），关闭代理后立即恢复正常。

排查与解决：

1. `node` 环境下在页面控制台跑一次 `fetch('http://127.0.0.1:8088/health')`，
   超时即说明请求被代理吞掉（而非后端没起来）。
2. 在代理工具里放行 / 直连 `localhost` 与 `127.0.0.1`，或临时关闭代理。
   （Windows 代理旁路列表位于
   `HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings` 的 `ProxyOverride`，
   但部分代理工具会绕过它直接接管流量。）
