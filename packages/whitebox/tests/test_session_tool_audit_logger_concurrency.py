import asyncio
import json
from pathlib import Path

from shannon_core.display.formatters import agent_prefix
from shannon_core.models.audit import AgentEndResult
from shannon_core.models.metrics import SessionMetadata
from shannon_whitebox.audit.session import AuditSession
from shannon_whitebox.audit.session_tool_audit_logger import SessionToolAuditLogger
from shannon_whitebox.audit.utils import generate_audit_path


def _make_meta(tmp_path: Path) -> SessionMetadata:
    return SessionMetadata(id="s1", web_url="https://example.com", output_path=str(tmp_path))


async def test_concurrent_agents_keep_correct_workflow_attribution(tmp_path: Path):
    """5 agents concurrent via asyncio.gather; each must own exactly its 5 [LLM] lines.
    Regression anchor: under the old shared _current_agent_name, all turns collapsed
    onto the last-started agent and the other four got 0."""
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    names = ["injection-vuln", "xss-vuln", "auth-vuln", "ssrf-vuln", "authz-vuln"]

    async def run_one(name: str) -> None:
        lg = SessionToolAuditLogger(session, name, attempt=1)
        await session.start_agent(name, f"prompt-{name}", attempt=1)
        await lg.initialize()
        for turn in range(1, 6):
            await lg.log_assistant_turn(turn, f"{name} turn {turn}")
            await lg.log_tool_start("Read", {"path": f"{name}.js"})
        await lg.close(success=True, duration_ms=100)
        await session.end_agent(name, AgentEndResult(
            success=True, duration_ms=100, cost_usd=0.0, attempt_number=1))

    await asyncio.gather(*(run_one(n) for n in names))
    await session.close()

    wf = (generate_audit_path(meta) / "workflow.log").read_text()
    for name in names:
        # FileLogRenderer._prefixed renders known agents as '[Prefix] name'
        # (formatters.agent_prefix); all 5 vuln names are in _AGENT_PREFIXES, so each
        # [LLM]/[TOOL] line carries its own prefix+name. Under the old shared-state race
        # every turn collapsed onto the last-started agent and the other four got 0.
        who = f"{agent_prefix(name)} {name}"
        assert wf.count(f"[LLM]   {who}: Turn") == 5, f"{name} [LLM] count != 5"
        assert wf.count(f"[TOOL]  {who}: Read:") == 5, f"{name} [TOOL] count != 5"


async def test_concurrent_agents_keep_correct_per_agent_json(tmp_path: Path):
    """Each agent's JSONL log contains only its own events (no _agent_logger race)."""
    meta = _make_meta(tmp_path)
    session = AuditSession(meta)
    await session.initialize()
    names = ["injection-vuln", "xss-vuln", "auth-vuln", "ssrf-vuln", "authz-vuln"]

    async def run_one(name: str) -> None:
        lg = SessionToolAuditLogger(session, name, attempt=1)
        await session.start_agent(name, f"prompt-{name}", attempt=1)
        await lg.initialize()
        await lg.log_assistant_turn(1, f"{name} content")
        await lg.close(success=True, duration_ms=0)
        await session.end_agent(name, AgentEndResult(
            success=True, duration_ms=0, cost_usd=0.0, attempt_number=1))

    await asyncio.gather(*(run_one(n) for n in names))
    await session.close()

    agents_dir = generate_audit_path(meta) / "agents"
    for name in names:
        log_files = list(agents_dir.glob(f"*_{name}_attempt-1.log"))
        assert len(log_files) == 1, f"expected 1 log for {name}, got {log_files}"
        content = log_files[0].read_text()
        assert f"Agent: {name}" in content
        events = [json.loads(line) for line in content.split("\n") if line.startswith("{")]
        llm = [e for e in events if e["type"] == "llm_response"]
        assert len(llm) == 1
        assert llm[0]["data"]["content"] == f"{name} content"
