# vuln findings 交付通道加固 Phase 1（截断修复 + 失败诊断）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 网关流中断在 LLM 最终消息尾部时，双引擎兜底解析从半截 JSON 救回 N-1 条 findings；防线报错时携带 stop_reason / 文本证据 / 通道状态诊断。

**Architecture:** 新增纯函数 `repair_truncated_json`（`llm_json.py`——双引擎共享的无依赖字符串工具模块），接入 anthropic / openai 两处 structured_output 兜底分支（同生共死的对称改造）；executor 给静默跳过写盘点补 warning、给 validate 防线 raise 增补诊断 context。

**Tech Stack:** Python 3.12 / pytest（monkeypatch + MagicMock，无真机 LLM）。零新依赖（不引 json_repair，手写）。

**Spec:** `docs/superpowers/specs/2026-08-19-truncated-json-recovery-and-finding-submission-design.md` §3.1（Phase 1a）+ §3.2（Phase 1b）。本 plan 只做 Phase 1；Phase 2（submit_finding / roster 对账）另有 plan。

## Global Constraints

- **双引擎一致**（CLAUDE.md §2）：anthropic 与 openai 侧必须同时接入截断修复，不允许只修一侧。
- **零新依赖**：`llm_json.py` 保持无 SDK / 无第三方依赖（code_index 复用此模块）。
- **测试只跑改动相关文件**（CLAUDE.md §3）：全套 pytest 有预存挂起，禁止广跑。
- **loads 当裁判**（spec §3.1）：`repair_truncated_json` 任何产出必须能 `json.loads` 通过；完整 JSON / 救不回 → 返回 None。
- **元素内部截断连同残缺元素丢弃**（spec §3.1）：字符串字面量内截断时不猜（不补 `"` 凑完整——那会静默截短 notes 内容）。
- commit 信息用中文、尾注 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- 包根：所有路径相对 `/root/shannon-py/packages/core/`；pytest 从 `/root/shannon-py/packages/core` 目录跑（`uv run pytest` 或仓库既定方式；下文命令以 `python -m pytest` 示意）。

---

### Task 1: `repair_truncated_json` 纯函数（`llm_json.py`）

**Files:**
- Modify: `src/supernova_core/agents/llm_json.py`（文件末尾追加函数）
- Test: `tests/agents/test_llm_json.py`（文件末尾追加用例；文件 docstring 更新为模块级）

**Interfaces:**
- Consumes: 无（纯函数）。
- Produces: `repair_truncated_json(payload: str | None) -> str | None`——Task 2 依赖此签名；尾部截断返回补闭合后的合法 JSON 字符串，完整 JSON / 空串 / None / 救不回返回 None。

- [ ] **Step 1: Write the failing test**

在 `tests/agents/test_llm_json.py` 末尾追加（并把文件首行 docstring 改为 `"""llm_json 单测：repair_json_arguments（tool_call arguments 修复）+ repair_truncated_json（流截断修复）。"""`）：

