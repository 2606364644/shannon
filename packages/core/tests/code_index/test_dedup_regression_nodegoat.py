# packages/core/tests/code_index/test_dedup_regression_nodegoat.py
"""双轨去重断裂修复回归（spec 2026-09-03 §4.1 夹具回归的等效落地）。

spec 原始 fixture（NodeGoat-20260903-071648，XSS GN 33 条→10 组）在其产出
环境，本机不可得。本文件用本机真实扫描 NodeGoat-20260826-184212（XSS GN
15 条 / LLM 11 条 / injection GN 9 条 / LLM 4 条）+ spec §1.2 精确记录的
分叉形态快照合成，锁定同一组行为断言：

- F1 尾标点：'POST /login,' 脏形与 'POST /login' 干净形同组（spec §1.2
  GN-28 组与 GN-37 组修后合一）
- F2/F3 vtype 类级归一：injection 跨轨（LLM CommandInjection × GN
  injection）、xss 细型（Stored × Reflected）key 可撞
- F3 LLM 轨同 key 折叠不吞卡
- ground truth 口径：合并对全部正确（不同文件/不同路由绝不合并）

注意：20260826 fixture 是 F6a 前的 builder 产物（GN 卡 path 无 "METHOD /path"
前缀、endpoint=None），跨轨交集测试按 F6a 生效形态（path 带路由前缀）补
前缀后验证——这正是本次修复后新扫描的产物形态。
"""
import json
from pathlib import Path

from supernova_core.code_index.dual_track_merger import (
    _finding_key,
    merge_dual_track_queues,
)
from supernova_core.code_index.gn_collapse import (
    _unit_key,
    collapse_gn_entries,
    extract_endpoint,
)
from supernova_core.models.queue_schemas import (
    InjectionVulnerability,
    XssVulnerability,
)

FIX = Path(__file__).parent / "fixtures" / "nodegoat_20260826"


def _load(queue, schema):
    data = json.loads((FIX / queue).read_text())["vulnerabilities"]
    return [schema.model_validate(d) for d in data]


# --- 真实数据：GN 轨 collapse 分组正确性（不错合并）---

def test_xss_collapse_real_scan_file_fallback_groups():
    """真实 15 条 XSS GN（contributions ×12 / memos / research ×2，path 均无
    路由前缀）→ 文件级回退 3 组；同文件不同参数/行号折一组、跨文件绝不合并。"""
    gn = _load("xss_gitnexus_queue.json", XssVulnerability)
    groups = collapse_gn_entries(gn)
    assert len(groups) == 3
    files = {k[1] for k in (_unit_key(g) for g in groups)}
    assert files == {"app/routes/contributions.js",
                     "app/routes/memos.js",
                     "app/routes/research.js"}
    contributions = next(g for g in groups
                         if _unit_key(g)[1] == "app/routes/contributions.js")
    assert len(contributions.affected_entries) == 12  # 3 参数 × 4 行号一主记录


def test_xss_collapse_vtype_class_level_keeps_grouping():
    """F2 vtype 类级归一只作用于 key 计算：真实卡全部 Reflected，类级化
    （Reflected→xss）后分组数不变、卡上 vulnerability_type 展示字段不动。"""
    gn = _load("xss_gitnexus_queue.json", XssVulnerability)
    groups = collapse_gn_entries(gn)
    assert len(groups) == 3
    assert all(g.vulnerability_type == "Reflected" for g in groups)
    assert all(_unit_key(g)[0] == "xss" for g in groups)


# --- spec §1.2 形态快照：POST /login 同洞三形折一组（F1 ground truth）---

def _login_card(id_, path, sink_ln):
    return XssVulnerability(
        ID=id_, vulnerability_type="Reflected", externally_exploitable=True,
        confidence="low", source="userName (app/routes/session.js:SessionHandler:8)",
        path=path,
        sink_call=f"app/routes/session.js:SessionHandler:render:{sink_ln}:{sink_ln}",
        sink_function="render", verdict="vulnerable", source_track="gitnexus")


