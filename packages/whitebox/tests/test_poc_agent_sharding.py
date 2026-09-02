# packages/whitebox/tests/test_poc_agent_sharding.py
"""poc-agent 聚类分片（2026-09-02 NodeGoat-20260902-045436 根因修复）。

一锅端 14 卡 = 90KB prompt + 10万 token 级请求（GLM ServerOverloaded 时段
最先被丢）+ 10-20K token 输出（超单响应上限截断 → 模型按 prompt 指令写脚本
自救 → 脚本再截断 → "Invalid JSON input"），4 次启动 0 交付（~$0.4 白烧）。
分片设计（对齐 chain-verdict 逐条模式）：

- 按 sink 文件聚类：同文件卡共享读码（路由注册/handler/middleware 文件级
  复用），每片 ≤ SUPERNOVA_POC_SHARD_MAX_CARDS（默认 3）张，装不下同文件
  裂多片；sink 文件提取优先级：sink_call/sink_function 字符串 regex
  （LLM 轨卡「file:line」形态）→ dataflow_steps 末步 file（GN 卡）→
  unknown 桶按序切（authz 等非 taint 卡无 sink 概念）。
- 片间共享 Semaphore（SUPERNOVA_POC_AGENT_CONCURRENCY 默认 3，跨类共享
  限流——防 5 类 × N 片并发叠加反而放大 429 暴露面）。
- agent_name poc-agent-{vc}-{序号}；prompt 只塞该片卡 + repo 路径直供
  （消掉每 agent 开场 find / 找仓的空转）。
- 片级诚实缺失（一片失败不炸类），类级 gather 后统一写回 queue 一次
  （单点写盘，片间无竞争）。
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

from supernova_whitebox.pipeline import activities


class _FakeInput:
    def __init__(self, tmp_path, web_url=""):
        self.agent_name = None
        self.web_url = web_url
        self.repo_path = str(tmp_path)
        self.deliverables_subdir = None
        self.workspace_name = None
        self.workspace_path = None
        self.config_path = None
        self.api_key = None
        self.pipeline_testing_mode = False
        self.prompt_override = None
        self.provider_config = None
        self.vuln_classes = None


def _wb(tmp_path):
    d = tmp_path / "deliverables" / "whitebox"
    (d / "intermediate").mkdir(parents=True, exist_ok=True)
    return d


def _write_queue(d, vulns, name="xss_exploitation_queue.json"):
    d.joinpath("intermediate", name).write_text(
        json.dumps({"vulnerabilities": vulns}))


def _read_queue(d, name="xss_exploitation_queue.json"):
    return json.loads(d.joinpath("intermediate", name).read_text(encoding="utf-8"))


def _agent_result(payload):
    return SimpleNamespace(structured_output=payload, text=None, success=True)


_VULN = {"ID": "XSS-VULN-01", "vulnerability_type": "Stored",
         "externally_exploitable": True, "confidence": "high",
         "merge_source": "llm-only", "title": "t", "severity": "high"}


def _card(cid, sink_call=None, sink_function=None, dataflow_steps=None):
    """聚类函数的输入是 queue pydantic 模型——用 SimpleNamespace 近似字段面。"""
    return SimpleNamespace(ID=cid, sink_call=sink_call,
                           sink_function=sink_function,
                           dataflow_steps=dataflow_steps)


def _step(file):
    return {"label": "s", "file": file, "line": 1, "protection": None}


# ── _group_poc_targets：纯函数聚类 ──────────────────────────────────────────

def test_groups_same_sink_file_into_one_shard():
    """同 sink 文件的卡聚同片（读码复用的核心收益）。"""
    cards = [_card("A", dataflow_steps=[_step("app/views/session.js")]),
             _card("B", dataflow_steps=[_step("app/views/session.js")]),
             _card("C", dataflow_steps=[_step("app/views/session.js")])]
    shards = activities._group_poc_targets(cards, max_per_shard=3)
    assert [[c.ID for c in s] for s in shards] == [["A", "B", "C"]]


def test_different_files_get_different_shards():
    cards = [_card("A", dataflow_steps=[_step("a.js")]),
             _card("B", dataflow_steps=[_step("b.js")])]
    shards = activities._group_poc_targets(cards, max_per_shard=3)
    assert [[c.ID for c in s] for s in shards] == [["A"], ["B"]]


def test_same_file_exceeding_max_splits():
    """同文件卡数超片上限 → 裂多片（防「文件级一锅端」回潮）。"""
    cards = [_card(i, dataflow_steps=[_step("session.js")])
             for i in ["A", "B", "C", "D", "E"]]
    shards = activities._group_poc_targets(cards, max_per_shard=3)
    assert [[c.ID for c in s] for s in shards] == [["A", "B", "C"], ["D", "E"]]


def test_sink_string_regex_preferred_over_dataflow_tail():
    """LLM 轨 XSS 卡真实形态：sink_function 带「file:line」指向模板 sink 终点，
    dataflow 末步 file 是渲染入口（server.js）——regex 命中优先（login.html
    与 login.html 同片，不落进 server.js 桶）。"""
    a = _card("A", sink_function='login.html:110 value="{{userName}}"（swig 插值）',
              dataflow_steps=[_step("server.js")])
    b = _card("B", sink_function='login.html:220 value="{{email}}"',
              dataflow_steps=[_step("server.js")])
    c = _card("C", sink_function='benefits.html:50 {{firstName}}',
              dataflow_steps=[_step("server.js")])
    shards = activities._group_poc_targets([a, b, c], max_per_shard=3)
    assert [[x.ID for x in s] for s in shards] == [["A", "B"], ["C"]]


def test_sink_call_string_regex():
    """inj 卡真实形态：sink_call="eval() — app/routes/contributions.js:32-34"
    （文件在字符串中后部，非开头）。"""
    a = _card("A", sink_call="eval() — app/routes/contributions.js:32-34")
    b = _card("B", sink_call="eval() — app/routes/contributions.js:40")
    c = _card("C", sink_call="find({$where: ...}) — app/data/allocations-dao.js:9")
    shards = activities._group_poc_targets([a, b, c], max_per_shard=3)
    assert [[x.ID for x in s] for s in shards] == [["A", "B"], ["C"]]


def test_no_sink_info_chunks_sequentially():
    """authz 等非 taint 卡无 sink 字段 → unknown 桶按序切（同类 authz 卡
    共享 handler/middleware 读码，按序聚片恰好合理）。"""
    cards = [_card(f"A{i}") for i in range(5)]
    shards = activities._group_poc_targets(cards, max_per_shard=2)
    assert [[c.ID for c in s] for s in shards] == [
        ["A0", "A1"], ["A2", "A3"], ["A4"]]


def test_empty_targets_returns_empty():
    assert activities._group_poc_targets([], max_per_shard=3) == []


def test_stable_order_within_shards():
    ids = ["c1", "c2", "c3", "c4"]
    cards = [_card(i, dataflow_steps=[_step("x.js")]) for i in ids]
    shards = activities._group_poc_targets(cards, max_per_shard=3)
    flat = [c.ID for s in shards for c in s]
    assert flat == ids


# ── _write_agent_pocs：分片编排行为 ────────────────────────────────────────

def _shard_vulns(files):
    """按 sink 文件序列造卡（dataflow_steps 末步 = sink 文件）。"""
    out = []
    for i, f in enumerate(files, 1):
        out.append({**_VULN, "ID": f"XSS-VULN-{i:02d}",
                    "dataflow_steps": [_step(f)]})
    return out


def _fake_agent_factory(all_ids, calls, fail_ids=(), delay=0.0):
    """按 prompt 内容路由：命中哪些卡 ID 就返回哪些卡的 pocs；fail_ids 命中即抛。"""
    async def fake_agent(**kwargs):
        calls.append(kwargs)
        prompt = kwargs["prompt"]
        hit = [i for i in all_ids if f'"{i}"' in prompt]
        if any(f'"{i}"' in prompt for i in fail_ids):
            raise RuntimeError("shard agent down")
        if delay:
            await asyncio.sleep(delay)
        return _agent_result({"pocs": [
            {"vulnerability_id": i, "curl": f"curl 'http://T/{i}'",
             "self_check": "pass"} for i in hit]})
    return fake_agent


async def test_multi_shard_agents_and_names(tmp_path, monkeypatch):
    """6 卡 2 sink 文件（3+3）→ 2 个 agent、名带序号、各 prompt 只含该片。"""
    d = _wb(tmp_path)
    vulns = _shard_vulns(["session.js"] * 3 + ["benefits.js"] * 3)
    _write_queue(d, vulns)
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    monkeypatch.delenv("SUPERNOVA_POC_SHARD_MAX_CARDS", raising=False)
    all_ids = [v["ID"] for v in vulns]
    calls = []
    with patch.object(activities, "run_gitnexus_verdict_agent",
                      side_effect=_fake_agent_factory(all_ids, calls)):
        written = await activities._write_agent_pocs(_FakeInput(tmp_path), d)
    assert len(calls) == 2
    assert {c["agent_name"] for c in calls} == {"poc-agent-xss-01",
                                                "poc-agent-xss-02"}
    # 各片 prompt 只含自己那 3 张卡
    for c in calls:
        hit = [i for i in all_ids if f'"{i}"' in c["prompt"]]
        assert len(hit) == 3, "片 prompt 应只含该片卡"
    assert sorted(written) == sorted(all_ids)
    # 统一写回：6 卡全部落盘
    assert all(v.get("report_poc") for v in _read_queue(d)["vulnerabilities"])


async def test_single_card_shard_still_numbered(tmp_path, monkeypatch):
    d = _wb(tmp_path)
    _write_queue(d, [dict(_VULN)])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    calls = []
    with patch.object(activities, "run_gitnexus_verdict_agent",
                      side_effect=_fake_agent_factory(["XSS-VULN-01"], calls)):
        await activities._write_agent_pocs(_FakeInput(tmp_path), d)
    assert [c["agent_name"] for c in calls] == ["poc-agent-xss-01"]


async def test_shard_failure_isolated(tmp_path, monkeypatch):
    """一片失败（agent 抛异常）→ 该片诚实缺失，另一片照常写回，整体不抛。"""
    d = _wb(tmp_path)
    vulns = _shard_vulns(["session.js"] * 2 + ["benefits.js"] * 2)
    _write_queue(d, vulns)
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    all_ids = [v["ID"] for v in vulns]
    fail_ids = {v["ID"] for v in vulns if "session" in
                v["dataflow_steps"][0]["file"]}
    calls = []
    with patch.object(activities, "run_gitnexus_verdict_agent",
                      side_effect=_fake_agent_factory(all_ids, calls,
                                                       fail_ids=fail_ids)):
        written = await activities._write_agent_pocs(_FakeInput(tmp_path), d)
    ok_ids = [i for i in all_ids if i not in fail_ids]
    assert sorted(written) == sorted(ok_ids)
    by_id = {v["ID"]: v for v in _read_queue(d)["vulnerabilities"]}
    assert all(by_id[i].get("report_poc") for i in ok_ids)
    assert all(not by_id[i].get("report_poc") for i in fail_ids)


async def test_shard_max_env_overrides(tmp_path, monkeypatch):
    """SUPERNOVA_POC_SHARD_MAX_CARDS=1 → 3 张同文件卡也逐卡分片（大仓可调）。"""
    d = _wb(tmp_path)
    vulns = _shard_vulns(["session.js"] * 3)
    _write_queue(d, vulns)
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    monkeypatch.setenv("SUPERNOVA_POC_SHARD_MAX_CARDS", "1")
    all_ids = [v["ID"] for v in vulns]
    calls = []
    with patch.object(activities, "run_gitnexus_verdict_agent",
                      side_effect=_fake_agent_factory(all_ids, calls)):
        await activities._write_agent_pocs(_FakeInput(tmp_path), d)
    assert len(calls) == 3


async def test_shared_semaphore_caps_concurrency(tmp_path, monkeypatch):
    """SUPERNOVA_POC_AGENT_CONCURRENCY=2：6 片（不同文件）同时跑，峰值并发 ≤2
    ——类间+片间共享限流，防并发叠加放大 429 暴露面。"""
    d = _wb(tmp_path)
    vulns = _shard_vulns([f"f{i}.js" for i in range(6)])
    _write_queue(d, vulns)
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    monkeypatch.setenv("SUPERNOVA_POC_AGENT_CONCURRENCY", "2")
    monkeypatch.delenv("SUPERNOVA_POC_SHARD_MAX_CARDS", raising=False)
    all_ids = [v["ID"] for v in vulns]
    state = {"active": 0, "peak": 0}

    async def tracked_agent(**kwargs):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.05)
        state["active"] -= 1
        hit = [i for i in all_ids if f'"{i}"' in kwargs["prompt"]]
        return _agent_result({"pocs": [
            {"vulnerability_id": i, "self_check": "pass"} for i in hit]})

    with patch.object(activities, "run_gitnexus_verdict_agent",
                      side_effect=tracked_agent):
        await activities._write_agent_pocs(_FakeInput(tmp_path), d)
    assert state["peak"] <= 2


async def test_prompt_includes_repo_root(tmp_path, monkeypatch):
    """repo 路径直供：prompt 带绝对路径，消掉每 agent 开场 find / 找仓空转。"""
    d = _wb(tmp_path)
    _write_queue(d, [dict(_VULN)])
    monkeypatch.setattr(activities, "_get_paths",
                        lambda inp: (tmp_path, d, tmp_path))
    calls = []
    with patch.object(activities, "run_gitnexus_verdict_agent",
                      side_effect=_fake_agent_factory(["XSS-VULN-01"], calls)):
        await activities._write_agent_pocs(_FakeInput(tmp_path), d)
    assert str(tmp_path) in calls[0]["prompt"]
