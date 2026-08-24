from dataclasses import dataclass, field


@dataclass
class CorrelationPipelineInput:
    """web → worker 的关联阶段入参（Temporal 参数，全字段可序列化）。"""
    config_path: str
    repo_workspace_paths: dict[str, str] = field(default_factory=dict)
    out_ws_dir: str = ""
    event_file: str = ""
    provider_config: dict | None = None
    env_overrides: dict[str, str] = field(default_factory=dict)
    pipeline_testing: bool = False
    # web 编排收尾（_ensure_scan_end 写终态）；CLI 直跑 True。默认 False（worker 由 web 提交）。
    write_scan_end: bool = False
