"""网络内置工具：web_fetch 抓取网页 / web_search 网页搜索（HTML 端点解析）。"""
import html as html_mod
import re
import urllib.parse
from typing import Any, Dict, List

import httpx

from core.config import get_config
from core.logger import get_logger

logger = get_logger("tools.web_ops")

DEFAULT_SEARCH_URL = "https://html.duckduckgo.com/html/?q="
REQUEST_TIMEOUT = 30.0
DEFAULT_MAX_BYTES = 1024 * 1024  # 1MB
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ds-web-local/3.0"


async def web_fetch(url: str, max_bytes: int = DEFAULT_MAX_BYTES) -> Dict[str, Any]:
    """抓取 URL 内容：超时 30s，超过 max_bytes 截断，错误返回 error 信息。"""
    if not url or not str(url).startswith(("http://", "https://")):
        return {"error": "url 必须以 http:// 或 https:// 开头", "url": url}
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": USER_AGENT})
        if resp.status_code >= 400:
            return {"url": str(resp.url), "status": resp.status_code,
                    "error": f"HTTP {resp.status_code}"}
        raw = resp.content
        truncated = len(raw) > max_bytes
        if truncated:
            raw = raw[:max_bytes]
        content = raw.decode("utf-8", errors="replace")
        logger.info(f"web_fetch 完成: {resp.url} status={resp.status_code} bytes={len(raw)}")
        return {"url": str(resp.url), "status": resp.status_code,
                "content": content, "truncated": truncated}
    except httpx.TimeoutException:
        return {"url": url, "error": "请求超时（30s）"}
    except httpx.HTTPError as e:
        return {"url": url, "error": f"网络错误: {e.__class__.__name__}: {e}"}
    except Exception as e:  # 兜底
        logger.exception(f"web_fetch 异常: {e}")
        return {"url": url, "error": f"抓取失败: {e}"}


def _unwrap_ddg_url(href: str) -> str:
    """解包 DuckDuckGo 的 /l/?uddg= 重定向链接。"""
    m = re.search(r"[?&]uddg=([^&]+)", href)
    if m:
        try:
            return urllib.parse.unquote(m.group(1))
        except Exception:
            return href
    return href


def _clean_text(raw: str) -> str:
    return html_mod.unescape(re.sub(r"<[^>]+>", "", raw)).strip()


def _parse_search_html(html: str, limit: int) -> List[Dict]:
    """解析 DuckDuckGo html 端点结果：标题/链接/摘要。"""
    results: List[Dict] = []
    # 标题块：<a class="result__a" href="...">标题</a>
    for m in re.finditer(
            r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html, re.S):
        title = _clean_text(m.group(2))
        if not title:
            continue
        results.append({"title": title, "url": _unwrap_ddg_url(m.group(1)), "snippet": ""})
        if len(results) >= limit:
            break
    # 摘要块：<a class="result__snippet" href="...">摘要</a>（与标题一一对应）
    snippets = re.findall(
        r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', html, re.S)
    for i, s in enumerate(snippets):
        if i < len(results):
            results[i]["snippet"] = _clean_text(s)
    return results


async def web_search(query: str, limit: int = 5) -> Any:
    """网页搜索：请求可配置搜索引擎端点（settings web.search_url），解析 HTML 提取结果。

    失败返回错误信息字符串（约定：成功返回 dict，失败返回 str）。
    """
    if not query or not str(query).strip():
        return "搜索失败: 缺少查询词 query"
    try:
        limit = max(1, min(int(limit), 20))
    except (TypeError, ValueError):
        limit = 5
    config = get_config()
    search_url = config.get_settings("web.search_url", DEFAULT_SEARCH_URL) or DEFAULT_SEARCH_URL
    try:
        params = None
        if "{query}" in search_url:
            url = search_url.replace("{query}", urllib.parse.quote(str(query)))
        elif search_url.rstrip().endswith("="):
            url = search_url + urllib.parse.quote(str(query))
        else:
            url = search_url
            params = {"q": str(query)}
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, params=params, headers={"User-Agent": USER_AGENT})
        if resp.status_code >= 400:
            return f"搜索失败: HTTP {resp.status_code}"
        results = _parse_search_html(resp.text, limit)
        logger.info(f"web_search 完成: q={query} results={len(results)}")
        return {"query": query, "results": results, "count": len(results), "status": "success"}
    except httpx.TimeoutException:
        return "搜索失败: 请求超时（30s）"
    except httpx.HTTPError as e:
        return f"搜索失败: 网络错误: {e.__class__.__name__}: {e}"
    except Exception as e:
        logger.exception(f"web_search 异常: {e}")
        return f"搜索失败: {e}"
