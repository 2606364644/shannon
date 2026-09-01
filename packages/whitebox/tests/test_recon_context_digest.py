"""Shared recon-context digest tests (schema v2, spec 2026-09-01).

The digest is an LLM-track artifact derived only from recon_deliverable.md.
It exists so the vuln-agent fan-out no longer summarizes the same recon input
once per agent. Schema v2 adds six-section views, endpoint-coverage
reconciliation, and degraded/resume-upgrade semantics.
"""
import hashlib
import json
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from supernova_whitebox.pipeline import activities
from supernova_whitebox.pipeline.shared import ActivityInput


RECON_MD = """# Recon

## 3. Authentication & Session Management Flow
- session cookie, 1h expiry

## 4. API Endpoint Inventory
| Method | Path | Role | Object ID |
|---|---|---|---|
| GET | /foo | user | - |
| POST | /bar/:id | admin | id |

## 5. Potential Input Vectors
- excluded section

### 6.3 Flows
- client → api → db

## 7. Role & Privilege Architecture
- roles: user, admin

## 8. Authorization Vulnerability Candidates
- GET /foo missing horizontal ownership check

## 9. Injection Sources
- command: exec(user.input)
"""

SIX_SECTION_SUMMARY = """## endpoints
- GET /foo (user)
- POST /bar/:id (admin, object-id=id)
## authz
- GET /foo: no ownership check (horizontal, high sensitivity)
## injection
- command: exec(user.input)
## xss
(none found)
## ssrf
(none found)
## auth
- session cookie, 1h expiry
"""

_SECTION_KEYS = {"endpoints", "authz", "injection", "xss", "ssrf", "auth"}


class _FakeAccountedClient:
    def __init__(self, result: str | None = SIX_SECTION_SUMMARY):
        self.result = result
        self.calls = 0
        self.finalized = 0

    async def __call__(self, prompt: str) -> str:
        self.calls += 1
        if self.result is None:
            raise RuntimeError("summary unavailable")
        return self.result

    async def finalize(self) -> None:
        self.finalized += 1


def _session() -> MagicMock:
    session = MagicMock()

    @asynccontextmanager
    async def track_step(*args, **kwargs):
        yield

    session.track_step = track_step
    return session


def _input(tmp_path: Path) -> ActivityInput:
    return ActivityInput(repo_path=str(tmp_path), workspace_path=str(tmp_path))


def _setup(tmp_path: Path, recon: str = RECON_MD) -> tuple[Path, _FakeAccountedClient]:
    deliverables = tmp_path / "deliverables" / "whitebox"
    deliverables.mkdir(parents=True)
    (deliverables / "recon_deliverable.md").write_text(recon, encoding="utf-8")
    client = _FakeAccountedClient()
    return deliverables, client


async def _run_digest(tmp_path: Path, deliverables: Path, client) -> dict:
    with ExitStack() as stack:
        for p in _digest_patches(deliverables, tmp_path, client):
            stack.enter_context(p)
        return await activities.run_recon_context_digest(_input(tmp_path))


def _digest_patches(deliverables: Path, tmp_path: Path, client):
    return (
        patch.object(activities, "_get_paths", return_value=(tmp_path, deliverables, tmp_path)),
        patch.object(activities, "ensure_audit_session", AsyncMock()),
        patch("supernova_whitebox.audit.session_registry.get_audit_session",
              return_value=_session()),
        patch.object(activities, "_make_recon_summary_llm_client", return_value=client),
    )