def test_xss_login_dirty_endpoint_forms_collapse_to_one():
    """spec §1.2 ground truth：'POST /login'（GN-28 组）与 'POST /login,'
    （GN-37 组，尾逗号脏形）修后同组——尾标点不再劈分同洞单元。"""
    gn = [
        _login_card("GN-28", "POST /login → swig render userName", 21),
        _login_card("GN-37", "POST /login, → swig render userName", 34),
        _login_card("GN-45", "POST /login → swig render userName", 40),
    ]
    groups = collapse_gn_entries(gn)
    assert len(groups) == 1
    assert extract_endpoint(groups[0].path) == "POST /login"
    assert len(groups[0].affected_entries) == 3


def test_xss_login_never_merges_across_files():
    """ground truth 反向：不同文件的同 sink 函数绝不合并（防「重复数下降
    但错合并」）。"""
    a = _login_card("GN-A", "POST /login → swig render userName", 21)
    b = XssVulnerability(
        ID="GN-B", vulnerability_type="Reflected", externally_exploitable=True,
        confidence="low", source="symbol (app/routes/research.js:ResearchHandler:8)",
        path="symbol -> app/routes/research.js:ResearchHandler:render (x)",
        sink_call="app/routes/research.js:ResearchHandler:render:31:15",
        sink_function="render", verdict="vulnerable", source_track="gitnexus")
    groups = collapse_gn_entries([a, b])
    assert len(groups) == 2


# --- 跨轨 key 交集（F2/F3：修前 0 → 修后 ≥1）---

def test_injection_cross_track_keys_intersect():
    """真实 LLM 卡（INJ-VULN-01 CommandInjection 'POST /contributions' eval）
    × 真实 GN sink_call（F6a 生效形态 path 前缀）→ vtype 归一后 key 相交
    （修前 CommandInjection≠injection 第一维即断，交集 0）。

    INJ-VULN-01 的 sink_call 保持 20260826 真实富文本多行号枚举形不规整
    （'...:32 (preTax)、:33...'）——parse 拒非 GN 形态后自然语言回退归一
    出 'eval'，与 GN 侧 sink 维可撞（曾规整 workaround 已删，本测试即锁定）。"""
    llm = _load("injection_llm_queue.json", InjectionVulnerability)
    gn_raw = _load("injection_gitnexus_queue.json", InjectionVulnerability)
    for f in gn_raw:
        f.path = f"POST /contributions → {f.path}"
    gn = collapse_gn_entries(gn_raw)
    lk = {_finding_key(f) for f in llm}
    gk = {_finding_key(f) for f in gn}
    assert lk & gk, "vtype 类级归一后 POST /contributions eval 应跨轨可撞"


def test_xss_cross_track_stored_reflected_keys_intersect():
    """spec §1.1 XSS-VULN-06(Stored llm) + GN-07(Reflected gn) 同洞形：
    同 endpoint+sink 下两轨细型不同 → 类级化后 key 相等。"""
    llm = XssVulnerability(
        ID="XSS-VULN-06", vulnerability_type="Stored", externally_exploitable=True,
        confidence="low", source="benefitStartDate (app/routes/benefits.js:BenefitsHandler:30)",
        path="POST /benefits → updateBenefits", sink_call="render",
        verdict="vulnerable")
    gn = XssVulnerability(
        ID="GN-07", vulnerability_type="Reflected", externally_exploitable=True,
        confidence="low", source="benefitStartDate (app/routes/benefits.js:BenefitsHandler:30)",
        path="POST /benefits → benefitStartDate -> render",
        sink_call="app/routes/benefits.js:BenefitsHandler:render:51:23",
        verdict="vulnerable", source_track="gitnexus")
    assert _finding_key(llm) == _finding_key(gn)


# --- F3 配套：LLM 轨同 key 折叠（真实 schema、不吞卡）---

def test_llm_track_same_key_folds_keeps_both_ids():
    llm = [
        XssVulnerability(
            ID=f"L{i}", vulnerability_type=t, externally_exploitable=True,
            confidence="low",
            source=f"p{i} (app/routes/session.js:SessionHandler:8)",
            path="POST /signup (anon) → handleSignup", sink_call="render",
            verdict="vulnerable")
        for i, t in enumerate(("Stored", "Reflected"))
    ]
    merged = merge_dual_track_queues(llm, [], mode="verdict")
    assert len(merged) == 1
    assert merged[0].merged_from == ["L1"]
