#!/usr/bin/env python3
"""双 profile 人工验收脚本 —— 桥接可靠性（逐调用信封 / 附件归属 / ask_user 隔离）

背景
----
DS Web Local 桥接可靠性改造（多 profile 路由 / 逐调用信封 / 附件默认落盘 / ask_user 隔离 /
幂等去重）的验收。浏览器交互部分必须由真人执行（§5.3 第 5/6 项与输入框行为），
后端契约部分本脚本用「模拟 profile A/B」直接驱动 HTTP 自动断言。

用法
----
    # 进程内 TestClient（无需启动后端，需 backend 依赖可导入）
    python scripts/acceptance_dual_profile.py

    # 连真实后端（需先启动：cd backend && python main.py，监听 127.0.0.1:8088）
    python scripts/acceptance_dual_profile.py --base-url http://127.0.0.1:8088

    # 只跑后端契约自动项（默认全部），失败返回退出码 1

自动覆盖（对照 §5.3）
    1  两个 caller 的信封回显各自 caller_id / batch_id，工具日志可按 caller 过滤（→ 1/2）
    2  A、B 各自调用互不串扰，结果各自正确（→ 2）
    3  ask_user：pending 按 caller 过滤；B 应答 A 的请求 → 403；A 应答成功（→ 3）
    4  大结果 → truncated+summary+附件；A 可读附件全文、B 读 → 403；
       模型 read_file 读附件成功且带 _external_source（→ 4）
    5  同 caller 双 tab：batch/tab 各自独立、工具日志保留 tab_id（→ 5 的后端部分）

手动仍需执行（脚本结尾会再次打印清单）
    浏览器状态点、GM_getValue('callerId') 两 profile 不同、输入框轮次清空/草稿保留、
    双标签真实并发、真实模型 read_file 往返、rolecard 回填可读性。
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

# 使 backend/ 下的一级包（core/api/tools/...）可直接导入
_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows 控制台
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

RESULTS = []  # (spec_ref, name, passed, detail)


def record(spec_ref: str, name: str, passed: bool, detail: str = "") -> bool:
    RESULTS.append((spec_ref, name, bool(passed), detail))
    tag = "PASS" if passed else "FAIL"
    print(f"  [{tag}] {name}  ({spec_ref})")
    if detail:
        print(f"         {detail}")
    return bool(passed)


def call_tool(client, tool: str, arguments: dict, **kw) -> dict:
    """单工具 /call → 返回第一个信封。kw 可传 caller_id/tab_id/batch_id/session_id。"""
    body = {"tool": tool, "arguments": arguments, **kw}
    r = client.post("/api/bridge/call", json=body)
    assert r.status_code == 200, f"/call HTTP {r.status_code}: {r.text[:300]}"
    return r.json()["results"][0]


# ----------------------------------------------------------------------------
# HTTP 客户端（进程内 TestClient 或真实后端）
# ----------------------------------------------------------------------------
class Client:
    def __init__(self, base_url=None):
        self.base_url = base_url
        self._tc = None
        self._http = None

    def __enter__(self):
        if self.base_url:
            import httpx
            self._http = httpx.Client(base_url=self.base_url, timeout=120)
            # 探活
            try:
                r = self._http.get("/health")
                assert r.status_code == 200, f"后端 /health → HTTP {r.status_code}"
            except Exception as e:
                raise SystemExit(
                    f"[错误] 无法连接后端 {self.base_url}（{e}）。"
                    f"请先启动：cd {_BACKEND} && python main.py") from e
        else:
            from fastapi.testclient import TestClient
            from main import create_app
            from tools.register import register_all_builtin_tools
            register_all_builtin_tools()  # 进程内手动注册（幂等）
            self._tc = TestClient(create_app())
            self._tc.__enter__()
        return self

    def __exit__(self, *exc):
        if self._tc:
            self._tc.__exit__(*exc)
        if self._http:
            self._http.close()

    def post(self, path, json=None):
        if self._http:
            return self._http.post(path, json=json)
        return self._tc.post(path, json=json)

    def get(self, path, params=None):
        if self._http:
            return self._http.get(path, params=params)
        return self._tc.get(path, params=params)


# ----------------------------------------------------------------------------
# 场景
# ----------------------------------------------------------------------------
def scenario_probe(client):
    """环境探测：/tools 带 idempotent、/tools 与 /rolecard 为函数调用形态。"""
    tools = client.get("/api/bridge/tools").json().get("tools", [])
    names = {t["name"] for t in tools}
    need = {"read_file", "write_file", "list_directory", "ask_user"}
    missing = need - names
    ok1 = record("§5.4", "工具清单齐备（read_file/write_file/list_directory/ask_user）",
                 not missing, f"缺失: {missing or '无'}")
    all_idem = all("idempotent" in t for t in tools)
    ok2 = record("§5.4/P1", "/tools 每个工具带 idempotent 元数据", all_idem,
                 "（幂等去重层2依赖该声明）" if all_idem else "存在缺少 idempotent 字段的工具")
    rc = client.get("/api/bridge/rolecard").json().get("rolecard", "")
    ok3 = record("§2.6", "rolecard 为函数调用形态（含附件/read_file/信封规则）",
                 ("read_file" in rc and "【工具结果" in rc) or ("read_file" in rc and "null" in rc),
                 f"rolecard {len(rc)} 字符")
    return ok1 and ok2 and ok3


def scenario_identity_and_logs(client):
    """§5.3-1/2：caller 身份回显 + 工具日志按 caller 过滤，互不串扰。"""
    tmp = Path(tempfile.mkdtemp(prefix="dsacc_id_"))
    try:
        fa, fb = tmp / "a.txt", tmp / "b.txt"
        ea = call_tool(client, "write_file", {"path": str(fa), "content": "A-OK"},
                       caller_id="accA", tab_id="tabA1", batch_id="bA_1")
        eb = call_tool(client, "write_file", {"path": str(fb), "content": "B-OK"},
                       caller_id="accB", tab_id="tabB1", batch_id="bB_1")
        ok_a = record("§5.3-1", "A 的信封回显 caller_id=accA / batch_id=bA_1",
                      ea.get("caller_id") == "accA" and ea.get("batch_id") == "bA_1",
                      f"实际: caller={ea.get('caller_id')} batch={ea.get('batch_id')}")
        ok_b = record("§5.3-1", "B 的信封回显 caller_id=accB / batch_id=bB_1",
                      eb.get("caller_id") == "accB" and eb.get("batch_id") == "bB_1",
                      f"实际: caller={eb.get('caller_id')} batch={eb.get('batch_id')}")
        # 附件目录按 caller 分属
        pa = Path(ea["file"]).as_posix()
        ok_dir = record("§2.3", f"附件落 A 目录（…/accA/<date>/result_*.json）",
                        "/accA/" in pa and ea["externalized"] is True and Path(ea["file"]).exists(),
                        ea["file"] or "")
        # 工具日志过滤
        logs_a = client.get("/api/bridge/tool_logs", params={"caller_id": "accA"}).json()["logs"]
        logs_b = client.get("/api/bridge/tool_logs", params={"caller_id": "accB"}).json()["logs"]
        ra, rb = ea["request_id"], eb["request_id"]
        ida = {e["request_id"] for e in logs_a}
        idb = {e["request_id"] for e in logs_b}
        ok_log = record("§5.3-1", "工具日志按 caller 过滤：A 只见 A、B 只见 B",
                        ra in ida and rb in idb and ra not in idb and rb not in ida,
                        f"A日志{len(idb) if False else len(logs_a)}条 / B日志{len(logs_b)}条")
        return ok_a and ok_b and ok_dir and ok_log
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def scenario_ask_user_isolation(client):
    """§5.3-3：pending 按 caller 过滤；B 代答 A 的请求 → 403；A 自答成功。"""
    token = uuid.uuid4().hex[:8]
    question = f"验收:{token}:继续吗?"
    holder = {}

    def do_ask():
        try:
            r = client.post("/api/bridge/call", json={
                "tool": "ask_user",
                "arguments": {"question": question, "timeout_sec": 30},
                "caller_id": "accA", "tab_id": "tabA1", "batch_id": "bA_ask"})
            holder["env"] = r.json()["results"][0]
        except Exception as e:  # noqa: BLE001
            holder["err"] = str(e)

    def _mine(pending, cid):
        return [p for p in pending if p.get("caller_id") == cid and p.get("question") == question]

    t = threading.Thread(target=do_ask)
    t.start()
    try:
        deadline = time.time() + 15
        pending_a = []
        while time.time() < deadline:
            all_a = client.get("/api/bridge/ask_user/pending",
                               params={"caller_id": "accA"}).json()["pending"]
            pending_a = _mine(all_a, "accA")
            if pending_a:
                break
            time.sleep(0.05)
        ok_seen = record("§5.3-3", "A 的挂起请求出现在 pending?caller_id=accA",
                         bool(pending_a), f"pending={len(pending_a)} 条")
        pending_b = client.get("/api/bridge/ask_user/pending",
                               params={"caller_id": "accB"}).json()["pending"]
        ok_isol = record("§5.3-3", "B 的 pending?caller_id=accB 看不到 A 的请求",
                         not _mine(pending_b, "accA"),
                         f"B pending={len(pending_b)} 条")
        rid = pending_a[0]["request_id"]
        # B 强行代答 → 403
        r = client.post("/api/bridge/ask_user", json={
            "request_id": rid, "answer": "否", "caller_id": "accB"})
        ok_403 = record("§5.3-3", "B 用 A 的 request_id 应答 → HTTP 403",
                        r.status_code == 403, f"实际 HTTP {r.status_code}")
        # A 正常应答
        r2 = client.post("/api/bridge/ask_user", json={
            "request_id": rid, "answer": "是", "caller_id": "accA"})
        ok_ans = record("§5.3-3", "A 应答自己的请求 → answered",
                        r2.status_code == 200 and r2.json().get("status") == "answered",
                        json.dumps(r2.json(), ensure_ascii=False)[:200])
        t.join(timeout=15)
        env = holder.get("env") or {}
        ok_result = record("§5.3-3", "A 的 /call 挂起解除且信封结果为应答内容",
                           "err" not in holder and env.get("status") == "ok"
                           and env.get("result") == "是",
                           f"status={env.get('status')} result={env.get('result')}")
        # 清理：清除 accA 遗留的本批挂起（应对中断重跑，避免污染下次运行）
        for p in client.get("/api/bridge/ask_user/pending",
                            params={"caller_id": "accA"}).json()["pending"]:
            if p.get("caller_id") == "accA" and p.get("request_id") != rid:
                client.post("/api/bridge/ask_user",
                            json={"request_id": p["request_id"], "answer": "是",
                                  "caller_id": "accA"})
        return ok_seen and ok_isol and ok_403 and ok_ans and ok_result
    finally:
        t.join(timeout=5)


def scenario_attachments_and_big_result(client):
    """§5.3-4：大结果 truncated+summary+附件；归属读取/403；read_file 附件全量。"""
    tmp = Path(tempfile.mkdtemp(prefix="dsacc_big_"))
    try:
        big = tmp / "big.txt"
        marker = "ACCEPT-BIG-"
        big.write_text(marker * 5000, encoding="utf-8")  # 60KB > 20KB 阈值

        # A 读大文件 → 信封 truncated 预览 + 附件路径
        env = call_tool(client, "read_file", {"path": str(big)},
                        caller_id="accA", tab_id="tabA1", batch_id="bA_big")
        ok_env = record("§5.3-4/§2.2", "A 的大结果信封 truncated+summary+file",
                        env.get("status") == "ok" and env.get("truncated") is True
                        and env.get("externalized") is True
                        and bool(env.get("summary")) and bool(env.get("file")),
                        f"truncated={env.get('truncated')} summary_len={len(env.get('summary') or '')}")
        fpath = env["file"]
        ok_disk = record("§5.3-4/§2.3", "附件文件真实存在且内含完整内容（非预览）",
                         fpath and Path(fpath).exists() and marker in Path(fpath).read_text(encoding="utf-8"),
                         str(fpath))

        # 附件归属：A 自读 200 / B 跨读 403
        rid = env["request_id"]
        r_own = client.get("/api/bridge/attachments",
                           params={"caller_id": "accA", "request_id": rid})
        ok_own = record("§5.3-4", "A 经 /api/bridge/attachments 读自己附件 → 200",
                        r_own.status_code == 200 and marker in r_own.json().get("content", ""),
                        f"HTTP {r_own.status_code}")
        r_for = client.get("/api/bridge/attachments",
                           params={"caller_id": "accB", "request_id": rid})
        ok_for = record("§5.3-4", "B 经 /api/bridge/attachments 跨 caller 读 A 附件 → 403",
                        r_for.status_code == 403, f"HTTP {r_for.status_code}")

        # 模型用 read_file 读该附件：完整返回 + _external_source（不二次外置/死循环）
        env2 = call_tool(client, "read_file", {"path": fpath},
                         caller_id="accA", tab_id="tabA1", batch_id="bA_big2")
        res2 = env2.get("result") or {}
        ok_read = record("§5.3-4/§2.5.3", "模型 read_file(附件) 成功：_external_source=True 且不再截断",
                         env2.get("status") == "ok"
                         and res2.get("_external_source") is True
                         and env2.get("truncated") is not True
                         and marker in res2.get("content", ""),
                         f"status={env2.get('status')} _external_source={res2.get('_external_source')}")
        return ok_env and ok_disk and ok_own and ok_for and ok_read
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def scenario_same_caller_two_tabs(client):
    """§5.3-5（后端部分）：同 caller 双 tab 批次独立、日志保留 tab_id。"""
    tmp = Path(tempfile.mkdtemp(prefix="dsacc_tab_"))
    try:
        f1, f2 = tmp / "t1.txt", tmp / "t2.txt"
        e1 = call_tool(client, "write_file", {"path": str(f1), "content": "tab1"},
                       caller_id="accA", tab_id="tabA1", batch_id="b1")
        e2 = call_tool(client, "write_file", {"path": str(f2), "content": "tab2"},
                       caller_id="accA", tab_id="tabA2", batch_id="b2")
        ok_env = record("§5.3-5", "同 caller 两个批次各自返回独立 request_id/batch_id",
                        e1["request_id"] != e2["request_id"]
                        and e1["batch_id"] == "b1" and e2["batch_id"] == "b2"
                        and e1["caller_id"] == e2["caller_id"] == "accA",
                        f"rid1={e1['request_id']} rid2={e2['request_id']}")
        logs = client.get("/api/bridge/tool_logs",
                          params={"caller_id": "accA"}).json()["logs"]
        tabs = {e["tab_id"] for e in logs if e["request_id"] in (e1["request_id"], e2["request_id"])}
        ok_log = record("§5.3-5", "工具日志区分 tab（tabA1 / tabA2）",
                        tabs == {"tabA1", "tabA2"}, f"tabs={tabs}")
        return ok_env and ok_log
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


MANUAL_CHECKLIST = """
================ 手动浏览器清单（无法自动化的部分，请在两个真实 profile 执行） ================

