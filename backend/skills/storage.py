"""技能持久化（SKILL.md 目录格式：多根发现 + user 根写入，兼容 legacy json）。

读写统一走 skills.discovery：
- 读：合并目录（bundled > user > hermes > legacy）
- 写：仅 user 根目录（backend/data/user-skills/<name>/SKILL.md）
"""
from typing import Any, Dict, Optional

from skills.discovery import get_discovery


def list_skills() -> Dict[str, Any]:
    """返回 {name: summary_dict} 合并目录。"""
    return {s["name"]: s for s in get_discovery().snapshot()["skills"]}


def get_skill(name: str) -> Optional[Dict]:
    """返回完整技能定义（含 content/steps/source）。"""
    d = get_discovery().get(name)
    return d.to_dict() if d else None


def save_skill(name: str, skill: Dict) -> Dict:
    """写入 user 根目录（生成 <name>/SKILL.md），返回最新定义。"""
    return get_discovery().save(name, skill).to_dict()


def delete_skill(name: str) -> bool:
    """仅允许删除 user 源技能。"""
    return get_discovery().delete(name)
