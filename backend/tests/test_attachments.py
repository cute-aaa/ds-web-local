"""附件 .md 化测试：每次调用除 .json 外再落盘一份 Markdown（模型阅读/人工查看友好）。"""
import core.attachments as att
from core.attachments import (
    render_envelope_markdown,
    persist_attachment,
    persist_attachment_md,
)


def _env(status="ok", result=None, error=None):
    return {
        "request_id": "r_test0001",
        "tool": "read_file",
        "caller_id": "c_test",
        "arguments": {"path": "C:/a.txt"},
        "status": status,
        "result": result,
        "error": error,
        "elapsed_ms": 12,
    }


def test_md_ok_string_result():
    md = render_envelope_markdown(_env(result="hello"))
    assert "# 工具调用结果 · read_file" in md
    assert "@@tool read_file {\"path\": \"C:/a.txt\"}" in md   # 原始调用可复制重试
    assert "hello" in md
    assert "## 结果" in md


def test_md_null_result_explicit():
    """null 必须显式说明是成功无内容，否则模型会误判为失败而重复调用。"""
    md = render_envelope_markdown(_env(result=None))
    assert "null" in md
    assert "## 结果" in md


def test_md_error_envelope():
    md = render_envelope_markdown(
        _env(status="error", error={"type": "ValueError", "message": "boom"}))
    assert "## 错误" in md
    assert "ValueError" in md
    assert "boom" in md
    assert "## 结果" not in md


def test_md_dict_result_wrapped_in_code_block():
    """非字符串结果用 json 代码块包裹，避免 Markdown 结构被结果内容破坏。"""
    md = render_envelope_markdown(_env(result={"a": 1}))
    assert "```json" in md
    assert '"a": 1' in md


def test_md_approval_hint():
    env = _env(status="approval_required")
    env["approval_required"] = True   # 与 api/bridge.py 构造的信封字段一致
    md = render_envelope_markdown(env)
    assert "人工审批" in md
    assert "approval_required" in md   # 非 ok 无 error 也要有交代，不能留空


def test_persist_md_alongside_json(tmp_path, monkeypatch):
    """.md 与 .json 落在同一目录、同名不同后缀。"""
    monkeypatch.setattr(att, "attachments_root", lambda: tmp_path)
    env = _env(result="hello world")
    pj = persist_attachment("c_test", env)
    pm = persist_attachment_md("c_test", env)
    assert pj and pm
    assert pj.endswith("result_r_test0001.json")
    assert pm.endswith("result_r_test0001.md")
    text = open(pm, encoding="utf-8").read()
    assert "hello world" in text
    assert "# 工具调用结果" in text


def test_persist_md_failure_is_silent(tmp_path, monkeypatch):
    """落盘失败不得阻断主流程（返回 None，由上层降级）。"""
    monkeypatch.setattr(att, "attachments_root", lambda: tmp_path / "no" / "such")
    monkeypatch.setattr(att, "attachments_root", lambda: tmp_path / "file_not_dir")
    (tmp_path / "file_not_dir").write_text("x", encoding="utf-8")   # 路径不是目录 → mkdir 失败
    assert persist_attachment_md("c_test", _env()) is None
