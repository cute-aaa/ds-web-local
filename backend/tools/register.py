"""注册所有内置工具到统一注册表。"""
from typing import Any

from tools.registry import get_registry
from tools import file_ops, search, git_ops, terminal_ops, todo, web_ops, jobs, ask_user


async def _skill_tool(name: str = "") -> Any:
    """skill 工具：不传 name 返回全部技能摘要（轻量查询）；传 name 按需加载正文。

    设计（dsh R7）：技能清单不占 role_card 上下文，模型需要时经此工具查询。
    """
    from skills.discovery import get_discovery, SKILL_NAME_RE
    discovery = get_discovery()
    if not name:
        # list 模式：返回可被模型调用的技能摘要（不返回正文，轻量）
        skills = []
        for s in discovery.list():
            if s.invocation.get("model", True):
                desc = s.description
                if len(desc) > 200:
                    desc = desc[:200] + "..."
                skills.append({"name": s.name, "description": desc, "source": s.source})
        return {"skills": skills, "count": len(skills)}
    if not SKILL_NAME_RE.match(name):
        return {"error": "技能名必须为 kebab-case（小写字母/数字，- 分隔，如 my-skill）"}
    defn = discovery.get(name)
    if not defn:
        return {"error": f"技能 '{name}' 不存在"}
    if not defn.invocation.get("model", True):
        return {"error": f"技能 '{name}' 不允许模型调用"}
    result = {"name": defn.name, "description": defn.description, "content": defn.content}
    if defn.steps:
        result["steps"] = defn.steps
    return result


def register_all_builtin_tools() -> None:
    reg = get_registry()

    # ---- 文件操作 ----
    reg.register_builtin(
        "search_replace", file_ops.search_replace,
        "搜索替换文件内容（count=-1 全量，1 首个）",
        {"type": "object", "properties": {
            "file_path": {"type": "string"}, "old_str": {"type": "string"},
            "new_str": {"type": "string"}, "count": {"type": "integer", "default": -1}},
         "required": ["file_path", "old_str", "new_str"]})
    reg.register_builtin(
        "read_file", file_ops.read_file, "读取文本文件（自动检测编码 UTF-8/GBK，二进制会提示；带行号分页）",
        {"type": "object", "properties": {
            "path": {"type": "string"}, "offset": {"type": "integer", "default": 1},
            "limit": {"type": "integer", "default": 2000}},
         "required": ["path"]})
    reg.register_builtin(
        "write_file", file_ops.write_file, "写入文件（覆盖）",
        {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
         "required": ["path", "content"]})
    reg.register_builtin(
        "list_directory", file_ops.list_directory, "列出目录内容",
        {"type": "object", "properties": {"path": {"type": "string", "default": "."}}})
    reg.register_builtin(
        "line_edit", file_ops.line_edit, "行编辑：SEARCH/REPLACE 块精确编辑",
        {"type": "object", "properties": {"file_path": {"type": "string"}, "edits": {"type": "string"}},
         "required": ["file_path", "edits"]})
    reg.register_builtin(
        "get_file_outline", file_ops.get_file_outline, "获取文件大纲（类/函数）",
        {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]})

    # ---- 搜索 ----
    reg.register_builtin(
        "grep_multi_search", search.grep_multi_search, "多线程全局搜索",
        {"type": "object", "properties": {
            "query": {"type": "string"}, "path": {"type": "string", "default": "."},
            "extensions": {"type": "array", "items": {"type": "string"}},
            "max_results": {"type": "integer", "default": 50}},
         "required": ["query"]})

    # ---- Git ----
    reg.register_builtin(
        "git_status", git_ops.git_status, "获取结构化 Git 状态",
        {"type": "object", "properties": {"working_dir": {"type": "string", "default": "."}}})
    reg.register_builtin(
        "git_diff", git_ops.git_diff, "查看代码差异",
        {"type": "object", "properties": {
            "file_path": {"type": "string"}, "cached": {"type": "boolean", "default": False},
            "working_dir": {"type": "string", "default": "."}}})
    reg.register_builtin(
        "git_commit", git_ops.git_commit, "提交更改",
        {"type": "object", "properties": {
            "message": {"type": "string"}, "add_all": {"type": "boolean", "default": False},
            "working_dir": {"type": "string", "default": "."}},
         "required": ["message"]})

    # ---- 终端 ----
    reg.register_builtin(
        "create_terminal", terminal_ops.create_terminal, "启动后台终端会话",
        {"type": "object", "properties": {"cwd": {"type": "string"}}})
    reg.register_builtin(
        "run_in_terminal", terminal_ops.terminal_run_command, "在终端执行命令并读取输出",
        {"type": "object", "properties": {
            "session_id": {"type": "string"}, "command": {"type": "string"},
            "wait": {"type": "number", "default": 1.0}},
         "required": ["session_id", "command"]})
    reg.register_builtin(
        "read_terminal", terminal_ops.terminal_read, "读取终端输出",
        {"type": "object", "properties": {"session_id": {"type": "string"}}, "required": ["session_id"]})
    reg.register_builtin(
        "list_terminals", terminal_ops.list_terminals, "列出活跃终端", {"type": "object", "properties": {}})
    reg.register_builtin(
        "delete_terminal", terminal_ops.delete_terminal, "销毁终端",
        {"type": "object", "properties": {"session_id": {"type": "string"}}, "required": ["session_id"]})

    # ---- 任务 ----
    reg.register_builtin(
        "TodoWrite", todo.manage_todos, "写入任务列表",
        {"type": "object", "properties": {"todos": {"type": "array"}}, "required": ["todos"]})
    reg.register_builtin(
        "read_todos", todo.read_todos, "读取任务列表", {"type": "object", "properties": {}})

    # ---- 技能（SKILL.md 目录格式，按需加载）----
    reg.register_builtin(
        "skill", _skill_tool, "按名称加载技能正文（SKILL.md 目录格式，按需注入）",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]})

    # ---- 网络 ----
    reg.register_builtin(
        "web_fetch", web_ops.web_fetch, "抓取 URL 网页内容（httpx，超时 30s，超过 max_bytes 截断）",
        {"type": "object", "properties": {
            "url": {"type": "string"},
            "max_bytes": {"type": "integer", "default": 1048576}},
         "required": ["url"]})
    reg.register_builtin(
        "web_search", web_ops.web_search,
        "网页搜索（settings web.search_url 端点，默认 DuckDuckGo HTML，解析标题/链接/摘要）",
        {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 5}},
         "required": ["query"]})

    # ---- 后台任务 ----
    reg.register_builtin(
        "job_list", jobs.job_list, "列出后台任务（状态/错误，不含结果）",
        {"type": "object", "properties": {}})
    reg.register_builtin(
        "job_output", jobs.job_output, "查询后台任务状态与结果",
        {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]})
    reg.register_builtin(
        "job_kill", jobs.job_kill, "取消后台任务",
        {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]})

    # ---- 人类确认 ----
    reg.register_builtin(
        "ask_user", ask_user.ask_user,
        "发起人类确认：挂起等待用户经桥接层确认框应答（超时返回 超时未应答）",
        {"type": "object", "properties": {
            "question": {"type": "string"},
            "timeout_sec": {"type": "integer", "default": 300}},
         "required": ["question"]})

    from core.logger import get_logger
    get_logger("tools.register").info(f"已注册 {len(reg.list_builtin_tools())} 个内置工具")
