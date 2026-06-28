def merge_exploitation_queues(per_repo: dict[str, list[dict]]) -> list[dict]:
    """合并 N 仓 exploitation_queue 的 vulnerabilities(对齐原始 TS)。

    每条 entry 只要求是 dict,跨服务标注 ``service`` / ``cross_service_source``。不校验
    条目内部字段——``title``/``description``/``severity``/``location`` 是 exploit 阶段
    字段,非 vuln queue 字段(重构早期误植的 subset 检查会让真实 vuln 条目——字段实为
    ``ID``/``vulnerability_type``/``source``/...——全部被丢弃,跨仓合并恒空)。条目级
    容错交给 ``VulnerabilityQueue.parse_lenient``。
    """
    merged: list[dict] = []
    for service, entries in per_repo.items():
        for e in entries:
            if not isinstance(e, dict):
                continue
            tagged = dict(e)
            tagged["service"] = service
            tagged.setdefault("cross_service_source", None)
            merged.append(tagged)
    return merged
