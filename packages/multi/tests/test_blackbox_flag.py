from supernova_blackbox.pipeline.shared import BlackboxPipelineInput


def test_correlated_workspace_field_default_none():
    inp = BlackboxPipelineInput(web_url="http://x")
    assert inp.correlated_workspace is None


def test_correlated_workspace_field_set():
    inp = BlackboxPipelineInput(web_url="http://x", correlated_workspace="my-corr")
    assert inp.correlated_workspace == "my-corr"
