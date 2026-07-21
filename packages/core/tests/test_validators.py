import pytest
from pathlib import Path
from supernova_core.models.agents import AgentName
from supernova_core.models.errors import ErrorCode, PentestError
from supernova_core.agents.validators import validate_deliverable, get_vuln_type, get_queue_filename

def test_get_vuln_type():
    assert get_vuln_type(AgentName.INJECTION_VULN) == "injection"
    assert get_vuln_type(AgentName.XSS_VULN) == "xss"
    assert get_vuln_type(AgentName.PRE_RECON) is None

def test_get_queue_filename():
    assert get_queue_filename(AgentName.INJECTION_VULN) == "injection_exploitation_queue.json"
    assert get_queue_filename(AgentName.AUTH_VULN) == "auth_exploitation_queue.json"
    assert get_queue_filename(AgentName.PRE_RECON) is None

@pytest.mark.asyncio
async def test_validate_deliverable_exists(tmp_path):
    (tmp_path / "pre_recon_deliverable.md").write_text("# Analysis")
    assert await validate_deliverable(tmp_path, AgentName.PRE_RECON)

@pytest.mark.asyncio
async def test_validate_deliverable_missing(tmp_path):
    with pytest.raises(Exception, match="Missing deliverable"):
        await validate_deliverable(tmp_path, AgentName.PRE_RECON)

def test_get_vuln_type_exploit_agents():
    assert get_vuln_type(AgentName.INJECTION_EXPLOIT) == "injection"
    assert get_vuln_type(AgentName.XSS_EXPLOIT) == "xss"
    assert get_vuln_type(AgentName.AUTH_EXPLOIT) == "auth"
    assert get_vuln_type(AgentName.SSRF_EXPLOIT) == "ssrf"
    assert get_vuln_type(AgentName.AUTHZ_EXPLOIT) == "authz"

def test_get_queue_filename_exploit_agents():
    assert get_queue_filename(AgentName.INJECTION_EXPLOIT) == "injection_exploitation_queue.json"
    assert get_queue_filename(AgentName.XSS_EXPLOIT) == "xss_exploitation_queue.json"
    assert get_queue_filename(AgentName.AUTH_EXPLOIT) == "auth_exploitation_queue.json"


@pytest.mark.asyncio
async def test_validate_deliverable_vuln_queue_missing_raises(tmp_path):
    """对齐原始 TS createVulnValidator (shannon/apps/worker/src/session-manager.ts:136-146):
    *-vuln agent 必须落盘 {vt}_exploitation_queue.json —— 它由 executor.py 从
    result.structured_output 写盘。agent 偶发不走结构化输出通道(GLM 长任务失忆,
    如 NodeGoat injection-vuln Turn 56 / authz-vuln Turn 72)时该文件缺失 →
    OUTPUT_VALIDATION_FAILED → classify_error_for_temporal 判 retryable=True →
    Temporal 重跑,而非静默漏盘(否则 merge continue + 黑盒 preflight No results)。
    回归锚点:NodeGoat injection/authz exploitation_queue 漏盘。"""
    # deliverable.md 在,但 exploitation_queue.json 缺失(模拟 agent 未产 structured output)
    (tmp_path / "injection_analysis_deliverable.md").write_text("# ok")
    with pytest.raises(PentestError, match="Missing exploitation queue") as exc:
        await validate_deliverable(tmp_path, AgentName.INJECTION_VULN)
    assert exc.value.error_code == ErrorCode.OUTPUT_VALIDATION_FAILED


@pytest.mark.asyncio
async def test_validate_deliverable_vuln_queue_present_passes(tmp_path):
    """正常路径: *-vuln agent 的 deliverable.md 与 exploitation_queue.json 均在 → 通过。
    防止校验逻辑过度(把所有 -vuln 都判失败)。空 vulnerabilities 数组也是合法结果
    (agent 判定该类无漏洞),故只校验文件存在。"""
    (tmp_path / "authz_analysis_deliverable.md").write_text("# ok")
    (tmp_path / "authz_exploitation_queue.json").write_text('{"vulnerabilities": []}')
    assert await validate_deliverable(tmp_path, AgentName.AUTHZ_VULN) is True


@pytest.mark.asyncio
async def test_validate_deliverable_exploit_agent_skips_queue_check(tmp_path):
    """*-exploit agent 不产 exploitation_queue.json(TS createExploitValidator 是 no-op),
    故即便该文件缺失也不应抛 queue 错 —— 防止把校验条件误写成 get_vuln_type(...)
    (它对 -exploit 也返回非 None,会误伤)。exploit 的 deliverable 是 *_evidence.md。"""
    (tmp_path / "injection_exploitation_evidence.md").write_text("# ok")
    # 不预置任何 exploitation_queue.json —— exploit agent 不该被校验
    assert await validate_deliverable(tmp_path, AgentName.INJECTION_EXPLOIT) is True
