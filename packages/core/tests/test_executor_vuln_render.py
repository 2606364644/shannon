"""executor vuln 双通道 e2e:host-rendered analysis md(collector+renderer)
+ exploitation_queue.json(structured_output)在**同一次** execute() run 中并存落盘。

Plan 3 Task 5 核心验证点:vuln agent 同 run 既要 collector 通道(analysis md)又要
structured_output 通道(queue.json)——双通道独立、不互斥。这是 Plan 1 executor 接线的
不变量(Task 7 落地),本测试为 vuln agent 锁定该不变量。

mock 策略(不改 executor.py / collectors / renderers / prompts):
- run_claude_prompt → fake_run:从 kwargs 取 executor 内部 make_collector(INJECTION_VULN)
  建的 vuln collector,调 collector.set_section(...) 模拟 agent 经 set_* 工具注入 payload。
  返回带全字段的 FakeResult(executor 读 success/turns/cost/cost_currency/text/model/
  structured_output/stop_reason/error/retryable/error_code/tokens.*)。
- GitManager 三个异步方法 → asyncio.sleep(0)(对齐 sibling test_executor_collector_render)。
- PromptManager.load_sync → "PROMPT" 字面量。
- make_collector / render_deliverable / atomic_write_json / validate_deliverable 全部走真实代码
  (这是 e2e:我们要证明的真实接入点就是这些)。

另验:skipped section → placeholder(host-render robustness):fake_run 调 0 个 set_* →
md 仍写盘(skipped section 渲染为 placeholder)、execute() 不抛 Missing-deliverable。这证明
host-render 路径消除了 Plan 3 的动机——「agent success 但没写 md」(§§ pre-recon-md-
deliverable-glm-forget-write)。

§1 双轨铁律:本测试零 GitNexus / 确定性层 import。
"""
import asyncio
import json

import pytest


