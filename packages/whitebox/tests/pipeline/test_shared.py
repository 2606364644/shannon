"""PipelineInput.event_file 字段：web 提交端塞路径，worker activity 读它写 events.ndjson。"""
from shannon_whitebox.pipeline.shared import PipelineInput


def test_pipeline_input_has_event_file_field():
    """event_file 字段存在，默认 None（CLI 不显式传时为 None，走 env 兜底）。"""
    inp = PipelineInput(repo_path="/r")
    assert inp.event_file is None


def test_pipeline_input_event_file_round_trips():
    """web 提交端塞 event_file，input 序列化经 temporal 后字段保留。"""
    inp = PipelineInput(repo_path="/r", workspace_name="ws", event_file="/workspaces/ws/events.ndjson")
    assert inp.event_file == "/workspaces/ws/events.ndjson"
