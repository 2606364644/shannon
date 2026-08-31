"""轻量单次 LLM client 记账（spec 2026-08-27 §8）。

2026-08-27 计费检查发现：轻量单次调用（track-parity / poc gap-fill /
expected_response / report summary / recon summary）cost 系统性漏记——client
闭包把 ClaudeRunResult 剥成 str，cost/tokens/model 无归宿，session.json 的
total_cost_usd / 进度条 / 报告 cost 全不含。本模块提取 22269e4a
（run_gitnexus_verdict_agent 记账）的通用模式：

- 包装 runner（async (prompt, **kw) -> ClaudeRunResult-like）为轻量 client 契约
  （-> str | None：structured_output 优先 JSON 化、回落 text；失败/异常 → None）；
- 闭包内累计 cost/tokens/turns/调用数；
- 首次会入账的调用惰性发一次 ``start_agent``（2026-08-28 补——此前只有 end，
  实时 dashboard 看不到轻量 agent 的 running 态；零调用零幽灵）；
- activity 出口 ``await client.finalize()`` 一次 ``end_agent`` 记总账（agent_name
  唯一，如 track-parity / poc-gapfill——防 metrics.agents 同名覆盖）。

audit_session=None（测试/降级）→ 纯透传 + finalize no-op。
"""
from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)


class AccountedLlmClient:
    """记账包装的轻量单次 client（spec 2026-08-27 §8）。"""

    def __init__(self, runner, audit_session, agent_name: str):
        """runner: async (prompt, **kw) -> ClaudeRunResult-like；audit_session:
        duck-typed ``await end_agent(name, AgentEndResult)``（whitebox
        AuditSession；None → 纯透传）。"""
        self._runner = runner
        self._session = audit_session
        self._agent_name = agent_name
        self._cost = 0.0
        self._currency: str | None = None
        self._model: str | None = None
        self._turns = 0
        self._in = 0
        self._out = 0
        self._cache_read = 0
        self._cache_creation = 0
        self._calls = 0
        self._failures = 0
        self._started = False
        self._start = time.monotonic()

    async def _ensure_started(self) -> None:
        """首次会入账的调用时发一次 start_agent（2026-08-28 实时页 Agent 盲区修复）。

        此前只有 finalize 的 end_agent → 前端 dashboard 看不到轻量 agent 的
        running 态。惰性触发点=首次成功/失败返回（不含被吞异常），保证
        start→end 永远配对、零调用零幽灵条目。duck-typed session 无
        start_agent 方法（旧 mock/降级）时跳过；prompt 传占位符（对齐
        run_agent 先例，不把大 prompt 写盘）。
        """
        if self._started:
            return
        self._started = True
        start = getattr(self._session, "start_agent", None)
        if start is not None:
            await start(self._agent_name,
                        f"accounted-llm={self._agent_name}", attempt=1)

    async def call_result(self, prompt: str, **kw):
        """调 runner 返回原始 result 并记账（供需要 success/structured_output
        语义做 retry 判定的调用方，如 poc gap-fill）。"""
        result = await self._runner(prompt, **kw)
        if result is None or getattr(result, "success", True) is False:
            self._failures += 1
            await self._ensure_started()
            return result
        await self._ensure_started()
        self._record_success(result)
        return result

    async def __call__(self, prompt: str, **kw) -> str | None:
        try:
            result = await self._runner(prompt, **kw)
        except Exception as exc:
            logger.debug("accounted client %s call failed: %s",
                         self._agent_name, exc)
            return None
        if result is None or getattr(result, "success", True) is False:
            self._failures += 1
            await self._ensure_started()
            return None
        await self._ensure_started()
        if isinstance(result, str):
            # 已剥 str 的 client（测试 fake / 旧闭包）：原样透传、按零成本记一次
            # 调用——此前 getattr(result, "text", "") 把整个 str payload 静默吞成
            # ""（track-parity 配对响应整笔丢失的现场形态，2026-08-31）。
            self._calls += 1
            return result
        self._record_success(result)
        so = getattr(result, "structured_output", None)
        if so is not None:
            return json.dumps(so, ensure_ascii=False)
        return getattr(result, "text", "") or ""

    def _record_success(self, result) -> None:
        self._calls += 1
        self._cost += getattr(result, "cost", 0.0) or 0.0
        cur = getattr(result, "cost_currency", None)
        if cur:
            self._currency = cur
        self._model = getattr(result, "model", None) or self._model
        self._turns += getattr(result, "turns", 0) or 0
        tokens = getattr(result, "tokens", None)
        if tokens is not None:
            self._in += getattr(tokens, "input_tokens", 0) or 0
            self._out += getattr(tokens, "output_tokens", 0) or 0
            self._cache_read += getattr(tokens, "cache_read_input_tokens", 0) or 0
            self._cache_creation += getattr(tokens, "cache_creation_input_tokens", 0) or 0

    async def finalize(self) -> None:
        """出口一次记总账（有调用才记；session=None no-op）。"""
        if self._session is None or self._calls + self._failures == 0:
            return
        from supernova_core.models.audit import AgentEndResult
        await self._session.end_agent(self._agent_name, AgentEndResult(
            success=(self._failures == 0),
            duration_ms=int((time.monotonic() - self._start) * 1000),
            cost_usd=self._cost,
            cost_currency=self._currency or "USD",
            model=self._model,
            num_turns=self._turns,
            input_tokens=self._in or None,
            output_tokens=self._out or None,
            cache_read_tokens=self._cache_read or None,
            cache_creation_tokens=self._cache_creation or None,
        ))