```python
# ── repair_truncated_json（spec 2026-08-19 §3.1：网关流中断兜底）─────────────
import json

import pytest

from supernova_core.agents.llm_json import repair_truncated_json


def _queue(n: int) -> str:
    """构造 n 条 findings 的 exploitation queue JSON（模拟 vuln agent 最终消息）。"""
    vulns = [
        {"ID": f"AUTH-VULN-{i:02d}", "title": f"t{i}",
         "notes": f"notes {i} with }} brace and \" quote"}
        for i in range(1, n + 1)
    ]
    return json.dumps({"vulnerabilities": vulns}, ensure_ascii=False)


def test_truncated_mid_12th_element_recovers_11():
    """截断在第 12 条元素内部（ID 字符串中途）→ 救回前 11 条完整元素。"""
    full = _queue(12)
    truncated = full[: full.index('"AUTH-VULN-12"') + 5]  # 第 12 条 ID 字符串中途
    with pytest.raises(json.JSONDecodeError):
        json.loads(truncated)  # 前置：截断串本身必须是坏 JSON（否则用例失真）
    repaired = repair_truncated_json(truncated)
    assert repaired is not None
    data = json.loads(repaired)
    assert len(data["vulnerabilities"]) == 11
    assert data["vulnerabilities"][-1]["ID"] == "AUTH-VULN-11"


def test_truncated_inside_string_literal_drops_partial_element():
    """截断在第 12 条 notes 字符串中间 → 残缺元素丢弃，救回 11 条。"""
    full = _queue(12)
    truncated = full[: full.index("notes 12") + 4]  # notes 值字符串中途
    repaired = repair_truncated_json(truncated)
    assert repaired is not None
    data = json.loads(repaired)
    assert len(data["vulnerabilities"]) == 11
    assert data["vulnerabilities"][-1]["ID"] == "AUTH-VULN-11"


def test_truncated_object_root_closing_brace_only():
    """object 根只缺闭合括号（尾部值完整，如 `{"a": 1, "b": 2`）→ 补全返回。"""
    assert repair_truncated_json('{"count": 12') == '{"count": 12}'


def test_truncated_array_root_recovers_elements():
    """array 根（`[...]`）尾部截断 → 丢残缺元素、补 `]`。"""
    full = json.dumps([{"ID": "A-1"}, {"ID": "A-2"}, {"ID": "A-3"}])
    truncated = full[: full.index('"A-3"')]
    repaired = repair_truncated_json(truncated)
    assert repaired is not None
    assert json.loads(repaired) == [{"ID": "A-1"}, {"ID": "A-2"}]


def test_truncated_before_first_element_returns_none():
    """截断在首个完整元素之前（无元素可救）→ None（走 validator 防线重试）。"""
    truncated = '{"vulnerabilities": [{"ID": "AU'
    assert repair_truncated_json(truncated) is None


def test_complete_json_returns_none():
    """完整 JSON 不归本函数管 → None（调用方已 loads 成功）。"""
    assert repair_truncated_json(_queue(12)) is None
    assert repair_truncated_json('{"a": 1}') is None


def test_garbage_and_empty_return_none():
    """纯文本 / 空串 / None → None。"""
    assert repair_truncated_json("not json at all") is None
    assert repair_truncated_json("") is None
    assert repair_truncated_json(None) is None  # type: ignore[arg-type]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /root/shannon-py/packages/core && python -m pytest tests/agents/test_llm_json.py -v -k repair_truncated
```
Expected: FAIL（`ImportError: cannot import name 'repair_truncated_json'`）。

- [ ] **Step 3: Write minimal implementation**

在 `src/supernova_core/agents/llm_json.py` 末尾追加：

```python
def repair_truncated_json(payload: str | None) -> str | None:
    """尾部截断的 JSON 补闭合修复；救不回返 None（spec 2026-08-19 §3.1）。

    只处理「尾部不完整」一种畸形（网关流中断在 LLM 最终消息的实际形态），
    不做任意畸形修复。loads 当裁判：任何产出必须能 json.loads 通过才返回；
    完整 JSON / 空串 / 救不回 → None（调用方走 validator 防线重试）。

    算法（转义感知栈扫描，语义等同 spec 描述的 raw_decode 定位失败点）：

    1. ``json.loads`` 能过 → None（不归本函数管）。
    2. 单遍扫描记录每个「容器直接子元素完成」候选点（``}``/``]`` pop 时刻）
       及该位置的嵌套栈快照。
    3. 候选 1（从晚到早）：截到候选点 + 按栈快照补闭合 → loads 验证。
       截断在元素内部（字符串中途/字段残缺）时，残缺元素连同其后内容被
       丢弃——回溯到上一个完整元素边界，救回 N-1 条。
    4. 候选 2（末尾补全）：扫描结束不在字符串字面量内时，按末尾栈整体补
       闭合（object 根尾部值完整、只缺 ``}`` 的形态）。字符串内截断不猜
       （补 ``"`` 会静默截短 notes 内容）。
    """
    if not payload or not payload.strip():
        return None
    s = payload.strip()
    try:
        json.loads(s)
        return None
    except (json.JSONDecodeError, ValueError):
        pass

    stack: list[str] = []
    in_string = False
    escape = False
    candidates: list[tuple[int, list[str]]] = []  # (cut_pos, stack_snapshot)
    for i, ch in enumerate(s):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
            candidates.append((i + 1, list(stack)))

    for cut, st in reversed(candidates):
        closer = "".join("}" if o == "{" else "]" for o in reversed(st))
        candidate = s[:cut] + closer
        try:
            json.loads(candidate)
            return candidate
        except (json.JSONDecodeError, ValueError):
            continue

    if not in_string:
        closer = "".join("}" if o == "{" else "]" for o in reversed(stack))
        candidate = s + closer
        try:
            json.loads(candidate)
            return candidate
        except (json.JSONDecodeError, ValueError):
            return None
    return None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /root/shannon-py/packages/core && python -m pytest tests/agents/test_llm_json.py -v
```
Expected: 全部 PASS（原有 repair_json_arguments 用例 + 新增 7 例）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py && git add packages/core/src/supernova_core/agents/llm_json.py packages/core/tests/agents/test_llm_json.py && git commit -m "feat(llm_json): repair_truncated_json 尾部截断补闭合修复 — spec 2026-08-19 §3.1

