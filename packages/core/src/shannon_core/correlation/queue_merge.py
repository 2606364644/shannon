from shannon_core.utils.paths import REQUIRED_VULN_FIELDS


def merge_exploitation_queues(per_repo: dict[str, list[dict]]) -> list[dict]:
    """合并 N 仓 exploitation_queue 的 vulnerabilities。

    B1 硬约束:每条 entry 必须含 title/description/severity/location
    (黑盒 has_valid_whitebox_results subset 检查);缺字段的丢弃。
    跨服务标注用额外 service 字段,不破坏检测。
    """
    merged: list[dict] = []
    for service, entries in per_repo.items():
        for e in entries:
            if not isinstance(e, dict):
                continue
            if not REQUIRED_VULN_FIELDS.issubset(e.keys()):
                continue
            tagged = dict(e)
            tagged["service"] = service
            tagged.setdefault("cross_service_source", None)
            merged.append(tagged)
    return merged
