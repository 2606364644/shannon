"""P3c 阶段 1 穿线不变量：web 提交的 provider_config 全程到 run_claude_prompt。

数据层不变量（无 mock，纯 dataclass 断言）：
1. PipelineInput.provider_config 经 workflows.py 灌入 ActivityInput 不丢；
2. 后续 activity 经 **act_input.__dict__ 复制，provider_config 保留；
3. asdict(ProviderConfig) 键名 == dataclass 字段，ProviderConfig(**dict) 可还原
   （run_claude_prompt:154 ProviderConfig(**provider_config) 成立的前提）。
"""
from dataclasses import asdict

from supernova_core.agents.providers import build_provider_config
from supernova_core.agents.runner import ProviderConfig
from supernova_whitebox.pipeline.shared import ActivityInput, PipelineInput


def test_pipeline_input_provider_config_survives_act_input_construction():
    """PipelineInput.provider_config 经 workflows.py:129 灌入 ActivityInput 不丢。"""
    pc = asdict(build_provider_config(provider_type="openai_compatible"))
    inp = PipelineInput(provider_config=pc)
    # 模拟 workflows.py:129 的灌入
    act = ActivityInput(repo_path="/r", provider_config=inp.provider_config)
    assert act.provider_config is pc
    assert act.provider_config["type"] == "openai_compatible"


def test_act_input_inherits_via_dict_splat():
    """后续 activity 经 **act_input.__dict__ 复制，provider_config 保留。"""
    act = ActivityInput(repo_path="/r", provider_config={"type": "x"})
    act2 = ActivityInput(**{**act.__dict__, "phase": "recon"})
    assert act2.provider_config == {"type": "x"}
    assert act2.phase == "recon"


def test_provider_config_dict_keys_match_providerconfig_fields():
    """asdict(ProviderConfig) 的键名 == dataclass 字段，ProviderConfig(**dict) 可还原。

    这是 run_claude_prompt:154 ProviderConfig(**provider_config) 成立的前提。
    """
    pc = asdict(build_provider_config())
    restored = ProviderConfig(**pc)
    assert restored.type == pc["type"]
