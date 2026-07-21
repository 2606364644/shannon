"""Task 5 (子项⑤): 端到端编排测试 — storage 4 路并行接入 build_code_index_with_gitnexus。

验证编排不变量:
1. ``ci.storage_write_points`` 非空(save 触发 java-orm-save 硬规则)。
2. ``ci.source_points`` 含 STORAGE 风味(findOneByUserId 触发 java-orm-find 硬规则 +
   LLM hunter 经 stub 返 []).
3. ``ci.parameter_graph.taint_flows`` 含 source_type=STORAGE 的 flow(intra-first
   将 storage read 作为 source 锚定到同函数 SQL sink; Task 2 已锁定该契约)。

Fixture 设计(Java, mirror test_intra_first_taint_flow.py 的 STORAGE 契约):
- ``save(User u)`` —— @PostMapping handler,调用 ``repo.save(u)`` 触发 java-orm-save。
- ``show(Long UserId)`` —— @GetMapping handler,``repo.findOneByUserId(UserId)``
  触发 java-orm-find(SourcePoint param_name="UserId"),``stmt.executeQuery(...)``
  触发 java-stmt-executequery(SinkCallSite)。intra_first 用确定性 fallback
  (llm_client=None): tainted_params={"UserId"}, hits[sink]=0.5;
  ``_source_points_matching`` 按 substring 匹配 ``"UserId" in "UserId"`` 命中
  → 产 TaintFlow(source_type=STORAGE, sink=executeQuery)。

Stubs(mirror test_build_code_index.py 的 _fake_mcp):
- ``mcp_client`` AsyncMock 返空 call graph(不依赖 GitNexus 索引)。
- ``llm_client=None`` —— 所有 LLM hunter 走 "LLM 不可用降级" 返 ([], []);
  ``analyze_taint_llm`` 走确定性 fallback(立场 B)。
- ``auto_index=False`` —— 不调 GitNexusEngine.ensure_indexed。
"""
import asyncio
import os
import tempfile

import pytest
from unittest.mock import AsyncMock

from supernova_core.code_index import build_code_index_with_gitnexus
from supernova_core.code_index.models import ParameterSource


# Java fixture —— 两个 handler(@PostMapping save + @GetMapping show)。
# 1. save: 调 repo.save(u) → storage write(java-orm-save)
# 2. show: 调 repo.findOneByUserId(UserId) → storage read(STORAGE SourcePoint),
#    bio 拼进 SQL → executeQuery sink(java-stmt-executequery)
_JAVA_SRC = """\
package com.example;

import org.springframework.web.bind.annotation.*;

class UserController {

    @PostMapping("/users")
    void create(User u) {
        repo.save(u);
    }

    @GetMapping("/users/{id}")
    void show(Long UserId) {
        String bio = repo.findOneByUserId(UserId);
        stmt.executeQuery("SELECT * FROM users WHERE bio='" + bio + "'");
    }
}
"""


def _make_java_repo() -> str:
    """tmp repo with UserController.java + .git dir (for detect_language)."""
    repo = tempfile.mkdtemp()
    with open(os.path.join(repo, "UserController.java"), "w") as fh:
        fh.write(_JAVA_SRC)
    os.makedirs(os.path.join(repo, ".git"), exist_ok=True)
    return repo


def _fake_mcp():
    """Stub GitNexus MCP client (empty call graph)."""
    mcp = AsyncMock()
    mcp.call_tool = AsyncMock(return_value={"upstream": [], "downstream": []})
    return mcp


@pytest.mark.asyncio
async def test_storage_4_way_orchestration_populates_points_and_flow():
    """端到端: 编排接入 storage 4 路并行 →
    storage_write_points / STORAGE source_points / STORAGE taint_flow 全部填充。

    断言(spec task-5-brief):
    1. ``ci.storage_write_points`` 非空(save 写点)。
    2. STORAGE-typed source_points 非空(read 读点,并入 source_points)。
    3. STORAGE-typed taint_flows 非空(intra-first: read → executeQuery sink)。
    """
    repo = _make_java_repo()
    index, _rule_gaps, _source_gaps, _storage_gaps = await build_code_index_with_gitnexus(
        repo,
        mcp_client=_fake_mcp(),
        llm_client=None,        # 关 LLM → 所有 hunter 降级返 ([], []) + taint 走 fallback
        auto_index=False,       # 不依赖 GitNexus CLI
        progress_cb=None,
    )

    # 断言 1: storage_write_points 非空
    assert index.storage_write_points, (
        f"storage_write_points 必须含 save 写点 (java-orm-save); "
        f"got {index.storage_write_points}")

    # 断言 2: source_points 含 STORAGE 风味
    storage_reads = [s for s in index.source_points
                     if s.source_type is ParameterSource.STORAGE]
    assert storage_reads, (
        f"source_points 必须含 STORAGE-typed 读点 (java-orm-find); "
        f"all source_types={[s.source_type for s in index.source_points]}")

    # 断言 3: parameter_graph.taint_flows 含 STORAGE 风味(intra-first 产)
    flows = (index.parameter_graph.taint_flows
             if index.parameter_graph is not None else [])
    storage_flows = [f for f in flows if f.source_type == ParameterSource.STORAGE]
    assert storage_flows, (
        f"必须存在 source_type==STORAGE 的 TaintFlow (intra-first: read→sink); "
        f"got {len(flows)} flows, source_types="
        f"{[f.source_type for f in flows]}")
