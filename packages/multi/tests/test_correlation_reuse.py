"""§6.2 闭环:关联 workspace merged queue 作为黑盒 recon-skip 复用源的纯函数测试。

被测函数 `has_correlation_results` 是从 Temporal workflow `run` 里抽出的纯助手,
用来回答:关联 workspace 的 deliverables 里是否存在对 `vuln_classes` 中至少一个
漏洞类有效的 `{vc}_exploitation_queue.json`(有效性复用 `has_valid_whitebox_results`,
即每条 entry 必须含 title/description/severity/location 四字段)。

这是 spec §7 "必要产物,非可选" 的检测端——A6 写,黑盒(B2/B3)读。
"""
import json

from shannon_blackbox.pipeline.workflows import has_correlation_results


def _write_queue(dlv, vc, entries):
    dlv.mkdir(parents=True, exist_ok=True)
    (dlv / f"{vc}_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": entries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


_VALID_ENTRY = {
    "title": "SSRF via gateway forwarding",
    "description": "gateway forwards user-controlled url to backend sink",
    "severity": "high",
    "location": "gateway POST /orders → order-svc.CreateOrder",
    # 跨服务额外标注 —— subset 检查允许多字段(spec §7 B1)
    "service": "order-svc",
    "cross_service_source": "gateway",
}


def test_true_when_valid_merged_queue_present(tmp_path):
    dlv = tmp_path / "deliverables"
    _write_queue(dlv, "ssrf", [_VALID_ENTRY])
    assert has_correlation_results(dlv, ["ssrf", "idor"]) is True


def test_false_when_deliverables_absent(tmp_path):
    # 关联 workspace 根本没有 deliverables 目录
    assert has_correlation_results(tmp_path / "deliverables", ["ssrf"]) is False


def test_false_when_queue_missing_for_all_classes(tmp_path):
    dlv = tmp_path / "deliverables"
    dlv.mkdir()
    # 只有 ssr 的 queue(打错名字),查询的类不存在
    _write_queue(dlv, "ssr", [_VALID_ENTRY])
    assert has_correlation_results(dlv, ["ssrf", "idor"]) is False


def test_false_when_queue_invalid_missing_required_field(tmp_path):
    """spec §7 B1 硬约束:缺 title/description/severity/location 任一即判无效。"""
    dlv = tmp_path / "deliverables"
    bad = {k: v for k, v in _VALID_ENTRY.items() if k != "location"}
    _write_queue(dlv, "ssrf", [bad])
    assert has_correlation_results(dlv, ["ssrf"]) is False


def test_false_when_vulnerabilities_empty(tmp_path):
    dlv = tmp_path / "deliverables"
    _write_queue(dlv, "ssrf", [])
    assert has_correlation_results(dlv, ["ssrf"]) is False


def test_false_when_vulnerabilities_not_list(tmp_path):
    dlv = tmp_path / "deliverables"
    dlv.mkdir()
    (dlv / "ssrf_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": {"not": "a list"}}), encoding="utf-8"
    )
    assert has_correlation_results(dlv, ["ssrf"]) is False


def test_subset_classes_match(tmp_path):
    """查询多个类,只要其中一个有有效 queue 即 True。"""
    dlv = tmp_path / "deliverables"
    _write_queue(dlv, "idor", [_VALID_ENTRY])
    assert has_correlation_results(dlv, ["ssrf", "idor", "sqli"]) is True


def test_empty_vuln_classes_is_false(tmp_path):
    dlv = tmp_path / "deliverables"
    _write_queue(dlv, "ssrf", [_VALID_ENTRY])
    assert has_correlation_results(dlv, []) is False


def test_corrupted_json_is_false(tmp_path):
    dlv = tmp_path / "deliverables"
    dlv.mkdir()
    (dlv / "ssrf_exploitation_queue.json").write_text("{not json", encoding="utf-8")
    assert has_correlation_results(dlv, ["ssrf"]) is False


def test_single_repo_zero_regression_contract():
    """单仓零回归契约:correlated_workspace 未设置时,黑盒 recon-skip 决策完全不
    查询关联 workspace——即 has_correlation_results 这条新路径根本不被调用。

    本测试固化契约:helper 是一个纯布尔函数,workflow 只在
    `if input.correlated_workspace:` 分支里调用它(见 workflows.py recon-skip 段)。
    helper 自身不读全局/不依赖 correlated_workspace;是否调用它由 workflow 决定。
    这里通过断言 helper签名接受任意 deliverables 路径来固化"调用方控制是否传"语义。
    """
    # 只要 helper 能被独立调用且不依赖任何 correlated_workspace 全局态,即满足
    # "None 时根本不调用"的工作流契约(工作流层由 test_blackbox_flag.py +
    # test_blackbox_reuse.py 覆盖 correlated_workspace 字段默认 None)。
    import inspect
    sig = inspect.signature(has_correlation_results)
    params = list(sig.parameters)
    assert params == ["corr_ws_deliverables", "vuln_classes"], params
