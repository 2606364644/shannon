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


def test_attack_chain_prompt_includes_output_language(monkeypatch):
    """回归：attack-chain.txt 必须经 @include 注入输出语言指令。

    历史 bug（2026-08-03）：i18n 化时漏给 attack-chain.txt 补 _output-language，
    导致 LLM 攻击链正文（name/description/steps）全英文，而渲染层标题/标签已双语
    —— 报告里攻击链章节标题「## 攻击链（多步利用路径）」是中文，正文却是英文。
    5 个 vuln-*.txt 均已 include，attack-chain.txt 须对齐。
    """
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    from supernova_core.prompts.manager import PromptManager

    content = (PROMPTS_DIR / "attack-chain.txt").read_text("utf-8")
    resolved = PromptManager(PROMPTS_DIR)._process_includes(content, PROMPTS_DIR)
    assert "简体中文" in resolved  # zh 语言指令经 include 注入


def test_attack_chain_prompt_is_pure_combiner():
    """防回退锁（2026-08-19 收窄）：attack-chain 是纯组合器，不发现漏洞。

    用户决策：只组合 exploitation_queue 里 vuln agent 已发现的漏洞。
    - 组合器身份 + 每步溯源字段 source_finding（queue + #index）
    - 全局禁发现（role 层）+ 缺 queue 不补挖（该类留白）
    - 发现式指令须删净：extend with your own grep / not a complete picture /
      verifier 发现代方 / phase 阶段叙事（下游零消费）
    - _code-path-rules include 已删："Analyze every entry" 是发现型语义，
      与纯组合器禁令自相矛盾
    """
    content = (PROMPTS_DIR / "attack-chain.txt").read_text("utf-8")
    # 组合器身份 + 溯源字段
    assert "Combiner" in content
    assert "source_finding" in content
    # 全局禁发现 + 缺 queue 不补挖
    assert "do NOT discover" in content or "NEVER discover" in content
    assert "no compensating discovery" in content
    # 发现式指令须已删
    assert "extend with your own grep" not in content
    assert "not a complete picture" not in content
    assert "Stored XSS verifier" not in content
    # phase 字段（旧阶段叙事遗产，下游零消费）已删
    assert '"phase"' not in content
    # code-path-rules include 已删（发现型语义冲突）
    assert "_code-path-rules" not in content


def test_attack_chain_prompt_chain_length_is_open():
    """防回退锁：链长开放——示例省略号 + 明文声明，长度跟 finding 走。

    历史 bug 方向：5 步示例教模型凑长 / 2 步示例教模型压短。设计定稿为
    示例不携带长度信号，长度规则明文化（2 到 N 步由 finding 衔接决定）。
    """
    content = (PROMPTS_DIR / "attack-chain.txt").read_text("utf-8")
    assert "chain length is OPEN" in content
    # 跨类组合合法（source_finding 可引任意 queue），vuln_type 标主导类
    assert "ANY queue" in content
    # 组合不出时空链是合法结果，不硬凑
    assert '{"chains": []}' in content


def test_assembler_only_reads_gitnexus_own_output():
    """assembler 读 gitnexus_queue（确定性层自己产物），不反向喂 LLM 轨。"""
    import inspect
    from supernova_core.code_index import attack_chain_assembler
    src = inspect.getsource(attack_chain_assembler)
    # assembler 不读 recon_deliverable（LLM 轨）——只读 gitnexus_queue
    # （它由 activity 喂 findings，自身不读文件，source 不含 recon 读文件逻辑）
    assert "recon_deliverable" not in src