loads 当裁判；元素内部截断连同残缺元素丢弃；字符串内截断不猜。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 双引擎兜底分支接入截断修复（anthropic + openai 对称）

**Files:**
- Modify: `src/supernova_core/agents/providers_anthropic.py:23`（import）与 `:443-457`（`_extract_result` 兜底分支）
- Modify: `src/supernova_core/agents/openai_result_mapper.py:80-85`（`map_run_result` 兜底分支）
- Test: `tests/agents/test_dual_engine_alignment.py`（文件末尾追加对称用例）

**Interfaces:**
- Consumes: Task 1 的 `repair_truncated_json(payload) -> str | None`。
- Produces: 双引擎兜底分支行为——`_extract_json_payload` 提取的 payload `json.loads` 失败时调 `repair_truncated_json`，成功则修复产出入 `structured_output` 并发 `logger.warning`（含 engine 标识、原始/修复长度）；失败维持 `structured_output = None`。Task 3/4 不依赖本任务内部符号。

- [ ] **Step 1: Write the failing test**

在 `tests/agents/test_dual_engine_alignment.py` 末尾追加：

```python
def test_truncated_final_text_recovered_both_engines():
    """截断修复双引擎对称（spec 2026-08-19 §3.1）：半截最终文本 →
    structured_output 救回 N-1 条。anthropic _extract_result 与 openai
    map_run_result 的兜底分支同生共死，必须一起接入。"""
    import json
    from unittest.mock import MagicMock
    from supernova_core.agents.providers_anthropic import AnthropicProvider
    from supernova_core.agents.openai_result_mapper import map_run_result
    from supernova_core.agents.runner import ProviderConfig

    full = json.dumps({"vulnerabilities": [
        {"ID": f"AUTH-VULN-{i:02d}", "title": f"t{i}"} for i in range(1, 13)]})
    truncated = full[: full.index('"AUTH-VULN-12"') + 5]  # 第 12 条 ID 字符串中途

    # anthropic 侧：_extract_result 兜底分支（collected_text）
    provider = AnthropicProvider(ProviderConfig(type="anthropic_api"))
    rm = MagicMock()
    rm.collected_text = truncated
    rm.result = ""
    rm.content = []
    rm.structured_output = None  # SDK 第一道（--json-schema）解析失败
    rm.usage = {"input_tokens": 1, "output_tokens": 1,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    rm.result_is_error = False
    rm.result_subtype = None
    rm.stop_reason = None
    claude_res = provider._extract_result(
        rm, duration=10, model="m", turn_count=1, output_format={"type": "object"})
    assert claude_res.structured_output is not None
    assert len(claude_res.structured_output["vulnerabilities"]) == 11
    assert claude_res.structured_output["vulnerabilities"][-1]["ID"] == "AUTH-VULN-11"

    # openai 侧：map_run_result 兜底分支（final_output 纯文本）
    o_usage = MagicMock(input_tokens=1, output_tokens=1, input_tokens_details=None)
    rr = MagicMock()
    rr.final_output = truncated
    rr.context_wrapper.usage = o_usage
    openai_res = map_run_result(
        rr, duration_ms=10, model="m", turns=1, output_format={"type": "object"})
    assert openai_res.structured_output is not None
    assert openai_res.structured_output == claude_res.structured_output
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /root/shannon-py/packages/core && python -m pytest tests/agents/test_dual_engine_alignment.py::test_truncated_final_text_recovered_both_engines -v
```
Expected: FAIL——两侧 `structured_output is None`（兜底 loads 失败后无修复）。