准备：普通浏览器 profile A + 无痕/第二用户 profile B，各自安装最新打包脚本
      （bridge/ds-bridge.user.js），同一后端运行中（python backend/main.py）。

§5.3-1 身份与状态点
  [ ] A 打开 DeepSeek 新建对话、B 打开 MiMo（或第二个 DeepSeek 账号）
  [ ] 两页右上状态圆点均绿（已连后端）
  [ ] 控制台 GM_getValue('callerId')：A 与 B 取值不同且各自稳定（刷新不变）
  [ ] 后端控制台「工具日志」页按 caller 下拉可分别筛出 A / B 记录

§5.3-2 真实并行
  [ ] A 让模型调 list_directory("G:/")，同时 B 让模型调 read_file("G:/a.txt")
  [ ] 两侧各自收到结果、回填互不包含对方内容（无互串）

§5.3-3 ask_user / 审批（真实浏览器）
  [ ] A 触发需审批工具（terminal）→ B 页面不弹确认框、轮询无该 pending
  [ ] （可选构造请求）B 强行带 A 的 request_id 应答 → 后端 403

§5.3-4 大结果（真实模型往返）
  [ ] A 请求长文件 → 回填为 summary+file 两段说明（非几十 KB 全量），模型能读出"路径"
  [ ] A 要求模型用 read_file(<file 路径>) 读取 → 成功拿到完整内容
  [ ] B 的模型不知道 A 的 file 路径 → 无法经 /api/bridge/attachments 读 A 附件（403）

