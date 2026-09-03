"""临时验证：编辑 backend/config/rolecard.md 后，generate_role_card() 是否立刻反映改动。
验证完自动还原文件。"""
import os
import time

from rolecard.generator import _template_file, generate_role_card

p = _template_file()
orig = p.read_text(encoding="utf-8")
try:
    before = "HOT_RELOAD_PROBE_MARK" in generate_role_card()
    p.write_text(orig + "\n<!-- HOT_RELOAD_PROBE_MARK -->\n", encoding="utf-8")
    future = time.time() + 10
    os.utime(p, (future, future))          # 确保 mtime 变化
    after = "HOT_RELOAD_PROBE_MARK" in generate_role_card()
    print("模板文件 :", p)
    print("编辑前包含标记 :", before)
    print("编辑后包含标记 :", after)
    print("热重载生效 :", (not before) and after)
finally:
    p.write_text(orig, encoding="utf-8")
    print("已还原原文件 :", p.read_text(encoding="utf-8") == orig)
