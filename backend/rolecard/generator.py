"""动态生成 role_card：工具清单 + 技能说明 + @@tool 行锚定输出格式说明。

模板已外部化为 Markdown 文件，改提示词只需编辑 md —— 无需改 Python、无需改 JS 脚本、
无需重新打包油猴脚本。

分层（与 skills 一致）：
    backend/rolecard/template.md   内置默认（随代码发布，勿直接改）
    backend/config/rolecard.md     用户副本（首次运行自动生成，之后以它为准）

占位符用「显式替换」而非 str.format：模板里可自由书写 { }，不必把花括号双写转义
（旧实现用 .format，模板里每个 { 都要写成 {{，漏写即运行时崩溃）。
"""
from pathlib import Path
from typing import Dict, List

from tools.registry import get_registry
from mcp_services.manager import get_manager
from core.config import get_config

TOOLS_PLACEHOLDER = "<!--TOOLS-->"
SKILLS_PLACEHOLDER = "<!--SKILLS-->"

_BUILTIN_TEMPLATE = Path(__file__).resolve().parent / "template.md"

# 极端兜底：内置 md 也缺失时仍要能出一份可用的 role_card，不让桥接整体失效
_FALLBACK_TEMPLATE = (
    "# 系统规范（必须严格遵守）\n"
    "你是运行在本地环境的工作助理，可调用下列工具完成任务。\n\n"
    "## 工具调用格式（每行一条，行首 @@tool）\n"
    "@@tool 工具名 {\"参数\":\"值\"}\n"
    "一条调用独占一行、行首必须是 @@tool、无终止符。\n\n"
    "<!--TOOLS-->\n\n<!--SKILLS-->"
)

_tpl_cache: Dict[str, object] = {"path": "", "mtime": 0.0, "text": ""}


def _xml_escape(s: str) -> str:
    """XML 转义 & < > " '。"""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))


def _skills_section() -> List[str]:
    """生成「## 可用技能」段：只给提示，不列清单（技能名/描述不占 role_card 上下文）。

    设计（dsh R7 精简版）：技能清单经 skill 工具按需查询——
    skill 不传 name 返回全部技能摘要；传 name 加载正文。
    """
    lines = ["", "## 可用技能"]
    lines.append("（技能库按需查询：调用 skill 工具且不传 name 返回全部技能列表；")
    lines.append("传 name 参数加载技能正文。无需记住技能名，随时可查。）")
    lines.append("")
    return lines


def _schema_hint(input_schema: Dict) -> str:
    """从 input_schema 生成参数提示（键名 + 枚举值），让模型不用猜参数名。"""
    if not input_schema:
        return ""
    props = input_schema.get("properties") or {}
    if not props:
        return ""
    parts = []
    for k, v in props.items():
        if not isinstance(v, dict):
            parts.append(k)
            continue
        enum = v.get("enum")
        if enum and isinstance(enum, list):
            parts.append(f"{k}=[{','.join(str(x) for x in enum[:6])}]")
        else:
            parts.append(k)
    hint = " 参数: " + ", ".join(parts)
    return hint[:200]


def _template_file() -> Path:
    """用户模板路径；settings.yaml 的 rolecard.template 可覆盖（相对路径基于 config 目录）。"""
    cfg = get_config()
    rel = cfg.get_settings("rolecard.template", "rolecard.md") or "rolecard.md"
    p = Path(rel)
    return p if p.is_absolute() else (Path(cfg.config_dir) / p)


def _builtin_text() -> str:
    """内置默认模板文本（内置 md 缺失时退到代码内兜底）。"""
    try:
        if _BUILTIN_TEMPLATE.exists():
            return _BUILTIN_TEMPLATE.read_text(encoding="utf-8")
    except Exception:
        pass
    return _FALLBACK_TEMPLATE


def ensure_template_file() -> Path:
    """确保用户模板存在：首次运行从内置默认复制一份，之后以用户副本为准。"""
    p = _template_file()
    if p.exists():
        return p
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_builtin_text(), encoding="utf-8")
        return p
    except Exception:
        return _BUILTIN_TEMPLATE


def load_template() -> str:
    """读取模板文本：按 mtime 缓存，文件一改下次请求即生效（无需重启后端）。"""
    p = ensure_template_file()
    try:
        mt = p.stat().st_mtime
        if _tpl_cache["path"] != str(p) or _tpl_cache["mtime"] != mt:
            _tpl_cache["text"] = p.read_text(encoding="utf-8")
            _tpl_cache["path"] = str(p)
            _tpl_cache["mtime"] = mt
        return str(_tpl_cache["text"])
    except Exception:
        return _builtin_text()


def generate_role_card() -> str:
    """生成文本版 role_card，供 web 端「脑」注入使用。"""
    registry = get_registry()
    manager = get_manager()

    tools: List[Dict] = []
    for t in registry.list_builtin_tools():
        tools.append({"name": t["name"], "description": t["description"],
                      "source": "builtin", "input_schema": t["input_schema"]})
    for full_name, info in manager.tools.items():
        tools.append({"name": full_name, "description": info["tool"].get("description", ""),
                      "source": "mcp", "input_schema": info["tool"].get("input_schema") or {}})

    # 工具清单（动态生成）
    tool_lines = []
    tool_lines.append("## 可用工具清单")
    tool_lines.append("（参数名/枚举值见各工具行尾部「参数:」提示，调用时严格使用这些参数名）")
    for t in tools:
        hint = _schema_hint(t["input_schema"])
        tool_lines.append(f"- {t['name']}: {t['description']}{hint}")

    tools_text = "\n".join(tool_lines)
    skills_text = "\n".join(_skills_section())

    # 占位符缺失时追加到末尾，保证工具清单不会静默消失
    tpl = load_template()
    if TOOLS_PLACEHOLDER not in tpl:
        tpl = tpl.rstrip() + "\n\n" + TOOLS_PLACEHOLDER
    if SKILLS_PLACEHOLDER not in tpl:
        tpl = tpl.rstrip() + "\n\n" + SKILLS_PLACEHOLDER

    return tpl.replace(TOOLS_PLACEHOLDER, tools_text).replace(SKILLS_PLACEHOLDER, skills_text)


def generate_tools_json() -> Dict:
    """返回结构化工具清单（供桥接脚本 / 外部消费，含 input_schema）。"""
    registry = get_registry()
    manager = get_manager()
    tools = []
    for t in registry.list_builtin_tools():
        tools.append({"name": t["name"], "description": t["description"],
                      "input_schema": t["input_schema"], "source": "builtin",
                      "idempotent": bool(t.get("idempotent", False))})
    # MCP 工具语义未知，默认非幂等（浏览器不做 30s 幂等批去重），
    # 避免重复点击/未知副作用的调用被静默吞掉
    for full_name, info in manager.tools.items():
        tools.append({"name": full_name, "description": info["tool"].get("description", ""),
                      "input_schema": info["tool"].get("input_schema") or {}, "source": "mcp",
                      "idempotent": False})
    from skills.discovery import get_discovery
    return {"tools": tools, "skills": [s.name for s in get_discovery().list()]}
