"""SKILL.md 技能发现：多根扫描 + frontmatter 解析 + 目录快照（dsh/Hermes 生态互通）。

设计要点：
- 多根发现（按优先级，先扫先得）：
  1) backend/skills/            内置技能（bundled）
  2) backend/data/user-skills/  用户自定义（user），API CRUD 写入此目录
  3) D:/Hermes/skills           只读导入源（hermes），配置开关 hermes_import_ro 控制，默认关闭
- 两种形态：目录包 <name>/SKILL.md 与扁平文件 <name>.md；技能名必须 kebab-case
- frontmatter（YAML，--- 分隔）：description 必填（缺失跳过）、when_to_use 可选、
  model-invocable / user-invocable 默认 true；正文为 content
- 层级合并：同名 fs 优先于 legacy json（backend/config/skills.json，source=legacy）；
  fs 内按根目录优先级（bundled > user > hermes）
- 目录快照：snapshot() 返回 {skills: [SkillSummary...], complete, digest}，
  digest(sha256) 供 role_card 增量刷新判断
- 变更检测：轻量轮询——每次 snapshot() 对比各根目录 mtime/文件列表（不做 watcher）
"""
import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from core.config import BASE_DIR, DATA_DIR, get_config
from core.logger import get_logger

logger = get_logger("skills.discovery")

SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_STEPS_BLOCK_RE = re.compile(r"```steps\s*\n(.*?)```", re.DOTALL)


@dataclass
class SkillSummary:
    """技能摘要（目录快照 / role_card 使用）。"""
    name: str
    description: str
    when_to_use: str = ""
    invocation: Dict[str, bool] = field(
        default_factory=lambda: {"model": True, "user": True}
    )
    source: str = "bundled"  # bundled | user | hermes | legacy

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "when_to_use": self.when_to_use,
            "invocation": dict(self.invocation),
            "source": self.source,
        }


@dataclass
class SkillDefinition(SkillSummary):
    """完整技能定义：摘要 + content + steps（供 skill 工具 / 执行引擎）。"""
    content: str = ""
    steps: Optional[List[Dict[str, Any]]] = None
    tools: Optional[List[Dict[str, Any]]] = None  # legacy 字段保留
    output_template: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d.update({
            "content": self.content,
            "steps": self.steps,
            "tools": self.tools,
            "output_template": self.output_template,
            "parameters": dict(self.parameters),
        })
        return d


# ---------- frontmatter / 正文解析 ----------

def _split_frontmatter(text: str) -> Tuple[Optional[str], str]:
    """按 --- 分隔符拆 frontmatter 与正文；无 frontmatter 返回 (None, 原文)。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return None, text


def _extract_steps_block(body: str) -> Optional[List[Dict[str, Any]]]:
    """从正文识别 ```steps``` yaml 代码块。"""
    m = _STEPS_BLOCK_RE.search(body)
    if not m:
        return None
    try:
        data = yaml.safe_load(m.group(1))
    except Exception:
        return None
    return data if isinstance(data, list) else None


def parse_skill_file(path: Path, name: str, source: str) -> Optional[SkillDefinition]:
    """解析单个 SKILL.md / <name>.md；description 缺失或 frontmatter 损坏返回 None。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning("读取技能 %s 失败: %s", path, e)
        return None
    fm, body = _split_frontmatter(text)
    meta: Dict[str, Any] = {}
    if fm is not None:
        try:
            meta = yaml.safe_load(fm) or {}
        except Exception as e:
            logger.warning("技能 %s frontmatter 解析失败: %s", name, e)
            return None
    if not isinstance(meta, dict):
        meta = {}
    description = str(meta.get("description", "")).strip()
    if not description:
        logger.warning("技能 %s 缺少 description，跳过", name)
        return None

    invocation = {
        "model": bool(meta.get("model-invocable", True)),
        "user": bool(meta.get("user-invocable", True)),
    }
    steps = meta.get("steps")
    if not isinstance(steps, list):
        steps = _extract_steps_block(body)
    return SkillDefinition(
        name=name,
        description=description,
        when_to_use=str(meta.get("when_to_use", "") or ""),
        invocation=invocation,
        source=source,
        content=body.strip() or "",
        steps=steps if isinstance(steps, list) else None,
        output_template=meta.get("output_template"),
        parameters=meta.get("parameters") or {},
    )