- [ ] **Step 3: Write minimal implementation**

**3a. `providers_anthropic.py`**——import 区（第 23 行 `from .openai_output_schema import _extract_json_payload` 之后）加一行：

```python
from .llm_json import repair_truncated_json
```

`_extract_result` 兜底分支（现 `:447-452`）改为：

```python
            payload = _extract_json_payload(text)
            if payload:
                try:
                    structured_output = json.loads(payload)
                except (json.JSONDecodeError, ValueError):
                    # 截断修复兜底（spec 2026-08-19 §3.1）：网关流中断在最终
                    # 消息尾部时，从半截 JSON 救回 N-1 条完整元素，避免
                    # structured_output=None → 静默漏盘 → 整轨报废三轮重跑。
                    repaired = repair_truncated_json(payload)
                    if repaired is not None:
                        structured_output = json.loads(repaired)
                        items = (structured_output.get("vulnerabilities")
                                 if isinstance(structured_output, dict)
                                 else structured_output)
                        logger.warning(
                            "structured_output recovered via truncation repair "
                            "(anthropic engine): payload_len=%d repaired_len=%d "
                            "recovered_items=%s",
                            len(payload), len(repaired),
                            len(items) if isinstance(items, (list, dict)) else "?",
                        )
                    else:
                        structured_output = None
                else:
                    logger.info(
                        "structured_output recovered from collected_text fallback "
                        "(SDK result_message.structured_output was empty)"
                    )
```

**3b. `openai_result_mapper.py`**——import 区第 10 行改为：

```python
from .llm_json import _extract_json_payload, repair_truncated_json
```

`map_run_result` 兜底分支（现 `:80-85`）改为：

```python
            candidate = _extract_json_payload(final)
            if candidate is not None:
                try:
                    structured_output = json.loads(candidate)
                except (json.JSONDecodeError, ValueError):
                    # 截断修复兜底（spec 2026-08-19 §3.1）：与 anthropic 引擎
                    # _extract_result 兜底对称（双引擎一致铁律）。
                    repaired = repair_truncated_json(candidate)
                    if repaired is not None:
                        structured_output = json.loads(repaired)
                        items = (structured_output.get("vulnerabilities")
                                 if isinstance(structured_output, dict)
                                 else structured_output)
                        _log.warning(
                            "structured_output recovered via truncation repair "
                            "(openai engine): payload_len=%d repaired_len=%d "
                            "recovered_items=%s",
                            len(candidate), len(repaired),
                            len(items) if isinstance(items, (list, dict)) else "?",
                        )
                    else:
                        structured_output = None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /root/shannon-py/packages/core && python -m pytest tests/agents/test_dual_engine_alignment.py tests/agents/test_openai_result_mapper.py tests/agents/test_openai_output_schema.py -v
```
Expected: 全部 PASS（新对称用例 + 两文件原有用例无回归——`test_openai_result_mapper.py` 有 `test_map_structured_output` 等 8+ 例，`test_openai_output_schema.py` 有 8 例）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py && git add packages/core/src/supernova_core/agents/providers_anthropic.py packages/core/src/supernova_core/agents/openai_result_mapper.py packages/core/tests/agents/test_dual_engine_alignment.py && git commit -m "feat(providers): 双引擎 structured_output 兜底接入截断修复 — spec 2026-08-19 §3.1

