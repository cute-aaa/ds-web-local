"""web_ops 内置工具测试：mock httpx 验证 fetch 成功/失败/超限 与 search 解析。"""
import httpx
import pytest

from tools import web_ops
from core.config import get_config


class FakeResponse:
    def __init__(self, content=b"", text="", status_code=200, url="http://example.com/"):
        self.content = content
        self._text = text
        self.status_code = status_code
        self.url = url

    @property
    def text(self):
        return self._text if self._text else self.content.decode("utf-8", errors="replace")


class FakeClient:
    last_instance = None

    def __init__(self, *args, **kwargs):
        self._responses = []
        self._exc = None
        self.last_url = None
        self.last_kwargs = None
        FakeClient.last_instance = self

    def set_responses(self, *responses):
        self._responses = list(responses)

    def set_exception(self, exc):
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kwargs):
        self.last_url = url
        self.last_kwargs = kwargs
        if self._exc is not None:
            raise self._exc
        if not self._responses:
            raise AssertionError("未配置 mock 响应")
        return self._responses.pop(0)


@pytest.fixture
def fake_httpx(monkeypatch):
    """mock 掉 httpx.AsyncClient，返回固定的 FakeClient 实例。"""
    FakeClient.last_instance = None
    client = FakeClient()
    monkeypatch.setattr(web_ops.httpx, "AsyncClient", lambda *a, **k: client)
    yield client
    FakeClient.last_instance = None


# ---- web_fetch ----

async def test_fetch_success(fake_httpx):
    fake_httpx.set_responses(FakeResponse(content=b"<html>ok</html>", status_code=200,
                                          url="http://example.com/page"))
    result = await web_ops.web_fetch("http://example.com/page")
    assert result["status"] == 200
    assert result["content"] == "<html>ok</html>"
    assert result["truncated"] is False
    assert result["url"] == "http://example.com/page"


async def test_fetch_truncated(fake_httpx):
    fake_httpx.set_responses(FakeResponse(content=b"x" * 5000, status_code=200))
    result = await web_ops.web_fetch("http://example.com/big", max_bytes=1024)
    assert result["truncated"] is True
    assert len(result["content"]) <= 1024


async def test_fetch_http_error(fake_httpx):
    fake_httpx.set_responses(FakeResponse(content=b"", status_code=404, url="http://example.com/missing"))
    result = await web_ops.web_fetch("http://example.com/missing")
    assert result["status"] == 404
    assert "error" in result


async def test_fetch_network_error(fake_httpx):
    fake_httpx.set_exception(httpx.ConnectError("connection refused"))
    result = await web_ops.web_fetch("http://example.com/down")
    assert "error" in result
    assert "网络错误" in result["error"]


async def test_fetch_timeout(fake_httpx):
    fake_httpx.set_exception(httpx.TimeoutException("timed out"))
    result = await web_ops.web_fetch("http://example.com/slow")
    assert "超时" in result["error"]


async def test_fetch_bad_url(fake_httpx):
    result = await web_ops.web_fetch("not-a-url")
    assert "error" in result


# ---- web_search ----

DDG_HTML = """
<html><body>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage1&amp;rut=x">Example Page One</a>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=...">first snippet here</a>
</div>
<div class="result">
  <a class="result__a" href="https://example.org/p2">Second Result</a>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=...">second snippet text</a>
</div>
</body></html>
"""


async def test_search_parses_ddg_results(fake_httpx):
    fake_httpx.set_responses(FakeResponse(text=DDG_HTML, status_code=200))
    result = await web_ops.web_search("hello world")
    assert result["status"] == "success"
    assert result["count"] == 2
    assert result["results"][0]["title"] == "Example Page One"
    # DDG 重定向链接解包
    assert result["results"][0]["url"] == "https://example.com/page1"
    assert result["results"][0]["snippet"] == "first snippet here"
    assert result["results"][1]["title"] == "Second Result"
    assert result["results"][1]["snippet"] == "second snippet text"


async def test_search_limit(fake_httpx):
    fake_httpx.set_responses(FakeResponse(text=DDG_HTML, status_code=200))
    result = await web_ops.web_search("q", limit=1)
    assert result["count"] == 1


async def test_search_appends_query_to_eq_url(fake_httpx):
    fake_httpx.set_responses(FakeResponse(text=DDG_HTML, status_code=200))
    await web_ops.web_search("hello world")
    assert fake_httpx.last_url == "https://html.duckduckgo.com/html/?q=hello%20world"


async def test_search_template_placeholder(fake_httpx, monkeypatch):
    cfg = get_config()
    saved = cfg._settings.get("web", {})
    cfg._settings["web"] = {"search_url": "https://search.example/?q={query}&hl=zh"}
    try:
        fake_httpx.set_responses(FakeResponse(text=DDG_HTML, status_code=200))
        await web_ops.web_search("hi there")
        assert fake_httpx.last_url == "https://search.example/?q=hi%20there&hl=zh"
    finally:
        cfg._settings["web"] = saved


async def test_search_params_form(fake_httpx, monkeypatch):
    """search_url 不以 = 结尾 → 走 params={'q': ...} 请求。"""
    cfg = get_config()
    saved = cfg._settings.get("web", {})
    cfg._settings["web"] = {"search_url": "https://search.example/search"}
    try:
        fake_httpx.set_responses(FakeResponse(text=DDG_HTML, status_code=200))
        await web_ops.web_search("hi")
        assert fake_httpx.last_url == "https://search.example/search"
        assert fake_httpx.last_kwargs.get("params") == {"q": "hi"}
    finally:
        cfg._settings["web"] = saved


async def test_search_network_failure_returns_string(fake_httpx):
    fake_httpx.set_exception(httpx.ConnectError("refused"))
    result = await web_ops.web_search("q")
    assert isinstance(result, str)
    assert "搜索失败" in result


async def test_search_empty_query(fake_httpx):
    result = await web_ops.web_search("")
    assert isinstance(result, str)
    assert "搜索失败" in result
