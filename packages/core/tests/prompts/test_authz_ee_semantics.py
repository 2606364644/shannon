"""Spec 2026-08-04 改动 A: authz/auth prompt 必须定义 externally_exploitable 的
网络可达性 (reachability) 语义。

真机根因 (delivery-20260804-024910): authz/auth prompt 从未定义 ee 语义, LLM 凭
英文 "externally exploitable" 字面把它猜成 authentication_required 的反面 ——
"需要登录 → 不可外部利用 → ee=False"。结果 20 条 authz 里 19 条 ee 误判 False,
公网可达的授权漏洞的 reachability 标签被错判 (当时 ee 还是 PoC 门控, 致丢失 PoC;
2026-08-11 起 PoC 门控已与 ee 解耦（poc_generator 已于 2026-08-27 随 poc-agent 直产退役），但 ee 语义仍需正确)。

对比 vuln-injection.txt:185-188 明确定义了 ee=reachability tag。本测试锁定:
authz/auth 的 4 个 prompt 必须含 ee 可达性语义定义, 防回归到"零定义"。
(xss/ssrf 的 ee 语义由 test_xss_ssrf_ee_semantics.py 锁定。)

不违反 CLAUDE.md §1 铁律: 本次仅澄清结构化输出字段语义, 不引入确定性层产物、
不 @include 确定性 hints (test_static_dataflow_hints_decoupling.py 不受影响)。
"""
from pathlib import Path

# parents[4] = repo root (holds prompts/)。同 test_static_dataflow_hints_decoupling.py。
PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"

TARGET_PROMPTS = {
    "vuln-authz.txt": PROMPTS_DIR / "vuln-authz.txt",
    "vuln-auth.txt": PROMPTS_DIR / "vuln-auth.txt",
    "authz_gitnexus_judge.txt": PROMPTS_DIR / "authz_gitnexus_judge.txt",
    "authz_gitnexus_explore.txt": PROMPTS_DIR / "authz_gitnexus_explore.txt",
}


def test_authz_prompts_define_ee_reachability_semantics():
    """每个 authz/auth prompt 必须把 externally_exploitable 定义为 reachability tag,
    并明确"需要登录 ≠ false"。缺任一关键词即判失败。"""
    missing = {}
    for name, path in TARGET_PROMPTS.items():
        assert path.exists(), f"prompt 不存在: {path}"
        text = path.read_text().lower()
        # 核心不变量: (1) ee 是 reachability tag (非 auth 要求); (2) 需登录 ≠ false
        has_reachability = "reachability" in text
        has_login_distinction = "requires login" in text
        if not (has_reachability and has_login_distinction):
            missing[name] = {
                "reachability": has_reachability,
                "requires_login_distinction": has_login_distinction,
            }
    assert not missing, (
        f"Spec 2026-08-04 改动 A: 这些 authz/auth prompt 缺 externally_exploitable "
        f"可达性语义定义 (LLM 会把 ee 误判为 authentication_required 反面 → 公网 "
        f"authz 漏洞丢 PoC): {missing}"
    )
