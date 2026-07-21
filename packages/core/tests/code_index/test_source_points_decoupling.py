"""CLAUDE.md §1 双轨铁律守卫:source_points(GitNexus 轨确定性产物)不得进
LLM 轨 prompt。

对齐 packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py 的
精神:LLM 轨(vuln-*.txt + recon.txt + pre-recon-code.txt 等 agent)必须自给自足,
不得消费确定性层产物,因为把确定性结果喂 LLM 轨会使其依赖一个经常不可用 / 超时的
层(GitNexus),破坏双轨独立性。

`source_points` 是 GitNexus 轨 `parameter_graph.json` 的确定性字段
(`packages/core/src/supernova_core/code_index/models.py`: ParameterGraph.source_points
= list[SourcePoint]),由 `source_detector.detect_sources` 规则 + LLM 补召回产出
(同 SinkCallSite 一类)。它绝不允许跨轨注入 LLM 轨 prompt。

路径核实:本测试位于 packages/core/tests/code_index/,parents[4] = repo root,
prompts 目录在 repo 根 / prompts(与 test_static_dataflow_hints_decoupling.py 复用
同一 PROMPTS_DIR 解析逻辑)。守卫真实扫到 prompt 文件(断言 scanned > 0),
防 vacuous pass。
"""
from pathlib import Path

# Anchor on this file's location so the path resolves regardless of pytest's cwd.
# packages/core/tests/code_index/test_source_points_decoupling.py
#   parents[0]=code_index  [1]=tests  [2]=core  [3]=packages  [4]=repo root
# 与 packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py 同样的
# parents[4] / "prompts" 解析(那条测试文件在 tests/prompts/,本测试在
# tests/code_index/,但 parents[4] 都正好是 repo root,因为层级深度相同)。
REPO_ROOT = Path(__file__).resolve().parents[4]
PROMPTS_DIR = REPO_ROOT / "prompts"

# FORBIDDEN_TOKENS:GitNexus 轨确定性 source 产物标识——不得出现在任何 LLM 轨
# prompt 文件(.txt / .md)正文。
FORBIDDEN_TOKENS = ("source_points", "SourcePoint", "source_point_ids")


def _all_prompt_files():
    """遍历 supernova 真实 prompts 目录下的所有 prompt 文件(.txt / .md)。

    若 PROMPTS_DIR 不存在(路径错),yield 为空——test_no_prompt_references_source_points
    的 scanned > 0 断言会失败,从而暴露路径错误,防 vacuous pass。"""
    if not PROMPTS_DIR.exists():
        return
    yield from PROMPTS_DIR.rglob("*.txt")
    yield from PROMPTS_DIR.rglob("*.md")


def test_no_prompt_references_source_points():
    """CLAUDE.md §1 双轨铁律:LLM 轨 prompt 不得引用确定性产物 source_points。

    任何 prompt 文件出现 source_points / SourcePoint / source_point_ids 即视为
    跨轨注入(GitNexus 确定性 → LLM 轨),违反铁律。"""
    offenders = []
    scanned = 0
    for p in _all_prompt_files():
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        scanned += 1
        for tok in FORBIDDEN_TOKENS:
            if tok in text:
                offenders.append(f"{p.relative_to(REPO_ROOT)}: mentions {tok}")
    # 守卫必须真实扫到文件(防 vacuous pass)
    assert scanned > 0, (
        f"守卫未扫到任何 prompt 文件(PROMPTS_DIR={PROMPTS_DIR} 路径错?),"
        "调整路径让守卫真实覆盖 prompt 文件"
    )
    assert not offenders, (
        "CLAUDE.md §1 双轨铁律违反:LLM 轨 prompt 引用了 GitNexus 轨确定性产物 "
        "source_points(跨轨注入,破坏 LLM 轨自给):\n" + "\n".join(sorted(offenders))
    )
