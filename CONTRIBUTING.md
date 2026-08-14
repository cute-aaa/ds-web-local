# 贡献指南

感谢你愿意为 DS Web Local 贡献代码！

## 环境准备

```bash
# 后端（Python 3.11+）
cd backend
pip install -r requirements.txt pytest pytest-asyncio

# 前端（Node 20+）
cd console
npm install
```

## 开发流程

1. Fork 仓库并克隆到本地
2. 创建功能分支：`git checkout -b feat/xxx`
3. 开发 + 测试（见下方验证命令）
4. 提交（遵循 Conventional Commits）并推送，发起 PR

## 验证命令（提交前必须全绿）

```bash
# 后端测试（151 用例）
cd backend && python -m pytest

# 桥接解析自测（14 用例，改 bridge/ 后必须跑）
cd bridge && node selftest.js

# 前端类型检查 + 构建
cd console && npx tsc --noEmit && npm run build

# 重新打包油猴脚本（改 bridge/ 源码后必须执行，产物随 commit 提交）
python bridge/build.py
```

## 代码规范

- 后端：Python 3.11，类型注解，函数 docstring（中文亦可），遵循现有模块划分（`tools/` 业务、`api/` 路由、`core/` 基础设施）
- 前端：React + TypeScript + Tailwind，页面放 `console/src/pages/`
- 桥接：`bridge/core.js`（引擎，站点无关）+ `bridge/adapters/`（站点适配器）；新站点 = 新 adapter + build.py 打包
- 注释与 UI 文案中文（与现有代码一致）

## 提交信息规范

```
feat: 新功能
fix: 修复
docs: 文档
chore: 构建/配置/杂项
test: 测试
```

## 安全注意事项

- 新增工具时遵循凭据引用模型（配置只存引用不存值）
- 工具返回结果注意脱敏（`settings.yaml` 的 `logging.sensitive_keys`）
- 默认只监听 127.0.0.1；不要默认开放局域网

## 发布流程（维护者）

```bash
git tag vX.Y.Z && git push origin vX.Y.Z
gh release create vX.Y.Z --title "..." bridge/ds-bridge.user.js
```

Release 附件建议包含：`ds-bridge.user.js`（油猴脚本）与 `console/dist/`（预构建控制台）。
