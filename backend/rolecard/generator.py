"""动态生成 role_card：工具清单 + 技能目录快照 + start:{...}end 输出格式说明。"""
from typing import Dict, List

from tools.registry import get_registry
from mcp_services.manager import get_manager
from core.config import get_config


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

    # 拼接完整 role_card
    template = """# 系统规范（必须严格遵守）
你是运行在本地环境的工作助理，可调用本地工具完成文件操作、搜索、Git、终端、MCP 服务、技能等任务。

## 工具调用格式
需要调用工具时，必须输出如下格式（可多行并列多个调用，每行一个）：
```
start:{{"name":"工具名","arguments":{{"参数":"值"}}}}end
start:{{"name":"工具名2","arguments":{{...}}}}end
```
完整示例（参数名必须与工具行「参数:」提示完全一致，禁止发明参数名）：
```
start:{{"name":"mcp__windows-mcp__App","arguments":{{"mode":"launch_executable","executable":"C:/path/app.exe","args":["-h"]}}}}end
start:{{"name":"list_directory","arguments":{{"path":"C:/Download"}}}}end
start:{{"name":"mcp__windows-mcp__Registry","arguments":{{"mode":"get","path":"HKCU:/Environment","name":"Path"}}}}end
start:{{"name":"read_file","arguments":{{"path":"C:/Download/data.txt","limit":50}}}}end
start:{{"name":"sed_replace","arguments":{{"file_path":"C:/Download/app.py","pattern":"old_func\\\\s*\\\\(","replacement":"new_func(","count":-1}}}}end
start:{{"name":"registry_read","arguments":{{"path":"HKCU\\\\Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Run","name":"WeChat"}}}}end
start:{{"name":"env_set","arguments":{{"name":"JAVA_HOME","value":"C:/Java/jdk21","persistent":true}}}}end
start:{{"name":"run_as_admin","arguments":{{"executable":"regedit.exe"}}}}end
start:{{"name":"write_file","arguments":{{"path":"C:/Download/data.txt","content":"new line","append":true}}}}end
```
达到目标后停止输出工具调用，直接给出结论。

## 通用规则（务必遵守）
- Windows 路径：必须用正斜杠（C:/Download）或双反斜杠（C:\\\\Download），
  禁止使用单反斜杠（C:\\Download 会破坏 JSON 格式导致解析失败）。
- 工具执行需要时间：调用后等待结果返回，严禁在结果未到达时编造、猜测或伪造输出内容。
- 若一次调用失败（路径不存在/参数错误等），根据错误信息修正后重试；不要假设结果或放弃。
- 每次调用一个工具并观察返回结果，再决定下一步；避免一次调用多个不相关的工具。
- 工具返回的大段内容（文件全文/目录列表等）不要原文复述，提炼要点总结即可；
  若内容含特殊字符（框线/艺术字符等），说明其含义而非原样粘贴。
- 读取文本文件必须用 read_file 工具（自动检测编码）；禁止用 PowerShell/终端
  的 Get-Content/type 读取文本文件（Windows 终端默认 GBK 解码会导致中文乱码）。
- 严禁编造工具未返回的内容（校验和、哈希、密文、文件大小等），只基于真实结果回答。
- 用户请求涉及文件/目录/系统信息/命令执行/进程等本地内容时，必须调用相应工具
  获取真实结果，禁止仅凭常识或记忆回答（你无法知道用户电脑的真实状态）。
- 执行写入/修改操作后，主动验证结果，确保操作成功

{tool_lines}

{skills_section}"""

    tool_lines_str = "\n".join(tool_lines)
    skills_section = "\n".join(_skills_section())

    return template.format(
        tool_lines=tool_lines_str,
        skills_section=skills_section
    )


def generate_tools_json() -> Dict:
    """返回结构化工具清单（供桥接脚本 / 外部消费，含 input_schema）。"""
    registry = get_registry()
    manager = get_manager()
    tools = []
    for t in registry.list_builtin_tools():
        tools.append({"name": t["name"], "description": t["description"],
                      "input_schema": t["input_schema"], "source": "builtin"})
    for full_name, info in manager.tools.items():
        tools.append({"name": full_name, "description": info["tool"].get("description", ""),
                      "input_schema": info["tool"].get("input_schema") or {}, "source": "mcp"})
    from skills.discovery import get_discovery
    return {"tools": tools, "skills": [s.name for s in get_discovery().list()]}
