# 部署

## 本地

```bash
# 后端
cd backend
pip install -r requirements.txt
python main.py

# 前端（开发模式，可选）
cd console
npm install
npm run dev        # http://localhost:5173（自动代理 /api → 8088）

# 前端（生产构建，由后端托管）
cd console
npm run build      # 产物 console/dist，经 http://localhost:8088/console 访问
```

一键脚本：`build_and_start.bat`（装依赖 + 构建前端 + 启动后端）。

## Docker

```bash
cd docker
docker compose up --build
```

- 后端 + 前端静态产物在单容器，映射 8088
- 卷挂载 `backend/config`（MCP/技能配置）、`backend/data`（会话/任务）、`backend/logs`

## 环境变量 / 配置

- `backend/config/settings.yaml`：日志、并发、健康、超时、认证、技能目录、监听线程
- `backend/config/mcp.json`：MCP 服务器
- `backend/config/skills.json`：legacy 技能（新技能走 SKILL.md 目录）
- `.env.example`：可选（LLM 双模式、认证 token）

## 配置/数据外部化（多机共用）

配置与数据目录默认在 `backend/` 内，可用环境变量指向任意位置（网盘/共享盘/Git 仓库），
实现多台电脑共用同一套配置、技能与数据：

```bash
# Windows（setx 永久生效）
setx DSW_CONFIG_DIR "G:\MyShare\ds-web-local\config"   # mcp.json / settings.yaml
setx DSW_DATA_DIR   "G:\MyShare\ds-web-local\data"     # user-skills / sessions.db / .credentials.yaml / approvals.log

# 启动（读环境变量）
python backend\main.py
```

- 配置目录缺失时自动创建空配置（等价默认）。
- 技能目录 `skills.skills_dirs` 支持**绝对路径**任意位置（source 标记为 `custom`），
  例如网盘上的 SKILL.md 技能库：`skills_dirs: ["skills", "data/user-skills", "G:/MyShare/skills-lib"]`。
- 注意：`.credentials.yaml` 含明文凭据，共享目录需保证访问权限（建议仅本机可读或加密盘）。
- Docker 场景：挂载卷直接指向外部目录即可。

## 监听线程（watcher）

后端启动后自动运行两个本地监听线程（`settings.yaml → watcher` 可调间隔，0=关闭）：

```yaml
watcher:
  config_interval: 5   # mcp.json/settings.yaml 变更 → MCP 服务热重载（无需重启）
  skills_interval: 5   # 技能目录新增/删除/修改 → catalog 与 role_card 自动刷新
```

在多机共用场景下，另一台电脑改配置/技能后，本机 5 秒内自动生效。

## 认证（局域网/多端接入时）

```yaml
# settings.yaml
security:
  auth_enabled: true
  auth_token: "你的token"
```

之后请求带 `Authorization: Bearer 你的token`（/health、/docs 豁免）。

## 升级说明

- v1 → v3：v3 用标准 MCP 协议替代自研行协议，role_card 改为动态生成。
- 迁移：原 v1 的 `mcp.json` 服务需改为标准 MCP 服务器（stdio command/args），或直接接第三方标准 MCP。
