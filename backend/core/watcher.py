"""本地监听线程：技能目录变更 + 配置文件变更（MCP 热重载）自动感知。

- 技能目录：新增/删除/修改 SKILL.md → 自动刷新 catalog 与 digest（role_card 下次生成即含新技能）
- 配置文件：mcp.json / settings.yaml 变更 → 自动热重载，仅重连受影响的 MCP 服务

间隔由 settings.yaml `watcher.config_interval` / `watcher.skills_interval` 控制（秒，0=关闭）。
"""
import asyncio
from typing import Any, Dict, Optional

from core.logger import get_logger
from core.config import get_config

logger = get_logger("core.watcher")

_running = False
_tasks: list = []


async def _skills_watch_loop(interval: int) -> None:
    from skills.discovery import get_discovery
    while _running:
        try:
            await asyncio.sleep(interval)
            if get_discovery().check_changes():
                logger.info("技能目录变化，目录与 role_card digest 已刷新")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"技能监听异常: {e}")


async def _config_watch_loop(interval: int) -> None:
    from mcp_services.manager import get_manager
    config = get_config()
    last_services: Dict[str, Any] = dict(config.get_all_services())
    while _running:
        try:
            await asyncio.sleep(interval)
            if not config.check_reload():
                continue
            new_services: Dict[str, Any] = config.get_all_services()
            mgr = get_manager()
            # 新增 / 变更的服务 → 热重载（仅传输字段变化才重启）
            for name, cfg in new_services.items():
                if name not in last_services or last_services.get(name) != cfg:
                    await mgr.reload(name, cfg)
            # 被删除的服务 → 移除
            for name in list(last_services.keys()):
                if name not in new_services:
                    await mgr.remove(name)
            last_services = dict(new_services)
            logger.info("配置文件变更，MCP 服务已热重载")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"配置监听异常: {e}")


async def _watch_loop(config_interval: int, skills_interval: int) -> None:
    loops = []
    if config_interval > 0:
        loops.append(asyncio.create_task(_config_watch_loop(config_interval)))
    if skills_interval > 0:
        loops.append(asyncio.create_task(_skills_watch_loop(skills_interval)))
    if not loops:
        return
    try:
        await asyncio.gather(*loops)
    except asyncio.CancelledError:
        # 关闭时静默退出（子任务已在各自循环内处理取消）
        pass


def start_watchers() -> None:
    """启动监听线程（幂等）。间隔 0 表示关闭对应监听。"""
    global _running
    if _running:
        return
    cfg = get_config().get_watcher_config()
    ci = int(cfg.get("config_interval", 5))
    si = int(cfg.get("skills_interval", 5))
    if ci <= 0 and si <= 0:
        logger.info("监听线程未启用（watcher 间隔均为 0）")
        return
    _running = True
    _tasks.append(asyncio.create_task(_watch_loop(ci, si)))
    logger.info(f"监听线程已启动（配置 {ci}s / 技能 {si}s）")


async def stop_watchers() -> None:
    """停止监听线程（cancel 并回收任务）。"""
    global _running
    _running = False
    for t in _tasks:
        t.cancel()
    for t in _tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    _tasks.clear()
