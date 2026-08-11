from supernova_core.renderers.pre_recon import render_pre_recon

__all__ = ["render_pre_recon", "render_deliverable", "build_exploit_verdicts_payload"]


def render_deliverable(agent_name, data: dict, deliverables_path=None, queue_root=None) -> "str | None":
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
    from supernova_core.models.agents import AgentName

    if agent_name == AgentName.PRE_RECON:
        return render_pre_recon(data)
    if agent_name == AgentName.RECON:
        from supernova_core.renderers.recon import render_recon

        return render_recon(data)
    if isinstance(agent_name, AgentName) and agent_name.value.endswith("-vuln"):
        vc = agent_name.value.removesuffix("-vuln")
        from supernova_core.renderers.vuln import render_vuln

        return render_vuln(vc, data)
    if isinstance(agent_name, AgentName) and agent_name.value.endswith("-exploit"):
        vc = agent_name.value.removesuffix("-exploit")
        return _render_exploit_deliverable(vc, data, deliverables_path, queue_root)
    return None


def _build_exploit_validation(vc, data, deliverables_path, queue_root=None):
    """读 queue → validate，返回 (validation, id_to_type, id_to_title)。

    抽自 _render_exploit_deliverable，供 md 渲染 + verdicts.json payload 构造共用
    （spec 2026-08-12：renderer 保持纯函数，payload 构造复用同一 validation 源，
    避免改 render_deliverable 公共签名牵连 ~16 个调用点）。
    """
    import json
    from pathlib import Path

    from supernova_core.collectors.exploit import validate_exploit_verdicts

    valid_ids: set[str] = set()
    id_to_type: dict[str, str] = {}
    id_to_title: dict[str, str] = {}
    if deliverables_path is not None:
        from supernova_core.utils.paths import resolve_track_deliverable, WHITEBOX_SUBDIR

        # 读 queue 的根：queue_root 优先（黑盒 = 白盒 repo_path/deliverables，queue 在
        # whitebox/ 子目录）；缺省回落 deliverables_path（whitebox：已含 whitebox/ 或平铺）。
        # resolve_track_deliverable 双路径 fallback 让两种结构都命中（spec 2026-08-08 根因修复：
        # 黑盒 exploit 链读 queue 用对白盒根，不再读黑盒自己空目录导致 valid_ids 空）。
        read_root = queue_root if queue_root is not None else deliverables_path
        queue_path = resolve_track_deliverable(Path(read_root), WHITEBOX_SUBDIR, f"{vc}_exploitation_queue.json")
        if queue_path.exists():
            try:
                from supernova_core.models.queue_schemas import VulnerabilityQueue

                parsed = VulnerabilityQueue.parse_lenient(queue_path.read_text(encoding="utf-8"))
                for v in parsed.queue.vulnerabilities:
                    vid = getattr(v, "ID", None)
                    if vid:
                        valid_ids.add(vid)
                        id_to_type[vid] = getattr(v, "vulnerability_type", vc)
                        title = getattr(v, "title", None)
                        if title:
                            id_to_title[vid] = title
            except (json.JSONDecodeError, OSError):
                pass
    entries = (data or {}).get("verdicts", []) if isinstance(data, dict) else (data or [])
    validation = validate_exploit_verdicts(entries, valid_ids)
    return validation, id_to_type, id_to_title


def _render_exploit_deliverable(vc, data, deliverables_path, queue_root=None):
    from supernova_core.renderers.exploit import render_exploit

    validation, id_to_type, id_to_title = _build_exploit_validation(
        vc, data, deliverables_path, queue_root)
    return render_exploit(vc, validation, id_to_type, id_to_title)


def build_exploit_verdicts_payload(vc, data, deliverables_path, queue_root=None) -> dict:
    """构造 ``{vc}_exploit_verdicts.json`` payload（补全主线缺失产物，spec 2026-08-12）。

    schema = {vuln_class, accepted_ids, verdicts, rejected}（孤儿消费者测试 schema 的超集）：
    - accepted_ids：所有 accepted verdict 的 id（exploited+blocked+potential+other），
      coverage/PoC 消费者读此字段（凡 accepted 即算覆盖）。
    - verdicts：完整 accepted verdict（含 status），计数器据此数 exploited。
    - rejected：[{id, reason}]（L1/L2/L3 拒因，调试可见性）。
    复用 _build_exploit_validation → 与 evidence.md 渲染同源同口径。
    """
    validation, _, _ = _build_exploit_validation(vc, data, deliverables_path, queue_root)
    return {
        "vuln_class": vc,
        "accepted_ids": [v.vulnerability_id for v in validation.accepted],
        "verdicts": [v.model_dump() for v in validation.accepted],
        "rejected": [
            {
                "id": (raw.get("vulnerability_id", "<unknown>")
                       if isinstance(raw, dict) else "<unknown>"),
                "reason": reason,
            }
            for raw, reason in validation.rejected
        ],
    }
