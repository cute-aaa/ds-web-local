import os
import pytest

from tools.file_ops import search_replace, read_file, write_file, line_edit, get_file_outline, list_directory


@pytest.mark.asyncio
async def test_search_replace_all(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello world\nhello again\n", encoding="utf-8")
    r = await search_replace(str(f), "hello", "HELLO", count=-1)
    assert r["replaced"] == 2
    assert f.read_text(encoding="utf-8") == "HELLO world\nHELLO again\n"


@pytest.mark.asyncio
async def test_search_replace_first_only(tmp_path):
    f = tmp_path / "b.txt"
    f.write_text("aaa aaa aaa", encoding="utf-8")
    r = await search_replace(str(f), "aaa", "bbb", count=1)
    assert r["replaced"] == 1
    assert f.read_text(encoding="utf-8") == "bbb aaa aaa"


@pytest.mark.asyncio
async def test_search_replace_not_found(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("xyz", encoding="utf-8")
    r = await search_replace(str(f), "nope", "yes")
    assert "error" in r


@pytest.mark.asyncio
async def test_write_and_read(tmp_path):
    f = tmp_path / "d.txt"
    r = await write_file(str(f), "line1\nline2\n")
    assert r["status"] == "success"
    r = await read_file(str(f))
    assert r["total_lines"] == 2
    assert "1|line1" in r["content"]


@pytest.mark.asyncio
async def test_line_edit(tmp_path):
    f = tmp_path / "e.py"
    f.write_text("def foo():\n    return 1\n", encoding="utf-8")
    edits = "<<<<<<< SEARCH\n    return 1\n=======\n    return 2\n>>>>>>> REPLACE"
    r = await line_edit(str(f), edits)
    assert r["applied"] == 1
    assert "return 2" in f.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_get_file_outline_python(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("def a():\n    pass\n\nclass B:\n    pass\n", encoding="utf-8")
    r = await get_file_outline(str(f))
    names = [o["name"] for o in r["outline"]]
    assert "a" in names and "B" in names


@pytest.mark.asyncio
async def test_list_directory(tmp_path):
    (tmp_path / "x.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    r = await list_directory(str(tmp_path))
    assert r["count"] == 2
