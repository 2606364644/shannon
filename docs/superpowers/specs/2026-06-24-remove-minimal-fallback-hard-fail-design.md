# 移除 minimal AST-only mode,改为 GitNexus 不可用即硬失败

**日期**:2026-06-24
**分支**:feat/fork-py
**状态**:设计已批准,待实现

## 背景与动机

whitebox 的 pre-recon 阶段通过 `run_code_index` 构建确定性代码索引(GitNexus 知识图谱 + tree-sitter + sink 检测 + taint 分析)。当 GitNexus 不可用时(CLI 没装 / 索引失败 / MCP 查询失败),当前实现会**降级**到 `minimal AST-only mode`(`_build_code_index_fallback`):只做单文件 tree-sitter 解析 + AST 模式 entry point 检测,**没有调用图、没有 sink 检测、没有 taint 分析**(`degradation_level=MINIMAL`)。

**问题**:minimal 产出的索引对白盒扫描几乎无用——白盒找漏洞依赖 source→sink 的跨函数调用链,而 minimal 把调用图/sink/taint 全砍了。基于这种贫瘠索引工作的下游 LLM agent 会被误导,产出低质量结果还假装成功。降级保底在这里是**反效果**。

## 现状(代码实证)

minimal 降级的多个入口:

- `code_index/__init__.py` `build_code_index_with_gitnexus` 的 `auto_index` 分支(line 88-104):GitNexus CLI 不可用 / 索引失败 → `return _build_code_index_fallback(...)`
- `activities.py` `run_code_index`(line 258-292):
  - `not indexed` → else 分支走 minimal(`auto_index=True`)
  - MCP `except` → `_StubMCPClient` + `auto_index=True` fallback
- `_build_code_index_fallback`(code_index/__init__.py:230-296):降级实现
- `_StubMCPClient`(activities.py:225):fallback 用的空 MCP 客户端

## 决策

**去掉 minimal 降级,GitNexus 不可用即硬失败。**

用户取舍明确:接受 GitNexus 的质量成本,拒绝 minimal 的降级垃圾索引——宁可 scan 失败,也不要误导性的降级产出。**不做**"LLM 独立兜底"的重构(那是另一个更大的设计方向,见 Out of Scope)。

两个确认的细节:

1. **MCP 查询失败(CLI 在 + 索引在,但查询挂)也硬失败**——与"不可用即失败"一致;删了 minimal 后没有东西兜底。
2. **`_build_code_index_fallback` / `_StubMCPClient` 直接删除**——不留死代码。

## 改动清单

### `packages/core/src/shannon_core/code_index/__init__.py`
- `build_code_index_with_gitnexus` 的 `auto_index` 分支(line 88-104):
  - `not engine.is_available()` → `raise PentestError(...)`(消息明确 "GitNexus CLI required, not installed")
  - `not index_result.success` → `raise PentestError(...)`(消息含 `index_result.error_message`)
- 删除 `_build_code_index_fallback`(line 230-296)
- `auto_index` 参数:删 fallback 后语义变为"构建前是否 ensure_indexed"。保留参数:`auto_index=True` 时 ensure_indexed(失败即 raise),`auto_index=False` 时假定已 indexed 直接进 MCP。

### `packages/whitebox/src/shannon_whitebox/pipeline/activities.py` `run_code_index`(line 258-292)
- `not engine.is_available()` → `raise PentestError("GitNexus CLI not available, cannot build code index")`
- `not indexed` → `raise PentestError("GitNexus indexing failed: ...")`(删 else minimal 分支)
- MCP `except`(line 275-284)→ `raise PentestError("GitNexus MCP query failed: ...")`(删 stub fallback)
- 删除 `_StubMCPClient`(line 225)

### 测试
- GitNexus 不可用 / 索引失败 / MCP 查询失败三类场景,断言从"返回 MINIMAL 索引"改为 `pytest.raises(PentestError)`,错误消息含 "GitNexus" 指引。
- `_StubMCPClient` / `_build_code_index_fallback` 的直接单测删除。
- 现有借 stub 跑 minimal 路径的测试改为用 `GitNexusEngine` mock(available + indexed)+ `GitNexusMCPClient` mock,或删除。

## 行为变化

| 场景 | 改动前 | 改动后 |
|---|---|---|
| GitNexus CLI 没装 | 降级 minimal | **硬失败**(PentestError) |
| GitNexus 索引失败 | 降级 minimal | **硬失败** |
| GitNexus MCP 查询失败 | 降级 minimal(stub) | **硬失败** |
| GitNexus 正常 | FULL 索引 | FULL 索引(不变) |

## 后果与配套

- ⚠️ **改完后,当前环境(gitnexus 不在 PATH)跑 scan 会立即失败**,报 "GitNexus required"。必须先装好 gitnexus 才能跑 scan。这是预期的硬失败语义。
- 失去全部降级保底:GitNexus 任何问题(没装/索引慢/损坏/MCP 挂)都让 scan 死。
- **强烈建议配套「治本①」(解除 event loop 阻塞)**:把 `parser.parse_file` / `subprocess.run` 用 `asyncio.to_thread` / `create_subprocess_exec` 移出 event loop。否则 GitNexus 一卡(>10min 超时)scan 就硬死,且会饿死并发 pre-recon agent。两者互补:治本① 让 GitNexus 稳定可用,本 spec 保证不偷偷降级。

## 不在范围内(Out of Scope)

- **LLM 独立兜底重构**(解除 fail-fast + `entry_point_fusion` 容错,让 GitNexus 挂时 LLM agent 独立产出 entry_points/sinks):另一个更大的设计方向,本次不做。
- GitNexus 索引性能优化、`.gitnexus` 完整性校验:另行处理。
- 治本①(解除阻塞):配套但独立,单独实现。

## 风险

- 硬失败让 scan 对 GitNexus 环境更脆:任何 GitNexus 问题(没装/版本不兼容/索引损坏)直接阻断 scan。缓解:错误消息清晰指引安装;配套治本① 提升 GitNexus 稳定性。
- 删 `_build_code_index_fallback` 需确认无其它引用:实现时 grep 全仓确认入口仅 code_index/__init__.py 与 activities.py。
