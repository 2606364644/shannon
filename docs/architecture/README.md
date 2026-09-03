# 项目架构文档

本目录是 supernova 当前架构的正式文档入口，记录“系统现在如何实现、为什么这样设计、在哪里改”。它面向贡献者和后续 agent，不是任务计划或历史提案归档。

## 目录定位

- **本目录**：当前架构、数据流、组件契约、运行开关与维护入口。
- [`docs/superpowers/`](../superpowers/README.md)：历史/在途工作的 spec 与 plan。
- [`docs/gap/`](../gap/)：效果差距与事故分析。
- 根目录 `AGENTS.md` / `CLAUDE.md`：每次会话必须遵守的架构不变量摘要。

架构文档更新规则：

1. 改实现时同步更新对应主题文档，而不是另开一份重复说明。
2. 文档先描述当前行为；设计中的能力必须明确标注“设计/规划中”。
3. 引用历史动机时链接 spec/plan，不把提案复制成本文正文。
4. 入口、Sink、规则、双轨和引擎契约属于跨模块不变量，修改前先读对应文档和测试。

## 推荐阅读路径

### 白盒主链路

1. [项目总览](overview.md)
2. [入口识别](entry-point-identification.md)
3. [Sink 识别](sink-identification.md)
4. [双轨分析](dual-track-analysis.md)
5. [调用链提取](call-chain-extraction.md)
6. [调用链研判](call-chain-verdict.md)
7. [PoC 生成](poc-generation.md)

### 仓库与跨服务

- [MR 扫描](mr-scanning.md)
- [跨仓微服务扫描](cross-repo-microservice-scanning.md)

### 操作者配置与验证

- [认证档案](auth-profiles.md)
- [HOST 档案](host-profiles.md)
- [黑盒验证](blackbox-verification.md)

### 执行引擎

- [双浏览器引擎](browser-engines.md)
- [双 Agent 引擎](agent-engines.md)

## 文档索引

| 主题 | 文档 | 核心问题 |
|---|---|---|
| 系统总览 | [overview.md](overview.md) | 包边界、扫描数据流、交付物结构 |
| 入口识别 | [entry-point-identification.md](entry-point-identification.md) | 哪些函数/路由算入口，如何融合与裁决 |
| Sink 识别 | [sink-identification.md](sink-identification.md) | 规则 sink、候选 sink、LLM 软 sink 与降级 |
| 双轨分析 | [dual-track-analysis.md](dual-track-analysis.md) | GitNexus 轨与纯 LLM 轨的边界、开关、合并 |
| 调用链提取 | [call-chain-extraction.md](call-chain-extraction.md) | GitNexus trace、taint propagation、二阶存储链 |
| 调用链研判 | [call-chain-verdict.md](call-chain-verdict.md) | 多轮 verdict agent、checkpoint、预算与 authz 特判 |
| PoC 生成 | [poc-generation.md](poc-generation.md) | 结构化 PoC agent、校验、写回与报告导出 |
| MR 扫描 | [mr-scanning.md](mr-scanning.md) | diff、增量 scope、删防护与 MR 报告 |
| 跨仓微服务扫描 | [cross-repo-microservice-scanning.md](cross-repo-microservice-scanning.md) | 自动拓扑、per-edge 关联、跨仓裁决 |
| 认证档案 | [auth-profiles.md](auth-profiles.md) | 多角色凭据、加密存储、测试登录与扫描展开 |
| HOST 档案 | [host-profiles.md](host-profiles.md) | 域名/IP 映射、系统与工作区档案、per-scan 代理 |
| 黑盒验证 | [blackbox-verification.md](blackbox-verification.md) | 白盒交接、端点 live 验证、动态利用与报告 |
| 双浏览器引擎 | [browser-engines.md](browser-engines.md) | Playwright / agent-browser 的能力、切换与清理 |
| 双 Agent 引擎 | [agent-engines.md](agent-engines.md) | Claude Agent SDK / openai-agents 的统一契约与差异 |

## 快速定位源码

| 领域 | 主要路径 |
|---|---|
| 索引、规则、链、研判 | `packages/core/src/supernova_core/code_index/` |
| 采集器与 prompt 工具契约 | `packages/core/src/supernova_core/collectors/`, `prompts/` |
| 报告与 PoC | `packages/core/src/supernova_core/services/`, `packages/core/src/supernova_core/models/report_data.py` |
| MR 增量 | `packages/core/src/supernova_core/mr_scan/`, `packages/whitebox/src/supernova_whitebox/pipeline/mr_*` |
| 白盒 Temporal 流程 | `packages/whitebox/src/supernova_whitebox/pipeline/` |
| 黑盒验证 | `packages/blackbox/src/supernova_blackbox/` |
| 跨仓编排 | `packages/multi/src/supernova_multi/`, `packages/core/src/supernova_core/correlation/`, `packages/core/src/supernova_core/topology/` |
| Web 与档案 | `packages/web/src/supernova_web/` |
| 浏览器与 Agent 引擎 | `packages/core/src/supernova_core/services/`, `packages/core/src/supernova_core/agents/` |

## 验证入口

本文档集只做 Markdown 链接与占位符检查；实现行为以对应包的定向测试为准。常用检查：

```bash
rg -n "TBD|TODO|待补充" docs/architecture
find docs/architecture -maxdepth 2 -type f | sort
```

修改某个主题时，优先运行该文档“验证入口”列出的定向测试；不要广跑全套 pytest（仓库存在预置挂起/失败用例）。