§5.3-5 同 profile 双标签
  [ ] A 开两个 DeepSeek 标签同时对话 → 各自独立回填、互不覆盖对方输入框

§5.3-6 输入框轮次/草稿回归
  [ ] 单 profile 连续两轮工具调用：第一轮结果已发送后，第二轮输入框无上一轮残留
  [ ] 单轮内 3 个调用回填为 3 段，每段带 #<request_id> 与 @@tool 工具名 {参数} 回显
  [ ] 用户手动输入草稿（无工具标记）后触发新工具调用 → 草稿不被清空
  [ ] 空结果（返回 null 的工具）回填为显式「返回 null（成功、无内容）」，流程不卡死

§5.4 文档一致性抽查
  [ ] docs/contracts.md 契约 A 信封示例与本机 /call 实测一致（字段、status∈{ok,error,approval_required}）
  [ ] docs/api.md 中 /api/bridge/attachments、/tool_logs?caller_id=、/ask_user 示例可复现
"""


def main():
    ap = argparse.ArgumentParser(description="DS Web Local 桥接可靠性双 profile 验收脚本（§5.3）")
    ap.add_argument("--base-url", default=None,
                    help="真实后端地址（缺省用进程内 TestClient）")
    args = ap.parse_args()

    print("=" * 72)
    print("DS Web Local · 桥接可靠性验收")
    print(("模式: 真实后端 " + args.base_url) if args.base_url else "模式: 进程内 TestClient（免启动后端）")
    print("=" * 72)

    with Client(args.base_url) as client:
        scenario_probe(client)
        scenario_identity_and_logs(client)
        scenario_ask_user_isolation(client)
        scenario_attachments_and_big_result(client)
        scenario_same_caller_two_tabs(client)

    print("\n---------------- 自动验收汇总 ----------------")
    n_pass = sum(1 for *_h, p in RESULTS if p)
    n_fail = len(RESULTS) - n_pass
    for ref in sorted({r[0] for r in RESULTS}):
        sub = [r for r in RESULTS if r[0] == ref]
        ok = sum(1 for r in sub if r[2])
        print(f"  {ref}: {ok}/{len(sub)} 通过")
    print(f"\n  总计: {n_pass}/{len(RESULTS)} 通过, {n_fail} 失败")
    print(MANUAL_CHECKLIST)
    print("（自动项通过 ≠ 验收完成：请按上面手动清单在真实双 profile 中逐项勾选，"))
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
