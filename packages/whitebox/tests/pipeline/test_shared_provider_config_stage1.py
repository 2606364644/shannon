"""P3c 阶段 1：PipelineInput/ActivityInput 加 provider_config 字段。"""
from supernova_whitebox.pipeline.shared import PipelineInput, ActivityInput
from supernova_blackbox.pipeline.shared import BlackboxPipelineInput, BlackboxActivityInput


def test_whitebox_pipeline_input_provider_config_default_none():
    assert PipelineInput().provider_config is None


def test_whitebox_pipeline_input_provider_config_set():
    inp = PipelineInput(provider_config={"type": "openai_compatible", "api_key": "sk-x"})
    assert inp.provider_config == {"type": "openai_compatible", "api_key": "sk-x"}


def test_whitebox_activity_input_provider_config_default_none():
    assert ActivityInput(repo_path="/r").provider_config is None


def test_whitebox_activity_input_provider_config_set():
    act = ActivityInput(repo_path="/r", provider_config={"type": "anthropic_api"})
    assert act.provider_config == {"type": "anthropic_api"}


def test_blackbox_pipeline_input_provider_config_field():
    assert BlackboxPipelineInput().provider_config is None
    assert BlackboxPipelineInput(provider_config={"type": "x"}).provider_config == {"type": "x"}


def test_blackbox_activity_input_provider_config_field():
    assert BlackboxActivityInput(web_url="http://x").provider_config is None
    assert BlackboxActivityInput(web_url="http://x", provider_config={"type": "x"}).provider_config == {"type": "x"}
