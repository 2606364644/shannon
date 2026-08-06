# docs/vs-ts — PY 重构版 vs TS 原始版对照文档集

> 本目录是 supernova-py（Python 重构版，分支 `feat/fork-py`）相对 `/root/shannon`（TypeScript 原始版，下称 **TS 版**）的安全扫描对照文档。按主题分工，按需取用：

| 文档 | 定位 | 读它为了 |
|------|------|----------|
| [`scan-effectiveness-gains-vs-ts.md`](./scan-effectiveness-gains-vs-ts.md) | **主文档** · 能力对照矩阵 | 看全貌：W1-W10 / B1-B5 / P1 编号的能力点矩阵 + 完整黑盒 exploitation 链条 + 诚实边界。交付 / 汇报 / 评审用 |
| [`py-redesign-architecture.md`](./py-redesign-architecture.md) | **架构图解** · 设计总览 | 看整体设计长什么样：双轨 / 多引擎 / 补召回 / PoC / 黑盒多角色认证档案 / 跨仓 / cost 的架构图 + 铁律汇总。以图为主 |
| [`refactor-scan-optimization-vs-ts.md`](./refactor-scan-optimization-vs-ts.md) | 叙事版 · 同主题不同写法 | 和主文档**同主题**（都是安全效果），写法不同：主文档=能力矩阵+验证档（交付口径），本文档=「踩到什么坑→怎么排查→怎么改」的体感叙事（读故事口径）；§6 稳定性是其中一个章节（主文档 §8.5 外链） |
| [`second-order-storage-taint-mechanism.md`](./second-order-storage-taint-mechanism.md) | 单机制深挖 | 看二阶存储中转污点的完整分析逻辑（谁写锚点 / 产物 / 谁用 / 判定模型 / 四介质覆盖） |
| [`intra-first-taint-mechanism.md`](./intra-first-taint-mechanism.md) | 单机制深挖 | 看 W5「handler 不入链致整类全空」的场景复盘：四步根因链 + source 补召回 / intra-first 方案 + 实测效果 |

## 阅读顺序建议

1. 先扫 [主文档 §0-§1](./scan-effectiveness-gains-vs-ts.md) 建立全局（TS 单轨纯 LLM → PY 双轨 verdict OR）。
2. 想看整体设计架构图（双轨 / 多引擎 / 补召回 / PoC / 黑盒多角色怎么连起来）→ [架构图解](./py-redesign-architecture.md)。
3. 想看某个优化「踩了什么坑、怎么排查」→ 对应章节进 [叙事配套](./refactor-scan-optimization-vs-ts.md)。
4. 关心二阶存储污点细节 → [机制深挖](./second-order-storage-taint-mechanism.md)。
5. 关心 W5 handler 不入链全空怎么修 → [机制深挖](./intra-first-taint-mechanism.md)。
6. 架构不变量（双轨铁律、确定性产物不喂 LLM 轨）→ [`../../CLAUDE.md`](../../CLAUDE.md) §1（架构图解 §10 也有汇总）。

## 文档间引用约定

- 主文档把「工程 / 稳定性」（chunk threshold、文件级聚合、Koa 治本、sandbox）统一外链到 refactor §6，不在正文展开，避免「扫描效果」与「让大仓跑完的工程」两类内容互相稀释。
- 二阶存储在主文档 §4.5 只做要点陈述，完整机制分析由 `second-order-storage-taint-mechanism.md` 承载，主文档与 refactor 均外链过去。
- 各份均为「测试绿 + 真机待跑」状态（架构图解为设计总览，验证状态外链主文档），**不把「单测绿」当「真机已验」**；真机硬证据（authz 0→21、hr 4→0 丢弃修复、11 个 curl PoC、exploit verdict 9 全 accepted）以主文档标注为准。
