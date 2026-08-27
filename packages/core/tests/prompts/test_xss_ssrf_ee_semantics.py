"""xss/ssrf vuln prompt 必须定义 externally_exploitable 的网络可达性 (reachability) 语义。

历史:只有 vuln-injection.txt 写了完整 ee 语义("not an admission gate" /
"regardless of reachability"),authz/auth 有弱化版,vuln-xss.txt / vuln-ssrf.txt
长期只有 schema 行 `"externally_exploitable": true | false`、零语义说明 —— LLM 凭
"externally exploitable" 字面易把 ee 误判成 authentication_required 的反面
("需登录 → 不可外部利用 → false")。

2026-08-11:给 vuln-xss.txt / vuln-ssrf.txt 补 <externally_exploitable_semantics>
段(对齐 vuln-authz.txt)。本测试锁定这两个 prompt 必须含 reachability 语义 +
"requires login" 区分,防回归到"零定义"。

注:PoC 门控已于同期从 externally_exploitable 解耦(后随 poc-agent 直产退役,
"能拼出 HTTP 形态即生成,纯非 HTTP 入口自然 skip"),ee 不再决定是否有 PoC;
但 ee 仍是 queue 里的 reachability tag(报告严重性维度),语义正确性仍需保证。

不违反 CLAUDE.md §1 铁律:仅澄清结构化输出字段语义,不引入确定性层产物、
不 @include 确定性 hints(test_static_dataflow_hints_decoupling.py 不受影响)。
"""
from pathlib import Path

# parents[4] = repo root (holds prompts/)。同 test_authz_ee_semantics.py。
PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"

TARGET_PROMPTS = {
    "vuln-xss.txt": PROMPTS_DIR / "vuln-xss.txt",
    "vuln-ssrf.txt": PROMPTS_DIR / "vuln-ssrf.txt",
}


def test_xss_ssrf_prompts_define_ee_reachability_semantics():
    """xss/ssrf prompt 必须把 externally_exploitable 定义为 reachability tag,
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
        f"xss/ssrf prompt 缺 externally_exploitable 可达性语义定义 "
        f"(LLM 会把 ee 误判为 authentication_required 反面 → reachability 标签错判): {missing}"
    )