anthropic _extract_result 与 openai map_run_result 对称接入
repair_truncated_json，断流尾部截断救回 N-1 条，warning 记录修复证据。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: executor 失败诊断（静默跳过 warning + validate raise context 增补）

**Files:**
- Modify: `src/supernova_core/agents/executor.py`（import 区 + `_result_cost_context` 后 + `:198-206` 写盘点 + `:242-243` validate 调用点）
- Create: `tests/test_executor_validation_diagnostics.py`

**Interfaces:**
- Consumes: 现有 `_result_cost_context(result) -> dict`（executor.py:47）；`PentestError.context: dict`（`models/errors.py:42`，实例属性恒为 dict）。
- Produces: `_validation_error_context(result) -> dict`（模块级私有，后续 Phase 2 在此函数接 collector 计数）；日志契约——跳过 warning 含 `"NOT written"`，修复证据在 Task 2 的 warning 里。

- [ ] **Step 1: Write the failing test**

创建 `tests/test_executor_validation_diagnostics.py`（参照 `test_executor_artifact_postprocess.py` 的 monkeypatch 模式）：

```python
"""executor 失败诊断（spec 2026-08-19 §3.2）：

1. structured_output=None 时跳过 queue 写盘必须留 warning（现状零日志）；
2. validate_deliverable 防线 raise 的 PentestError.context 携带 stop_reason /
   文本长度与末尾片段 / structured_output_present / cost（现状只有 agent_name）。
"""
import asyncio

import pytest

from supernova_core.models.errors import PentestError


def _run(coro):
    return asyncio.run(coro)


def _stub_result():
    """成功但无 structured_output 的 result——模拟网关断流后兜底解析失败。"""
    truncated = '{"vulnerabilities": [{"ID": "AUTH-VULN-01"}, {"ID": "AU'

    class _R:
        success = True
        turns = 3
        cost = 0.42
        cost_currency = "CNY"
        text = truncated
        error = None
        retryable = True
        model = "stub-model"
        stop_reason = "end_turn"

        class tokens:
            input_tokens = 100
            output_tokens = 50
            cache_read_input_tokens = 0
            cache_creation_input_tokens = 0

        structured_output = None

    return _R()


def _setup_executor(tmp_path, monkeypatch):
    from supernova_core.agents import executor as exec_mod
    from supernova_core.models.agents import AGENTS, AgentName
    from supernova_core.prompts.manager import PromptManager

    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    # 预置 deliverable md，让 validate_deliverable 走到 queue 检查（validators.py:41 防线）
    defn = AGENTS[AgentName.INJECTION_VULN]
    (deliverables / defn.deliverable_filename).write_text("placeholder", encoding="utf-8")

    async def fake_run(**kw):
        return _stub_result()

    monkeypatch.setattr(exec_mod, "run_claude_prompt", fake_run)
    monkeypatch.setattr(exec_mod.GitManager, "ensure_repository",
                        classmethod(lambda cls, p: asyncio.sleep(0)))
    monkeypatch.setattr(exec_mod.GitManager, "create_checkpoint",
                        lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(exec_mod.GitManager, "commit",
                        lambda *a, **k: asyncio.sleep(0))
    # 隔离 md 渲染（executor.py:20 render_deliverable）：本测试聚焦诊断断言，
    # 不依赖 vuln renderer 对空 collector 的行为
    monkeypatch.setattr(exec_mod, "render_deliverable", lambda *a, **k: None)

    pm = PromptManager.__new__(PromptManager)
    pm.prompts_dir = tmp_path
    monkeypatch.setattr(pm, "load_sync", lambda *a, **k: "PROMPT")

    return exec_mod.AgentExecutor(pm), exec_mod, deliverables


def test_missing_queue_error_carries_diagnostics(tmp_path, monkeypatch):
    ax, exec_mod, deliverables = _setup_executor(tmp_path, monkeypatch)

    with pytest.raises(PentestError) as ei:
        _run(ax.execute(
            agent_name=exec_mod.AgentName.INJECTION_VULN,
            repo_path=str(deliverables), deliverables_path=str(deliverables),
            structured_output_schema={"type": "object"},
        ))

    ctx = ei.value.context
    # 现有键保留（validators 原始 context）
    assert ctx["agent_name"] == "injection-vuln"
    # 诊断增补键（spec §3.2）
    assert ctx["stop_reason"] == "end_turn"
    assert ctx["collected_text_len"] == len(_stub_result().text)
    assert ctx["collected_text_tail"].endswith('{"ID": "AU')
    assert ctx["structured_output_present"] is False
    # cost/tokens 合并（_result_cost_context 字段）
    assert ctx["cost_usd"] == 0.42
    assert ctx["input_tokens"] == 100


def test_skipped_queue_write_logs_warning(tmp_path, monkeypatch, caplog):
    """structured_output=None 跳过写盘必须留 warning（现状零日志，排障靠猜）。"""
    import logging
    ax, exec_mod, deliverables = _setup_executor(tmp_path, monkeypatch)

    with caplog.at_level(logging.WARNING, logger="supernova_core.agents.executor"):
        with pytest.raises(PentestError):  # validate 防线照常 raise（另一断言覆盖）
            _run(ax.execute(
                agent_name=exec_mod.AgentName.INJECTION_VULN,
                repo_path=str(deliverables), deliverables_path=str(deliverables),
                structured_output_schema={"type": "object"},
            ))

    warnings = [r for r in caplog.records
                if "NOT written" in r.getMessage()]
    assert len(warnings) == 1
    assert "injection_exploitation_queue.json" in warnings[0].getMessage()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /root/shannon-py/packages/core && python -m pytest tests/test_executor_validation_diagnostics.py -v
```
Expected: 两个用例 FAIL——`ctx` 缺 `stop_reason` 等诊断键（KeyError/断言失败）；caplog 无 warning 记录。

