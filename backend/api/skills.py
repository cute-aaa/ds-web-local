"""技能管理 API（契约 B）：SKILL.md 目录格式 CRUD + catalog + import + execute。

路由说明（注意顺序）：/catalog、/import 等静态段必须在 /{name} 之前声明。
- GET    /api/skills            合并目录（summary + source）
- GET    /api/skills/catalog    目录快照 {skills, digest, complete}（供 role_card）
- POST   /api/skills            新建（写入 user 根，<name>/SKILL.md）
- POST   /api/skills/import     从 hermes/bundled 源拷贝副本到 user 根
- GET    /api/skills/{name}     技能详情（含 content）
- GET    /api/skills/{name}/load 完整定义（content + steps，供 skill 工具）
- PUT    /api/skills/{name}     覆盖（仅 user 源技能）
- DELETE /api/skills/{name}     删除（仅 user 源技能）
- POST   /api/skills/{name}/execute 执行
"""
from typing import Any, Dict, Optional
from fastapi import APIRouter
from pydantic import BaseModel, Field

from core.logger import get_logger
from core.errors import ApiError, ErrorCode
from skills.discovery import get_discovery, SKILL_NAME_RE
from skills.engine import get_skill_engine

router = APIRouter(prefix="/api/skills", tags=["skills"])
logger = get_logger("api.skills")


class SkillBody(BaseModel):
    name: str = ""
    description: str = ""
    when_to_use: str = ""
    prompt: str = ""                     # 旧字段（兼容）：正文
    content: Optional[str] = None        # 新格式正文（优先于 prompt）
    tools: list = Field(default_factory=list)       # 旧字段（兼容）：steps
    steps: Optional[list] = None         # 新格式步骤
    output_template: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    model_invocable: Optional[bool] = None
    user_invocable: Optional[bool] = None


class SkillImportBody(BaseModel):
    source_dir: str = "hermes"  # hermes | bundled
    name: str = ""


class SkillExecuteBody(BaseModel):
    inputs: Dict[str, Any] = Field(default_factory=dict)


def _body_to_skill_dict(body: SkillBody) -> Dict[str, Any]:
    """SkillBody → 落盘 dict（frontmatter 字段 + content/steps）。"""
    skill: Dict[str, Any] = {"description": body.description}
    if body.when_to_use:
        skill["when_to_use"] = body.when_to_use
    if body.model_invocable is not None:
        skill["model-invocable"] = body.model_invocable
    if body.user_invocable is not None:
        skill["user-invocable"] = body.user_invocable
    content = body.content if body.content is not None else body.prompt
    if content:
        skill["content"] = content
    steps = body.steps if body.steps is not None else (body.tools or None)
    if steps:
        skill["steps"] = steps
    if body.output_template is not None:
        skill["output_template"] = body.output_template
    if body.parameters:
        skill["parameters"] = body.parameters
    return skill


def _not_found(name: str):
    return ApiError(ErrorCode.NOT_FOUND, f"技能 '{name}' 不存在", 404)


@router.get("")
async def list_all():
    """合并目录：summary 列表（含 source）。"""
    snap = get_discovery().snapshot()
    return {"skills": snap["skills"], "digest": snap["digest"], "complete": snap["complete"]}


@router.post("/reload")
async def reload_skills():
    """强制刷新技能目录（重扫 SKILL.md 多根 + 更新 digest，供桥接面板「刷新技能」按钮）。"""
    from skills.discovery import get_discovery
    d = get_discovery()
    d._rescan()
    snap = d.snapshot()
    return {"status": "ok", "count": len(snap["skills"]), "digest": snap["digest"]}


@router.get("/dirs")
async def skill_dirs():
    """当前技能发现目录（含 hermes 只读源），供桥接面板展示。"""
    from skills.discovery import get_discovery
    d = get_discovery()
    return {"dirs": [{"path": str(root), "source": source} for root, source in d.roots]}


@router.get("/catalog")
async def catalog():
    """目录快照（供 role_card 增量刷新判断）。"""
    return get_discovery().snapshot()


@router.post("")
async def create(body: SkillBody):
    name = body.name
    if not name:
        raise ApiError(ErrorCode.BAD_REQUEST, "缺少技能名 name")
    if not SKILL_NAME_RE.match(name):
        raise ApiError(ErrorCode.BAD_REQUEST,
                        "技能名必须为 kebab-case（小写字母/数字，- 分隔，如 my-skill）")
    discovery = get_discovery()
    if discovery.get(name):
        raise ApiError(ErrorCode.CONFLICT, f"技能 '{name}' 已存在", 409)
    skill = _body_to_skill_dict(body)
    defn = discovery.save(name, skill)
    return {"status": "success", "name": name, "skill": defn.to_dict()}


@router.post("/import")
async def import_skill(body: SkillImportBody):
    """从源根目录（hermes | bundled）拷贝副本到 data/user-skills/。"""
    if body.source_dir not in ("hermes", "bundled"):
        raise ApiError(ErrorCode.BAD_REQUEST, "source_dir 必须为 hermes 或 bundled")
    defn = get_discovery().import_skill(body.source_dir, body.name)
    if not defn:
        raise ApiError(ErrorCode.NOT_FOUND,
                       f"在 {body.source_dir} 源中找不到技能 '{body.name}'", 404)
    return {"status": "success", "name": body.name,
            "skill": defn.to_dict(), "source_dir": body.source_dir}


@router.get("/{name}/load")
async def load(name: str):
    """完整定义（content + steps），供 skill 工具按需加载。"""
    defn = get_discovery().get(name)
    if not defn:
        raise _not_found(name)
    return {"name": name, "skill": defn.to_dict()}


@router.get("/{name}")
async def detail(name: str):
    defn = get_discovery().get(name)
    if not defn:
        raise _not_found(name)
    data = defn.to_dict()
    data.setdefault("prompt", data.get("content", ""))  # 向后兼容旧字段
    return {"name": name, "skill": data}


@router.put("/{name}")
async def update(name: str, body: SkillBody):
    discovery = get_discovery()
    defn = discovery.get(name)
    if not defn:
        raise _not_found(name)
    if defn.source != "user":
        raise ApiError(ErrorCode.BAD_REQUEST,
                       f"技能 '{name}' 来自只读源（{defn.source}），不允许修改", 400)
    skill = _body_to_skill_dict(body)
    updated = discovery.save(name, skill)
    return {"status": "success", "name": name, "skill": updated.to_dict()}


@router.delete("/{name}")
async def delete(name: str):
    if not get_discovery().delete(name):
        raise ApiError(ErrorCode.NOT_FOUND,
                       f"技能 '{name}' 不存在或来自只读源", 404)
    return {"status": "success", "name": name}


@router.post("/{name}/execute")
async def execute(name: str, body: SkillExecuteBody):
    engine = get_skill_engine()
    result = await engine.execute_skill(name, body.inputs)
    if "error" in result and "output" not in result:
        raise ApiError(ErrorCode.NOT_FOUND, result["error"], 404)
    return result
