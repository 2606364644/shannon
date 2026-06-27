"""Anchor: run_auth_config_scan 的 workflow 调用必须被 try/except 包裹（non-fatal），
与同轨 run_authz_gitnexus_judge / run_gitnexus_chain_verdict 一致。
df33ec5 时它无 try/except，失败会中断整个 vulnerability-analysis 阶段。"""
from pathlib import Path


def test_auth_config_scan_call_is_non_fatal():
    wf = Path(__file__).resolve().parents[1] / "src/shannon_whitebox/pipeline/workflows.py"
    src = wf.read_text()
    idx = src.find("activities.run_auth_config_scan")
    assert idx != -1, "run_auth_config_scan 调用未找到"
    # 调用前 200 字符内应有 try:；调用后 600 字符内应有 warning 标注 non-fatal
    # （窗口从 400 扩到 600：Task 5 在 warning 前加了 success info 日志，块变长。）
    before = src[max(0, idx - 200):idx]
    after = src[idx:idx + 600]
    assert "try:" in before, (
        "run_auth_config_scan 调用必须包裹在 try: 中（non-fatal），"
        "与 run_authz_gitnexus_judge / run_gitnexus_chain_verdict 一致"
    )
    assert "Auth config scan failed" in after, (
        "run_auth_config_scan 失败应有 warning 日志"
    )
