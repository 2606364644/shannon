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


def test_attack_chain_pipeline_does_not_feed_gitnexus_to_llm_prompt():
    """守铁律：GitNexus 轨产物（gitnexus_queue）不被 attack-chain.txt 引用。"""
    content = (PROMPTS_DIR / "attack-chain.txt").read_text("utf-8")
    # attack-chain.txt 只读 exploitation_queue（LLM 轨），不读 gitnexus_queue
    assert "gitnexus_queue" not in content
    assert "exploitation_queue" in content


def test_assembler_only_reads_gitnexus_own_output():
    """assembler 读 gitnexus_queue（确定性层自己产物），不反向喂 LLM 轨。"""
    import inspect
    from shannon_core.code_index import attack_chain_assembler
    src = inspect.getsource(attack_chain_assembler)
    # assembler 不读 recon_deliverable（LLM 轨）——只读 gitnexus_queue
    # （它由 activity 喂 findings，自身不读文件，source 不含 recon 读文件逻辑）
    assert "recon_deliverable" not in src
