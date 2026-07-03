from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"


def test_attack_chain_prompt_exists():
    content = (PROMPTS_DIR / "attack-chain.txt").read_text("utf-8")
    assert "attack chain" in content.lower() or "攻击链" in content
    # 多步链类型
    assert "stored XSS" in content or "storage" in content.lower()
    assert "IDOR" in content or "privilege" in content.lower()


def test_attack_chain_prompt_reads_llm_track_sources():
    """守铁律：prompt 指示读 LLM 轨产物（recon + exploitation_queue），不引确定性层。"""
    content = (PROMPTS_DIR / "attack-chain.txt").read_text("utf-8")
    assert "recon_deliverable.md" in content or "exploitation_queue" in content
    for tok in ("parameter_graph", "SinkCallSite", "static_dataflow_hints", "gitnexus_queue"):
        assert tok not in content, f"attack-chain.txt 引确定性 token: {tok}"


def test_attack_chain_prompt_decoupled_global():
    """解耦测试 rglob 覆盖：无 FORBIDDEN token。"""
    content = (PROMPTS_DIR / "attack-chain.txt").read_text("utf-8")
    for tok in ("parameter_graph", "SinkCallSite", "static_dataflow_hints"):
        assert tok not in content
