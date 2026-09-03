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


@dataclass
class TopologyAnalysisInput:
    """web → worker 的拓扑预分析入参（Temporal 参数，全字段可序列化）。

    web 侧 _start 已完成校验/manifest/fingerprint/缓存判定并落 queued state.json；
    worker activity 只负责执行段（prompt 组装 + agent + 终态写入，spec
    2026-09-03 §4.1）。timeout/max_turns 由 web 读同一 env 组入——worker 不再
    读 env，防两侧默认值漂移（spec R5）。prompt 不在 input 里——worker 侧组装。
    """
    analysis_id: str
    ws: str
    workspaces_dir: str  # 共享卷 workspaces 根（store 构造用，web/worker 同挂载）
    repos: list[str] = field(default_factory=list)
    repo_paths: dict[str, str] = field(default_factory=dict)
    manifest: dict = field(default_factory=dict)
    provider_config: dict | None = None
    env_overrides: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 900.0
    max_turns: int = 30
