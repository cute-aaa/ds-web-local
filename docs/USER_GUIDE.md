# 用户指南（非开发者版）

本文面向不想看代码、只想让网页版 DeepSeek 拥有本地能力的用户。

## 你需要准备什么

| 东西 | 说明 |
|---|---|
| Windows 电脑 | 本项目主要面向 Windows（桌面自动化功能） |
| Python 3.11+ | 后端运行环境，[点此下载](https://www.python.org/downloads/)（安装时勾选 Add to PATH） |
| 浏览器 + Tampermonkey | Chrome / Edge 都行，[安装 Tampermonkey](https://www.tampermonkey.net/) |
| DeepSeek 账号 | chat.deepseek.com 登录可用 |

## 一步步安装

### 第 1 步：安装后端

1. 解压项目到任意目录（比如 `D:\ds-web-local`）
2. 打开命令行（Win+R 输入 `cmd` 回车），执行：

```bat
cd D:\ds-web-local\backend
pip install -r requirements.txt
```

### 第 2 步：启动后端

双击项目根目录的 `start.bat`（或命令行执行 `python backend/main.py`）。

看到 `Uvicorn running on http://127.0.0.1:8088` 就成功了。**这个窗口保持开着**。

验证：浏览器打开 http://127.0.0.1:8088/health 显示 `{"status":"ok"}` 即正常。

### 第 3 步：安装桥接脚本

1. 用记事本打开 `bridge\ds-bridge.user.js`，**全选复制**
2. 浏览器点 Tampermonkey 图标 → 管理面板 → 「+」新建脚本
3. **删除默认内容**，粘贴复制的代码 → Ctrl+S 保存
4. 打开 https://chat.deepseek.com

### 第 4 步：开始使用

- 页面右上角出现**状态圆点**（灰→绿 = 连接成功）
- 点「**新建对话**」——系统规范会自动注入（对话里第一条消息是「# 系统规范…」）
- 直接像聊天一样提需求，例如：
  - 「查看 D:/ 目录下有什么」
  - 「读取 G:/Download/xxx.txt 的内容」
  - 「列出当前运行的进程」
  - 「启动 D:/games/xxx.exe」

## 常见问题

**Q: 右上角圆点一直是灰色/红色？**
A: 后端没启动或端口不对。确认 start.bat 窗口在运行；点圆点打开面板，检查后端地址是不是 http://localhost:8088。

**Q: 模型调用工具后，结果出现在输入框？**
A: 这是正常设计——**按一下发送键**把结果提交给模型，模型才能继续回答（自动发送模式可在面板开关，但可能触发 DeepSeek 风控）。

**Q: 模型不调用工具，直接文字回答？**
A: 刷新页面后**新建对话**（规范只注入到新会话）；再明确说一次需求。

**Q: 电脑重启后怎么恢复？**
A: 双击 `start.bat` 启动后端 → 打开 chat.deepseek.com 刷新 → 新建对话。

**Q: 需要管理员权限吗？**
A: 一般不需要。只有启动某些程序或操作系统目录时可能需要（工具会返回错误，可改用其他方式）。