- [ ] **Step 3: Write minimal implementation**

**3a.** `executor.py` import 区（第 1 行 `import time` 之前）加：

```python
import logging
```

import 区之后、模块级代码前加：

```python
logger = logging.getLogger(__name__)
```

**3b.** `_result_cost_context`（executor.py:47-63）函数之后追加：

```python
def _validation_error_context(result) -> dict:
    """validate_deliverable 防线 raise 时的诊断 context（spec 2026-08-19 §3.2）。

    现状该 raise 只带 agent_name/expected_queue，stop_reason / 文本证据 /
    通道状态全丢（网关断流排障只能猜）。合并 _result_cost_context 的
    cost/tokens；collector 计数 Phase 2 接 collector 后有真值，当前恒 0。
    """
    ctx = _result_cost_context(result)
    text = getattr(result, "text", "") or ""
    ctx.update({
        "stop_reason": getattr(result, "stop_reason", None),
        "collected_text_len": len(text),
        "collected_text_tail": text[-200:] if text else "",
        "structured_output_present": getattr(result, "structured_output", None) is not None,
        "collector_submitted_count": 0,  # Phase 2（submit_finding）接入
        "collector_roster_count": 0,     # Phase 2（finding_roster）接入
    })
    return ctx
```

**3c.** queue 写盘点（executor.py:198-206）改为：

```python
        queue_filename = get_queue_filename(agent_name)
        if (
            not skip_artifact_postprocess
            and result.structured_output is not None
            and queue_filename
        ):
            # spec 2026-08-18 tiering：queue json 下沉桶内 intermediate/（交付物留顶层）。
            queue_path = intermediate_path(deliverables, queue_filename)
            atomic_write_json(queue_path, result.structured_output)
        elif not skip_artifact_postprocess and queue_filename:
            # 诊断（spec 2026-08-19 §3.2）：现状此分支零日志静默跳过，网关断流
            # 排障全靠猜；warning 留第一现场（validate 防线随后 raise 补 context）。
            logger.warning(
                "agent %s produced no structured output — queue %s NOT written "
                "(text_len=%d, stop_reason=%r)",
                agent_name.value, queue_filename,
                len(getattr(result, "text", "") or ""), result.stop_reason,
            )
```

