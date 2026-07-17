from shannon_core.renderers.pre_recon import render_pre_recon

__all__ = ["render_pre_recon", "render_deliverable"]


def render_deliverable(agent_name, data: dict, deliverables_path=None) -> "str | None":
    """按 agent 分发 renderer。

    - Plan 1: pre-recon 走 render_pre_recon。
    - Plan 2: recon 走 ReconCollector/render_recon。
    - Plan 3: 5 个 vuln agent（``<vc>-vuln``）共用 vuln renderer，按 vc branching。
    - Plan 4: 5 个 exploit agent（``<vc>-exploit``）：data = collector.get_all()（含
      "verdicts" list，append section）。需 deliverables_path 读 ``{vc}_exploitation_queue.json``
      取 valid_ids + id_to_type，跑 validate_exploit_verdicts → render_exploit。
    - 其余 agent 返 None（无 collector 通道，走 self-Write 路径）。

    vc 派生与 ``collectors/__init__.py::make_collector`` 同源（无字典、无漂移、
    无跨模块 import）；executor.py 依赖此一致性：collector 通道开 ⇒ renderer 必配套。

    ``deliverables_path`` 仅 ``-exploit`` 分支使用（读 queue）；pre_recon / recon /
    ``-vuln`` 分支向后兼容，多传不影响（默认 None）。
    """
    from shannon_core.models.agents import AgentName

    if agent_name == AgentName.PRE_RECON:
        return render_pre_recon(data)
    if agent_name == AgentName.RECON:
        from shannon_core.renderers.recon import render_recon

        return render_recon(data)
    if isinstance(agent_name, AgentName) and agent_name.value.endswith("-vuln"):
        vc = agent_name.value.removesuffix("-vuln")
        from shannon_core.renderers.vuln import render_vuln

        return render_vuln(vc, data)
    if isinstance(agent_name, AgentName) and agent_name.value.endswith("-exploit"):
        vc = agent_name.value.removesuffix("-exploit")
        return _render_exploit_deliverable(vc, data, deliverables_path)
    return None


def _render_exploit_deliverable(vc, data, deliverables_path):
    import json
    from pathlib import Path

    from shannon_core.collectors.exploit import validate_exploit_verdicts
    from shannon_core.renderers.exploit import render_exploit

    valid_ids: set[str] = set()
    id_to_type: dict[str, str] = {}
    if deliverables_path is not None:
        queue_path = Path(deliverables_path) / f"{vc}_exploitation_queue.json"
        if queue_path.exists():
            try:
                from shannon_core.models.queue_schemas import VulnerabilityQueue

                parsed = VulnerabilityQueue.parse_lenient(queue_path.read_text(encoding="utf-8"))
                for v in parsed.queue.vulnerabilities:
                    vid = getattr(v, "ID", None)
                    if vid:
                        valid_ids.add(vid)
                        id_to_type[vid] = getattr(v, "vulnerability_type", vc)
            except (json.JSONDecodeError, OSError):
                pass
    entries = (data or {}).get("verdicts", []) if isinstance(data, dict) else (data or [])
    validation = validate_exploit_verdicts(entries, valid_ids)
    return render_exploit(vc, validation, id_to_type)
