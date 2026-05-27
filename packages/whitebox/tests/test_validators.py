import pytest
from pathlib import Path
from shannon_core.models.agents import AgentName
from shannon_whitebox.agents.validators import validate_deliverable, get_vuln_type, get_queue_filename

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