**3d.** validate 调用点（executor.py:242-243）改为：

```python
        if not skip_artifact_postprocess:
            try:
                await validate_deliverable(deliverables, agent_name)
            except PentestError as exc:
                # 诊断增补（spec 2026-08-19 §3.2）：防线 raise 原地补 result 级
                # 证据（stop_reason/文本尾巴/通道状态/cost）再上抛——不改
                # validate_deliverable 签名（纯函数波及面大），也不吞 retryable
                # /error_code 分类（原地 update，分类字段不动）。
                exc.context.update(_validation_error_context(result))
                raise
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /root/shannon-py/packages/core && python -m pytest tests/test_executor_validation_diagnostics.py -v
```
Expected: 两个用例 PASS。

- [ ] **Step 5: Run neighbor executor tests (regression)**

```bash
cd /root/shannon-py/packages/core && python -m pytest tests/test_executor_artifact_postprocess.py tests/test_executor_error_code_passthrough.py tests/test_executor_vuln_render.py -v
```
Expected: 全部 PASS（既有 executor 行为无回归——尤其 `test_skip_postprocess_avoids_queue_write`：skip=True 时不进新 elif 分支）。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py && git add packages/core/src/supernova_core/agents/executor.py packages/core/tests/test_executor_validation_diagnostics.py && git commit -m "feat(executor): 失败路径诊断 — 跳过写盘补 warning + 防线 raise 带 result 级证据 — spec 2026-08-19 §3.2

structured_output=None 静默跳过零日志 → warning；validate PentestError.context
原地增补 stop_reason/collected_text 长度与末尾 200 字符/通道状态/cost。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 回归收尾（相关测试全集 + 铁律锁定 + 文档）

**Files:**
- 无代码改动（验证性任务；若发现问题就地修复并补进对应 commit）

**Interfaces:**
- Consumes: Task 1-3 的全部产出。
- Produces: Phase 1 完成判定——全部相关测试绿 + 铁律锁定测试绿。

- [ ] **Step 1: Run all touched-area tests together**

```bash
cd /root/shannon-py/packages/core && python -m pytest \
  tests/agents/test_llm_json.py \
  tests/agents/test_dual_engine_alignment.py \
  tests/agents/test_openai_result_mapper.py \
  tests/agents/test_openai_output_schema.py \
  tests/test_executor_validation_diagnostics.py \
  tests/test_executor_artifact_postprocess.py \
  tests/test_executor_error_code_passthrough.py \
  tests/test_executor_vuln_render.py \
  -v
```
Expected: 全部 PASS。任何失败：修复后重跑，并把修复补进对应任务的 commit（`git commit --amend` 或 fixup）。

- [ ] **Step 2: Run iron-rule lock test**

```bash
cd /root/shannon-py/packages/core && python -m pytest tests/prompts/test_static_dataflow_hints_decoupling.py -v
```
Expected: PASS（本 Phase 未触 prompt，锁定面不受影响——跑一次确认）。

- [ ] **Step 3: Verify no SDK import leaked into llm_json**

```bash
cd /root/shannon-py && grep -n "^import\|^from" packages/core/src/supernova_core/agents/llm_json.py
```
Expected: 只有 `__future__` / `json` / `re`——零 SDK / 零第三方依赖（模块定位不变量）。

- [ ] **Step 4: Update memory**

向 `/root/.claude/projects/-root-shannon-py/memory/vuln-queue-delivery-hardening-spec.md` 追加一行实施状态（Phase 1 done：3 个 commit 主题、测试数），并在 `MEMORY.md` 对应行尾补「Phase1 已实施」。无需新 memory 文件。

- [ ] **Step 5: Final commit (if memory/docs touched inside repo)**

memory 在 repo 外（`~/.claude/`），不进 git。若 Step 1-3 产生了修复 commit，确认 `git log --oneline -5` 顶部是本 plan 的 3-4 个 commit；否则无操作。
