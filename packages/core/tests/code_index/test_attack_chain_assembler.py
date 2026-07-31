import logging
from supernova_core.code_index.attack_chain_assembler import assemble_attack_chains


def _finding(vt, source, sink, path, evidence="src→sink"):
    return {
        "vulnerability_type": vt,
        "source": source,
        "sink_call": sink,
        "path": path,
        "evidence_chain": evidence,
        "verdict": "vulnerable",
        "externally_exploitable": True,
    }


def _stored_xss_findings():
    return {
        "injection": [_finding("injection", "POST /api/profile.bio", "DB insert profiles",
                               "profile_ctl.js:42 → db.insert")],
        "xss": [_finding("xss", "DB profiles.bio", "GET /api/profile/:id render",
                         "profile_ctl.js:88 → render")],
        "ssrf": [],
        "authz": [],
    }


def test_assemble_stored_xss_chain_from_injection_plus_xss():
    """injection 写入 + xss 渲染 = stored XSS 链。"""
    chains = assemble_attack_chains(_stored_xss_findings(), logging.getLogger(__name__))
    assert len(chains) >= 1
    stored = [c for c in chains if c["vuln_type"] == "xss" or "stored" in c["name"].lower()]
    assert len(stored) >= 1
    assert len(stored[0]["steps"]) >= 2  # 多步


def test_assemble_returns_empty_when_gitnexus_unavailable():
    """GitNexus 不可用（无 findings）→ 空链（降级，LLM 轨兜底）。"""
    chains = assemble_attack_chains({}, logging.getLogger(__name__))
    assert chains == []


def test_assemble_returns_empty_when_no_cross_endpoint_link():
    """单端点 findings（无跨端点关联）→ 不组多步链。"""
    findings = {
        "injection": [_finding("injection", "GET /api/x?q", "SQL exec", "x.js:1→sql")],
        "xss": [],
        "ssrf": [],
        "authz": [],
    }
    chains = assemble_attack_chains(findings, logging.getLogger(__name__))
    assert chains == []


def test_assemble_zh_lang(monkeypatch):
    """zh 模式：攻击链模板文案为中文。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "zh")
    chains = assemble_attack_chains(_stored_xss_findings(), logging.getLogger(__name__))
    assert chains
    assert "存储型 XSS" in chains[0]["name"]
    assert "已存储数据" in chains[0]["steps"][1]["description"]  # storage step


def test_assemble_en_lang(monkeypatch):
    """en 模式：攻击链模板文案为英文。"""
    monkeypatch.setenv("SUPERNOVA_AGENT_NARRATION_LANG", "en")
    chains = assemble_attack_chains(_stored_xss_findings(), logging.getLogger(__name__))
    assert chains
    assert "Stored XSS" in chains[0]["name"]
    assert "stored data" in chains[0]["steps"][1]["description"]