@pytest.fixture(autouse=True)
def _en_lang_default(monkeypatch):
    """断言基于英文渲染（对齐 renderers/test_render_deliverable_exploit.py 的 en 默认）。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")


# ── FakeResult:覆盖 executor 读取的全部字段 ───────────────────────────
class FakeResult:
    """对齐 AgentExecutionResult 字段(executor.py:191-202 + is_spending_cap 早退)。"""

    def __init__(self, structured_output):
        self.success = True
        self.turns = 3                 # >2 → is_spending_cap_behavior 早退 False
        self.cost = 0.0
        self.cost_currency = "USD"
        self.text = "done"             # 非 spending-cap 关键词
        self.model = "stub-model"
        self.structured_output = structured_output
        self.stop_reason = "end_turn"
        self.error = None
        self.retryable = True
        self.error_code = None

        class _T:
            input_tokens = 10
            output_tokens = 5
            cache_read_input_tokens = 0
            cache_creation_input_tokens = 0

        self.tokens = _T()


def _patch_executor_env(monkeypatch, tmp_path):
    """executor 外部依赖打桩:GitManager / PromptManager。

    make_collector / render_deliverable / atomic_write_json / validate_deliverable
    全部走真实代码(这是 e2e 验证目标)。
    """
    from supernova_core.agents import executor as exec_mod

    monkeypatch.setattr(
        exec_mod.GitManager, "ensure_repository",
        classmethod(lambda cls, p: asyncio.sleep(0)),
    )
    monkeypatch.setattr(
        exec_mod.GitManager, "create_checkpoint",
        lambda *a, **k: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        exec_mod.GitManager, "commit",
        lambda *a, **k: asyncio.sleep(0),
    )

    class StubPM:
        def load_sync(self, *a, **kw):
            return "PROMPT"

    return exec_mod.AgentExecutor(prompt_manager=StubPM())


@pytest.mark.asyncio
async def test_injection_vuln_renders_analysis_md_and_queue_both_written(monkeypatch, tmp_path):
    """双通道同 run:analysis md(collector→renderer)+ exploitation_queue.json
    (structured_output)都在一次 execute() 中落盘——Plan 3 核心不变量。"""
    from supernova_core.agents import executor as exec_mod
    from supernova_core.models.agents import AgentName

    repo = tmp_path / "repo"
    repo.mkdir()
    deliverables = tmp_path / "deliverables"

    queue_payload = {
        "verdicts": [
            {
                "vulnerability_id": "INJ-1",
                "status": "false_positive",
                "reason": "parameterized query",
                "evidence": "evidence text",
            }
        ]
    }

    captured: dict = {}

    async def fake_run(**kw):
        collector = kw.get("collector")
        captured["collector_passed"] = collector is not None
        # 模拟 agent 经 set_* 工具注入 payload —— tool_name 与 vuln collector 注册一致
        # (collectors/vuln.py::make_vuln_sections)。
        if collector is not None:
            collector.set_section(
                "set_findings_summary",
                {"key_outcome": "sqli found", "patterns": []},
            )
        return FakeResult(structured_output=queue_payload)

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    executor = _patch_executor_env(monkeypatch, tmp_path)

    await executor.execute(
        agent_name=AgentName.INJECTION_VULN,
        repo_path=str(repo),
        deliverables_path=str(deliverables),
        structured_output_schema={"type": "object"},
    )

    # ── 不变量 1:executor 把 make_collector 建的 vuln collector 透传给 run_claude_prompt
    assert captured["collector_passed"] is True

    # ── 不变量 2:analysis md(host 渲染通道)落盘 + 含 set_findings_summary 注入内容
    md_file = deliverables / "injection_analysis_deliverable.md"
    assert md_file.exists(), (
        "host-rendered analysis md missing — dual-channel md 通道断"
    )
    md = md_file.read_text(encoding="utf-8")
    assert "# Injection Analysis Report" in md
    assert "## 1. Executive Summary" in md
    assert "sqli found" in md           # collector payload 经 renderer 流到 md

    # ── 不变量 3:exploitation_queue.json(structured_output 通道)落盘 + 内容 round-trip
    queue_file = deliverables / "injection_exploitation_queue.json"
    assert queue_file.exists(), (
        "exploitation_queue.json missing — dual-channel structured_output 通道断"
    )
    persisted = json.loads(queue_file.read_text(encoding="utf-8"))
    assert persisted == queue_payload

    # ── 不变量 4:skipped section(本 run 未调 set_strategic_intelligence /
    # set_safe_vectors / set_blind_spots)→ placeholder(host-render robustness)
    assert "set_strategic_intelligence` was not called" in md
    assert "set_safe_vectors` was not called" in md
    assert "set_blind_spots` was not called" in md


@pytest.mark.asyncio
async def test_injection_vuln_skipped_all_sections_md_written_no_raise(monkeypatch, tmp_path):
    """skipped → placeholder:host-render 消除「agent success 但没写 md」失败模式。

    fake_run 不调任何 set_* 工具(模拟 GLM 长任务后失忆,end_turn 没经结构化工具通道)。
    预期:execute() 不抛 Missing-deliverable,md 仍写盘(所有 section placeholder),
    queue.json 照常写。这是 Plan 3 的整个动机——self-Write 路径下 GLM 失忆丢 Write
    会触发 Missing-deliverable 重跑;host-render 把 md 渲染从 agent 行为解耦。
    """
    from supernova_core.agents import executor as exec_mod
    from supernova_core.models.agents import AgentName
    from supernova_core.models.errors import PentestError

    repo = tmp_path / "repo"
    repo.mkdir()
    deliverables = tmp_path / "deliverables"

    queue_payload = {"verdicts": []}

    async def fake_run(**kw):
        # 故意不调任何 set_* —— 模拟 agent 失忆
        return FakeResult(structured_output=queue_payload)

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    executor = _patch_executor_env(monkeypatch, tmp_path)

    # execute() 不应抛 —— host-render 把 md 写盘与 agent set_* 调用解耦
    try:
        await executor.execute(
            agent_name=AgentName.INJECTION_VULN,
            repo_path=str(repo),
            deliverables_path=str(deliverables),
            structured_output_schema={"type": "object"},
        )
    except PentestError as e:
        pytest.fail(
            f"execute() raised on skipped-all-sections run — host-render robustness 断: "
            f"{e}"
        )

    md_file = deliverables / "injection_analysis_deliverable.md"
    assert md_file.exists(), "skipped-all run: md 应仍落盘(全 section placeholder)"
    md = md_file.read_text(encoding="utf-8")
    # 4 个 section 全 skipped → 4 个 placeholder(strategic_intelligence / safe_vectors /
    # blind_spots 在 _executive_summary / _dominant_patterns 共享 findings_summary,故
    # findings_summary 缺会触发 §1/§2 placeholder;§3/§4/§5 各自 placeholder)。
    assert "set_findings_summary` was not called" in md
    assert "set_strategic_intelligence` was not called" in md
    assert "set_safe_vectors` was not called" in md
    assert "set_blind_spots` was not called" in md

    # queue 通道不受 collector 通道影响(双通道独立)
    queue_file = deliverables / "injection_exploitation_queue.json"
    assert queue_file.exists()
    assert json.loads(queue_file.read_text(encoding="utf-8")) == queue_payload


@pytest.mark.asyncio
async def test_injection_exploit_renders_evidence_using_queue_root(monkeypatch, tmp_path):
    """黑盒根因修复 e2e（spec 2026-08-08）：exploit agent 经真实 AgentExecutor 渲染 evidence，
    queue_root（= 白盒根）透传到 render_deliverable → valid_ids 来自 queue_root/whitebox/ →
    真实 verdict 进 accepted → evidence.md 含该 ID。证明 queue_root 经 executor → renderer 全链透传
    （现场回归点：修复前 deliverables_path 空目录 → valid_ids 空 → 真实 verdict 全被 L2 拒）。"""
    from supernova_core.agents import executor as exec_mod
    from supernova_core.models.agents import AgentName

    repo = tmp_path / "repo"
    repo.mkdir()
    deliverables = tmp_path / "deliverables"  # 黑盒产物落点（空，无 queue）

    queue_root = tmp_path / "whitebox-root"  # 白盒根：queue 在 whitebox/ 子目录
    (queue_root / "whitebox").mkdir(parents=True)
    (queue_root / "whitebox" / "injection_exploitation_queue.json").write_text(json.dumps(
        {"vulnerabilities": [
            {"ID": "INJ-VULN-01", "vulnerability_type": "SQLi",
             "externally_exploitable": True, "confidence": "high"}]}))

    async def fake_run(**kw):
        collector = kw.get("collector")
        # 模拟 agent 经 add_exploit 工具注入 verdict（vulnerability_id 在 queue 中）
        if collector is not None:
            collector.append_section("add_exploit", {
                "vulnerability_id": "INJ-VULN-01", "status": "exploited", "severity": "critical",
                "impact": "i", "exploitation_steps": ["s"], "proof_of_impact": "p"})
        return FakeResult(structured_output=None)

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    executor = _patch_executor_env(monkeypatch, tmp_path)

    await executor.execute(
        agent_name=AgentName.INJECTION_EXPLOIT,
        repo_path=str(repo),
        deliverables_path=str(deliverables),
        queue_root=str(queue_root),
    )

    evidence = deliverables / "injection_exploitation_evidence.md"
    assert evidence.exists(), "evidence.md 应由 renderer 渲染落盘"
    md = evidence.read_text(encoding="utf-8")
    assert "## Successfully Exploited" in md
    assert "INJ-VULN-01" in md  # queue_root 透传 → valid_ids 含 INJ-VULN-01 → verdict 进 accepted


@pytest.mark.asyncio
async def test_injection_exploit_writes_verdicts_json(monkeypatch, tmp_path):
    """exploit agent 跑完后 verdicts.json 落盘 deliverables/blackbox/（spec 2026-08-12）。
    补全主线缺失产物：计数器数 exploited、coverage/PoC 读 accepted_ids。"""
    from supernova_core.agents import executor as exec_mod
    from supernova_core.models.agents import AgentName

    repo = tmp_path / "repo"
    repo.mkdir()
    deliverables = tmp_path / "deliverables"  # 黑盒产物落点

    queue_root = tmp_path / "whitebox-root"  # 白盒根：queue 在 whitebox/ 子目录
    (queue_root / "whitebox").mkdir(parents=True)
    (queue_root / "whitebox" / "injection_exploitation_queue.json").write_text(json.dumps(
        {"vulnerabilities": [
            {"ID": "INJ-VULN-01", "vulnerability_type": "SQLi",
             "externally_exploitable": True, "confidence": "high"}]}))

    async def fake_run(**kw):
        collector = kw.get("collector")
        if collector is not None:
            collector.append_section("add_exploit", {
                "vulnerability_id": "INJ-VULN-01", "status": "exploited", "severity": "critical",
                "impact": "i", "exploitation_steps": ["s"], "proof_of_impact": "p"})
        return FakeResult(structured_output=None)

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    executor = _patch_executor_env(monkeypatch, tmp_path)

    await executor.execute(
        agent_name=AgentName.INJECTION_EXPLOIT,
        repo_path=str(repo),
        deliverables_path=str(deliverables),
        queue_root=str(queue_root),
    )

    verdicts_file = deliverables / "blackbox" / "injection_exploit_verdicts.json"
    assert verdicts_file.exists(), "verdicts.json 应落盘 deliverables/blackbox/"
    payload = json.loads(verdicts_file.read_text(encoding="utf-8"))
    assert payload["vuln_class"] == "injection"
    assert "INJ-VULN-01" in payload["accepted_ids"]
    exploited = [v for v in payload["verdicts"] if v.get("status") == "exploited"]
    assert len(exploited) == 1