def build_skill_md(skill: Dict[str, Any]) -> str:
    """将技能 dict 序列化为 <name>/SKILL.md（frontmatter + content）。"""
    meta: Dict[str, Any] = {}
    if skill.get("description"):
        meta["description"] = skill["description"]
    if skill.get("when_to_use"):
        meta["when_to_use"] = skill["when_to_use"]
    if "model-invocable" in skill:
        meta["model-invocable"] = bool(skill["model-invocable"])
    if "user-invocable" in skill:
        meta["user-invocable"] = bool(skill["user-invocable"])
    steps = skill.get("steps")
    if not isinstance(steps, list) and isinstance(skill.get("tools"), list):
        steps = skill["tools"]  # 兼容旧字段 tools → steps
    if steps:
        meta["steps"] = steps
    if skill.get("output_template") is not None:
        meta["output_template"] = skill["output_template"]
    if skill.get("parameters"):
        meta["parameters"] = skill["parameters"]
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=False)
    content = skill.get("content")
    if content is None:
        content = skill.get("prompt", "")  # 兼容旧字段 prompt
    return f"---\n{fm}---\n\n{content or ''}".rstrip() + "\n"


# ---------- 发现器 ----------

class SkillDiscovery:
    def __init__(
        self,
        roots: Optional[List[Tuple[Path, str]]] = None,
        legacy: Optional[Dict[str, Any]] = None,
        max_depth: int = 2,
    ):
        """roots: [(目录, source)]，按优先级排序（先扫先得）；legacy: legacy json 技能 dict。"""
        self.roots = roots if roots is not None else self._default_roots()
        self.legacy = legacy if legacy is not None else get_config().get_all_skills()
        self.max_depth = max_depth
        self._catalog: Dict[str, SkillSummary] = {}
        self._defs: Dict[str, SkillDefinition] = {}
        self._digest: str = ""
        self._complete: bool = True
        self._signatures: Dict[str, Optional[Tuple[Any, ...]]] = {}
        # 确保 user 根目录存在（缺根不视为扫描不完整）
        for root, source in self.roots:
            if source == "user":
                root.mkdir(parents=True, exist_ok=True)
        self._rescan()

    @staticmethod
    def _default_roots() -> List[Tuple[Path, str]]:
        cfg = get_config().get_skills_config()
        roots: List[Tuple[Path, str]] = []
        for d in cfg.get("skills_dirs", ["skills", "data/user-skills"]):
            p = Path(d)
            if not p.is_absolute():
                p = BASE_DIR / p
            if p == (BASE_DIR / "skills"):
                source = "bundled"
            elif p == (BASE_DIR / "data" / "user-skills"):
                # 默认 user 根跟随 DSW_DATA_DIR（外部化时数据目录整体搬走）
                if DATA_DIR != BASE_DIR / "data":
                    p = DATA_DIR / "user-skills"
                source = "user"
            else:
                source = "custom"  # 自定义目录（网盘/共享盘等），绝对路径任意位置
            roots.append((p, source))
        if cfg.get("hermes_import_ro", False):
            hermes_dir = Path(cfg.get("hermes_import_dir", "D:/Hermes/skills"))
            roots.append((hermes_dir, "hermes"))
        return roots

    # ---- 扫描 ----

    @staticmethod
    def _dir_signature(root: Path) -> Tuple[Any, ...]:
        """轻量变更指纹：根目录 mtime + 相关文件列表 (相对路径, mtime_ns, 大小)。"""
        files = []
        try:
            for p in root.rglob("*.md"):
                try:
                    st = p.stat()
                    files.append((p.relative_to(root).as_posix(), st.st_mtime_ns, st.st_size))
                except OSError:
                    continue
            root_mtime = root.stat().st_mtime_ns
        except OSError:
            return ()
        return (root_mtime, tuple(sorted(files)))

    def _scan_root(
        self, root: Path, source: str, depth: int
    ) -> Dict[str, SkillDefinition]:
        """扫描单个根目录（目录包 /SKILL.md + 扁平 <name>.md，支持分类子目录下探一层）。"""
        found: Dict[str, SkillDefinition] = {}
        try:
            entries = sorted(root.iterdir(), key=lambda p: p.name)
        except OSError:
            return found
        for p in entries:
            if p.is_file() and p.suffix == ".md":
                name = p.stem
                if not SKILL_NAME_RE.match(name):
                    continue
                defn = parse_skill_file(p, name, source)
                if defn:
                    found[name] = defn
            elif p.is_dir() and not p.name.startswith("."):
                md = p / "SKILL.md"
                if md.is_file():
                    name = p.name
                    if not SKILL_NAME_RE.match(name):
                        continue
                    defn = parse_skill_file(md, name, source)
                    if defn:
                        found[name] = defn
                elif depth < self.max_depth - 1:
                    # 分类目录（如 D:/Hermes/skills 下的 devops/）：下探一层
                    found.update(self._scan_root(p, source, depth + 1))
        return found

    def _legacy_to_definition(self, name: str, skill: Dict[str, Any]) -> SkillDefinition:
        tools = skill.get("tools") if isinstance(skill.get("tools"), list) else None
        return SkillDefinition(
            name=name,
            description=str(skill.get("description", "") or ""),
            when_to_use=str(skill.get("when_to_use", "") or ""),
            invocation={
                "model": bool(skill.get("model-invocable", True)),
                "user": bool(skill.get("user-invocable", True)),
            },
            source="legacy",
            content=str(skill.get("prompt", "") or ""),
            steps=tools,  # tools → steps 映射（引擎按 steps 流水线执行）
            tools=tools,
            output_template=skill.get("output_template"),
            parameters=skill.get("parameters") or {},
        )

    def _rescan(self) -> None:
        catalog: Dict[str, SkillSummary] = {}
        defs: Dict[str, SkillDefinition] = {}
        complete = True
        signatures: Dict[str, Optional[Tuple[Any, ...]]] = {}
        for root, source in self.roots:
            key = str(root)
            if not root.exists():
                complete = False
                signatures[key] = None
                continue
            signatures[key] = self._dir_signature(root)
            for name, defn in self._scan_root(root, source, 0).items():
                if name not in catalog:  # 先扫先得：根目录优先级
                    catalog[name] = defn
                    defs[name] = defn
        # legacy json 并入（fs 优先）
        for name, skill in self.legacy.items():
            if name in catalog:
                continue
            defn = self._legacy_to_definition(name, skill)
            catalog[name] = defn
            defs[name] = defn
        self._catalog = catalog
        self._defs = defs
        self._complete = complete
        self._signatures = signatures
        self._digest = self._compute_digest(catalog)

    @staticmethod
    def _compute_digest(catalog: Dict[str, SkillSummary]) -> str:
        items = sorted((s.to_dict() for s in catalog.values()), key=lambda d: d["name"])
        payload = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ---- 对外接口 ----

    def _changed(self) -> bool:
        for root, _ in self.roots:
            key = str(root)
            if not root.exists():
                if self._signatures.get(key) is not None:
                    return True
                continue
            if self._dir_signature(root) != self._signatures.get(key):
                return True
        return False

    def check_changes(self) -> bool:
        """供监听线程周期调用：检测目录变更并刷新，返回是否有变化。"""
        if self._changed():
            self._rescan()
            return True
        return False

    def snapshot(self) -> Dict[str, Any]:
        """目录快照：{skills: [summary...], complete, digest}。轮询检测变更后重新扫描。"""
        if self._changed():
            self._rescan()
        return {
            "skills": [s.to_dict() for s in self._catalog.values()],
            "complete": self._complete,
            "digest": self._digest,
        }

    def list(self) -> List[SkillSummary]:
        self.snapshot()
        return list(self._catalog.values())

    def get(self, name: str) -> Optional[SkillDefinition]:
        self.snapshot()
        return self._defs.get(name)

    def get_summary(self, name: str) -> Optional[SkillSummary]:
        self.snapshot()
        return self._catalog.get(name)

    def get_digest(self) -> str:
        return self.snapshot()["digest"]

    def _user_root(self) -> Path:
        for root, source in self.roots:
            if source == "user":
                root.mkdir(parents=True, exist_ok=True)
                return root
        raise RuntimeError("未配置 user 技能根目录（skills_dirs 需包含 data/user-skills）")

    # ---- 写入（API CRUD → user 根目录）----

    def save(self, name: str, skill: Dict[str, Any]) -> SkillDefinition:
        """写入 <user_root>/<name>/SKILL.md；返回最新定义。"""
        root = self._user_root()
        pkg = root / name
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "SKILL.md").write_text(build_skill_md(skill), encoding="utf-8")
        self._rescan()
        defn = self._defs.get(name)
        if not defn:
            raise RuntimeError(f"技能 '{name}' 保存后未出现在目录中")
        return defn

    def delete(self, name: str) -> bool:
        """仅允许删除 user 源技能；bundled/hermes/legacy 为只读。"""
        defn = self._defs.get(name)
        if not defn or defn.source != "user":
            return False
        root = self._user_root()
        pkg = root / name
        if pkg.exists() and (pkg / "SKILL.md").exists():
            shutil.rmtree(pkg)
            self._rescan()
            return True
        flat = root / f"{name}.md"
        if flat.exists():
            flat.unlink()
            self._rescan()
            return True
        return False

    # ---- 导入（hermes | bundled → user 副本）----

    def _source_root(self, source_dir: str) -> Optional[Path]:
        for root, source in self.roots:
            if source == source_dir:
                return root
        return None

    def _find_skill_path(self, src_root: Path, name: str):
        """在源根内定位技能：目录包 <name>/SKILL.md 或扁平 <name>.md（支持分类目录下探）。

        与 _scan_root 一致的遍历方式（iterdir + is_file()，跟随符号链接），
        保证 catalog 中可见的技能（如 D:/Hermes/skills 下的 symlink 技能目录）
        一定可以定位导入。Windows 上 iterdir 需完整消费，避免遗留目录句柄。
        """

        def walk(d: Path, depth: int):
            try:
                entries = sorted(d.iterdir(), key=lambda p: p.name)
            except OSError:
                return None, None
            pkg = None
            flat = None
            for p in entries:
                try:
                    if p.is_file() and p.name == "SKILL.md" and p.parent.name == name:
                        return p.parent, "pkg"
                    if p.is_file() and p.name == f"{name}.md" and flat is None:
                        flat = p
                    elif p.is_dir() and not p.name.startswith(".") and depth < self.max_depth:
                        rp, rf = walk(p, depth + 1)
                        if rp is not None:
                            return rp, "pkg"
                        if rf is not None and flat is None:
                            flat = rf
                except OSError:
                    continue
            if pkg:
                return pkg, "pkg"
            if flat:
                return flat, "flat"
            return None, None

        return walk(src_root, 0)

    def import_skill(self, source_dir: str, name: str) -> Optional[SkillDefinition]:
        """从源根目录（hermes | bundled）深拷贝技能副本到 user 根目录。"""
        if not SKILL_NAME_RE.match(name):
            return None
        src_root = self._source_root(source_dir)
        if not src_root or not src_root.exists():
            return None
        located = self._find_skill_path(src_root, name)
        if not located or located[0] is None:
            return None
        src_path, kind = located
        target = self._user_root()
        if kind == "pkg":
            # 深拷贝 SKILL.md 及同名目录资源文件
            shutil.copytree(src_path, target / name, dirs_exist_ok=True)
        else:
            shutil.copy2(src_path, target / f"{name}.md")
            # 同名目录资源一并拷贝
            res_dir = src_path.parent / name
            if res_dir.is_dir():
                shutil.copytree(res_dir, target / name, dirs_exist_ok=True)
        self._rescan()
        return self._defs.get(name)


_discovery: Optional[SkillDiscovery] = None


def get_discovery() -> SkillDiscovery:
    """全局发现器单例。"""
    global _discovery
    if _discovery is None:
        _discovery = SkillDiscovery()
    return _discovery
