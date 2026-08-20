"""spec 2026-08-21 §4.2 端到端回归: NodeGoat 四漏洞形态 + intra 合法空判定。

复现真因链(非 llm_client=None 的 fallback 路径——那条已绿):
mock LLM 对 taint prompt 返回合法空判定(NodeGoat 2026-08-20 事件:参数提取只含
构造参数 db,intra 问错问题 → tainted_params=[])→ 断言表达式回退(修复点 A)+参数
并入(B)+规则(C/D/E)仍让四类候选出现:eval 注入 / needle SSRF / res.render XSS /
res.redirect(Open_Redirect 面由修复点 E 在 builder 层产出,此处验证候选不缺)。
"""
import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

from supernova_core.code_index import build_code_index_with_gitnexus
from supernova_core.code_index.chain_verdict import extract_candidate_chains

NODEGOAT_SHAPE = """\
function Handler(db) {
  this.displayResearch = (req, res) => {
    const url = req.query.url + req.query.symbol;
    return needle.get(url, (e, r, b) => { res.send(b); });
  };
  this.update = (req, res) => {
    const preTax = eval(req.body.preTax);
    return res.render("contributions", { preTax: preTax });
  };
  this.learn = (req, res) => {
    return res.redirect(req.query.url);
  };
}
"""


async def _llm_empty_taint(prompt, **kw):
    """模拟 NodeGoat 真因:对 intra taint 提问返回合法空判定(问错问题 → 空);
    对其它 discovery 提问返回空 JSON 数组(触发各 discovery 的确定性降级)。"""
    if "taint propagation" in prompt:
        return '{"tainted_params": [], "propagation_paths": []}'
    return "[]"


@pytest.mark.asyncio
async def test_nodegoat_four_vuln_candidates_survive_empty_intra():
    with tempfile.TemporaryDirectory() as repo:
        with open(os.path.join(repo, "app.js"), "w") as fh:
            fh.write(NODEGOAT_SHAPE)
        os.makedirs(os.path.join(repo, ".git"), exist_ok=True)
        fake_mcp = AsyncMock()
        fake_mcp.call_tool = AsyncMock(return_value={"upstream": [], "downstream": []})
        with patch("supernova_core.code_index.detect_entry_points", return_value=[]):
            index, _rg, _sg, _sg2 = await build_code_index_with_gitnexus(
                repo, mcp_client=fake_mcp, llm_client=_llm_empty_taint)

        scs = {s.id: s for s in index.sink_call_sites}
        rules = {s.rule_id for s in index.sink_call_sites}
        # 规则层:四类 sink 都被抓到
        assert "ts-eval" in rules, f"eval sink 缺失, got {rules}"
        assert "ts-needle-get" in rules, f"needle SSRF sink 缺失, got {rules}"
        assert "ts-res-render" in rules, f"res.render XSS sink 缺失, got {rules}"
        assert "ts-res-redirect" in rules, f"res.redirect sink 缺失, got {rules}"

        # 参数并入:Handler block.parameters 含嵌套 arrow 的 req/res
        handler = next(b for b in index.blocks if b.function_name == "Handler")
        assert "req" in handler.parameters, \
            f"嵌套 arrow 形参未并入(NodeGoat 断点 1), got {handler.parameters}"

        # flow 层:表达式回退在 intra 全空下仍产 taint flows
        flows = index.parameter_graph.taint_flows
        assert flows, "intra 合法空判定下 flows 不应为空(表达式回退失效?)"

        # 路由层:三类候选都能 extract(ssrf 含 needle 与 redirect 两形态)
        pgraph = index.parameter_graph
        inj = extract_candidate_chains(pgraph, vuln_class="injection", sink_call_sites=scs)
        ssrf = extract_candidate_chains(pgraph, vuln_class="ssrf", sink_call_sites=scs)
        xss = extract_candidate_chains(pgraph, vuln_class="xss", sink_call_sites=scs)
        assert inj, f"eval 注入候选缺失(修复点 C), got {len(inj)}"
        assert any(c.sink_slot == "cmd_argument" for c in inj), \
            f"eval 候选 slot 应为 cmd_argument(修复点 C), got {[c.sink_slot for c in inj]}"
        assert ssrf, f"SSRF 候选缺失(修复点 A: url expr 回退), got {len(ssrf)}"
        assert xss, f"XSS 候选缺失(修复点 D: res.render category=xss), got {len(xss)}"