def _artifact(deliverables: Path) -> dict:
    return json.loads(
        (deliverables / "intermediate" / "recon_context_digest.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_digest_generated_once_then_cache_hit(tmp_path):
    deliverables, client = _setup(tmp_path)

    first = await _run_digest(tmp_path, deliverables, client)
    second = await _run_digest(tmp_path, deliverables, client)

    assert first["source"] == "llm-summary"
    assert first["cache_hit"] is False
    assert first["degraded"] is False
    assert first["degraded_reason"] is None
    assert second["cache_hit"] is True
    assert client.calls == 1
    assert client.finalized == 1

    data = _artifact(deliverables)
    assert data["schema_version"] == 2
    assert data["source"] == "llm-summary"
    assert data["degraded"] is False
    assert data["degraded_reason"] is None
    assert data["source_hash"] == hashlib.sha256(RECON_MD.encode()).hexdigest()
    assert data["summarizer_prompt_version"] == (
        activities.RECON_CONTEXT_SUMMARIZER_PROMPT_VERSION)
    assert data["text"] == SIX_SECTION_SUMMARY
    assert set(data["sections"]) == _SECTION_KEYS
    assert "- GET /foo (user)" in data["sections"]["endpoints"]
    # 对账：§4 表 2 数据行，digest endpoints 节 2 行 → ratio 1.0
    assert data["coverage"] == {"digest_endpoint_rows": 2, "coverage_ratio": 1.0}
    assert data["missing_sections"] == []
    assert data["input_meta"]["source_endpoint_rows"] == 2
    assert data["input_meta"]["input_truncated"] is False


@pytest.mark.asyncio
async def test_recon_change_invalidates_digest(tmp_path):
    deliverables, client = _setup(tmp_path)

    await _run_digest(tmp_path, deliverables, client)
    (deliverables / "recon_deliverable.md").write_text(
        RECON_MD + "\n| GET | /new | user | - |\n", encoding="utf-8")
    result = await _run_digest(tmp_path, deliverables, client)

    assert result["cache_hit"] is False
    assert client.calls == 2


@pytest.mark.asyncio
async def test_llm_failure_writes_deterministic_six_sections_terminal_state(tmp_path):
    """LLM 失败 → deterministic-extract 六节 sections（非 degraded 有效终态）。

    spec 2026-09-01 §4.4/§4.6：确定性抽取是终态——resume 跳过、不再升级
    （旧行为"deterministic 也升级"按新语义反转为终态）。
    """
    deliverables, failing = _setup(tmp_path)
    failing.result = None

    result = await _run_digest(tmp_path, deliverables, failing)

    assert result["source"] == "deterministic-extract"
    assert result["degraded"] is False
    assert failing.finalized == 1

    data = _artifact(deliverables)
    assert data["schema_version"] == 2
    assert data["degraded"] is False
    # 六节确定性抽取构造的 sections（xss 无源节缺席）
    assert set(data["sections"]) == {"auth", "endpoints", "ssrf", "authz", "injection"}
    assert "| GET | /foo |" in data["sections"]["endpoints"]
    assert "exec(user.input)" in data["sections"]["injection"]
    assert data["missing_sections"] == ["xss"]
    # 对账仅对 llm-summary 生效 → deterministic 模式 coverage 不适用
    assert data["coverage"] == {"digest_endpoint_rows": None, "coverage_ratio": None}

    recovered = _FakeAccountedClient("upgraded digest")
    resumed = await _run_digest(tmp_path, deliverables, recovered)

    assert resumed["cache_hit"] is True  # 终态跳过，不触发升级
    assert recovered.calls == 0


@pytest.mark.asyncio
async def test_coverage_low_digest_is_degraded_and_upgrades_on_resume(tmp_path):
    """复刻真实残缺（31 端点只摘要出 4 个的同型）：ratio<0.8 → degraded → resume 升级。"""
    deliverables, sparse = _setup(tmp_path)
    sparse.result = "## endpoints\n- GET /foo (user)\n" + (
        "## authz\n(none found)\n## injection\n(none found)\n## xss\n(none found)\n"
        "## ssrf\n(none found)\n## auth\n(none found)\n")

    degraded = await _run_digest(tmp_path, deliverables, sparse)

    assert degraded["source"] == "llm-summary"
    assert degraded["degraded"] is True
    assert degraded["degraded_reason"] == "coverage_low"
    data = _artifact(deliverables)
    assert data["coverage"] == {"digest_endpoint_rows": 1, "coverage_ratio": 0.5}

    good = _FakeAccountedClient(SIX_SECTION_SUMMARY)
    upgraded = await _run_digest(tmp_path, deliverables, good)

    assert upgraded["cache_hit"] is False
    assert good.calls == 1
    assert _artifact(deliverables)["degraded"] is False


@pytest.mark.asyncio
async def test_unsectioned_digest_degrades_and_upgrades_on_resume(tmp_path):
    """LLM 输出完全不分节 → sections 空 + degraded=unsectioned，text 全量保底。"""
    deliverables, prose = _setup(tmp_path)
    prose.result = "Plain prose without any section heading at all.\nJust two lines.\n"

    result = await _run_digest(tmp_path, deliverables, prose)

    assert result["degraded"] is True
    assert result["degraded_reason"] == "unsectioned"
    data = _artifact(deliverables)
    assert data["sections"] == {}
    assert data["text"].startswith("Plain prose")
    assert data["missing_sections"] == [
        "endpoints", "authz", "injection", "xss", "ssrf", "auth"]

    good = _FakeAccountedClient(SIX_SECTION_SUMMARY)
    upgraded = await _run_digest(tmp_path, deliverables, good)

    assert upgraded["cache_hit"] is False
    assert good.calls == 1
    assert _artifact(deliverables)["degraded"] is False


@pytest.mark.asyncio
async def test_v1_digest_cache_miss_regenerates(tmp_path):
    """存量 v1 digest 即使指纹字段全对也判 miss（schema_version 升 2 双保险）。"""
    deliverables, client = _setup(tmp_path)
    digest_path = deliverables / "intermediate" / "recon_context_digest.json"
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.write_text(json.dumps({
        "schema_version": 1,
        "source": "llm-summary",
        "source_hash": hashlib.sha256(RECON_MD.encode("utf-8")).hexdigest(),
        "summarizer_prompt_version": activities.RECON_CONTEXT_SUMMARIZER_PROMPT_VERSION,
        "language": "zh",
        "text": "legacy v1 digest",
    }), encoding="utf-8")

    result = await _run_digest(tmp_path, deliverables, client)

    assert result["cache_hit"] is False
    assert client.calls == 1
    assert _artifact(deliverables)["schema_version"] == 2


@pytest.mark.asyncio
async def test_vuln_prompt_builder_reads_digest_without_llm(tmp_path):
    """注入侧读 digest（schema v2）；零 per-agent LLM 调用（分节注入另测）。"""
    deliverables, _ = _setup(tmp_path)
    digest_path = deliverables / "intermediate" / "recon_context_digest.json"
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.write_text(json.dumps({
        "schema_version": 2,
        "source": "llm-summary",
        "degraded": False,
        "degraded_reason": None,
        "source_hash": hashlib.sha256(RECON_MD.encode("utf-8")).hexdigest(),
        "summarizer_prompt_version": activities.RECON_CONTEXT_SUMMARIZER_PROMPT_VERSION,
        "language": "zh",
        "text": "shared context",
        "sections": {},
    }), encoding="utf-8")

    async def fail_client(*args, **kwargs):
        raise AssertionError("vuln prompt builder must not summarize per agent")

    contexts = []
    with patch.object(activities, "_make_recon_summary_llm_client", side_effect=fail_client):
        for _ in range(5):
            values = await activities._build_vuln_prompt_variables(
                _input(tmp_path), {})
            contexts.append(values["RECON_CONTEXT"])
            assert "FRAMEWORK_ANALYSIS" in values

    assert contexts == ["shared context"] * 5


@pytest.mark.asyncio
async def test_vuln_prompt_builder_injects_sectioned_context_in_fixed_order(tmp_path):
    """digest sections 非空 → RECON_CONTEXT 按固定节序重组（spec §4.4，第一期不路由）。"""
    deliverables, _ = _setup(tmp_path)
    digest_path = deliverables / "intermediate" / "recon_context_digest.json"
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.write_text(json.dumps({
        "schema_version": 2,
        "source": "llm-summary",
        "degraded": False,
        "degraded_reason": None,
        "source_hash": hashlib.sha256(RECON_MD.encode("utf-8")).hexdigest(),
        "summarizer_prompt_version": activities.RECON_CONTEXT_SUMMARIZER_PROMPT_VERSION,
        "language": "zh",
        "text": "raw llm output",
        "sections": {
            "auth": "- session cookie",
            "_unparsed": "- stray lead",
            "xss": "- render t.html",
            "ssrf": "- fetch user url",
            "endpoints": "- GET /foo (user)",
            "injection": "- exec(user.input)",
            "authz": "- no ownership check",
        },
    }), encoding="utf-8")

    async def fail_client(*args, **kwargs):
        raise AssertionError("sectioned digest must not trigger per-agent LLM summary")

    with patch.object(activities, "_make_recon_summary_llm_client", side_effect=fail_client):
        values = await activities._build_vuln_prompt_variables(_input(tmp_path), {})

    ctx = values["RECON_CONTEXT"]
    # "## auth\n" 带换行锚定，避免匹配到 "## authz" 的前缀
    assert ctx.index("## endpoints") < ctx.index("## authz") < ctx.index("## injection") \
        < ctx.index("## xss") < ctx.index("## ssrf") < ctx.index("## auth\n") \
        < ctx.index("## additional"), "节序应固定：六节序 + _unparsed 挂尾"
    assert "- GET /foo (user)" in ctx
    assert "- stray lead" in ctx
    assert ctx.startswith("## endpoints")


@pytest.mark.asyncio
async def test_missing_digest_uses_deterministic_extract_without_llm(tmp_path):
    deliverables, _ = _setup(tmp_path)

    async def fail_client(*args, **kwargs):
        raise AssertionError("missing digest must not trigger per-agent LLM summary")

    with patch.object(activities, "_make_recon_summary_llm_client", side_effect=fail_client):
        values = await activities._build_vuln_prompt_variables(
            _input(tmp_path), {})

    assert "GET /foo" in values["RECON_CONTEXT"]
    assert "## 5. Potential Input Vectors" not in values["RECON_CONTEXT"]
