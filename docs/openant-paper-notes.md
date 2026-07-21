# OpenAnt 论文笔记：代码分解 / 对抗性验证 / 动态测试的 LLM 漏洞发现

> **来源：** 微信公众号「知识分享者」论文速读《OpenAnt：通过代码分解、对抗性验证与动态测试的LLM漏洞发现》
> （原文链接 https://mp.weixin.qq.com/s/apu4-rIhU8ZqynmvXJIPvQ，2026-06 抓取）
>
> **论文信息（文章引用，未独立核验）：** *OpenAnt: LLM-Powered Vulnerability Discovery Through Code Decomposition, Adversarial Verification, and Dynamic Testing*，Nahum Korda / Gadi Evron，Knostic。arXiv: 2606.19149；代码 Apache 2.0：github.com/knostic/OpenAnt。
>
> **本文档性质：** 基于上述**二手速读文章**整理。写本笔记的目的是**提炼 OpenAnt 的流水线思路，对照 supernova GitNexus 轨现状，沉淀为后续优化项候选**（见 §6）。论文细节若要落地，请回到 arXiv 原文 / 官方仓库核验。

---

## 0. TL;DR

OpenAnt 把"在百万行级真实仓库里用 LLM 挖洞"做成一条**六阶段闭环流水线**：前两阶段纯静态分析（不耗 token）、中间三阶段 LLM 语义推理与攻击者模拟、末阶段沙箱动态验证。其工程内核可以概括为三点——

1. **代码分解 + 可达性过滤**：把仓库切成函数级"分析单元"（三层依赖内联），从外部入口 BFS，只保留可达函数 → 把分析面平均砍掉 **97%**，让 LLM 阶段在经济上可行。
2. **对抗性验证**：不让模型只回答"有没有漏洞"，而要它**扮演能力受限的远程攻击者**，走完认证 / 输入校验 / 平台边界等真实障碍，且须**多路径探索 + 存在受害者**才能下"无法利用"的结论 → 在评估里把 49.5% 的候选砍掉。
3. **动态验证**：对仍可信的候选，**从零自动生成 Dockerfile + PoC 脚本**，在受限沙箱里真实复现，容器以 JSON 输出 `CONFIRMED/NOT_REPRODUCED/...` 定论 → 把"理论漏洞"升级为"可执行证据"。

**评估结论：** 8 个真实开源项目（OpenSSL / WordPress / Rails / Flowise / n8n 等，6 门语言），64132 个函数 → 190 个确认漏洞 → **144 个被自动复现**（75.8%），总成本 **1461.25 美元**（平均每仓 < 200 美元）。覆盖 30+ 漏洞类别，以 IDOR/缺失授权、Mass Assignment、SSRF、路径穿越、XSS、注入为主。

---

## 1. 背景：它要解决什么

OpenAnt 直击把 LLM 丢进真实大仓的三大痛点，也顺带回应了传统 SAST 的老问题：

- **SAST 告警疲劳**：Semgrep / CodeQL / Fortify / Checkmarx 误报率可达 40%+，仅约 20% 开发者主动用 SAST；规则驱动"写严则误报、写松则漏报"，对新语言/框架滞后。
- **LLM 的"中间信息丢失"**：超长上下文中，序列中段信息识别能力显著下降 → "把整个仓库塞进上下文"不可行。
- **LLM 的成本爆炸**：按 token 计费，百万行级仓库成本天文数字。
- **LLM 结论无法闭环验证**：若推理结果不能验证，等于把"误报"从规则世界搬到概率世界，没解决根本问题。

核心立场：**不要让模型一次性吃下整个仓库，而要把它拆成小而自洽的分析单元；不要只做静态推理，而要让模型扮演攻击者，把"理论可疑"转成"实际可达"；不要相信模型自报结论，要用沙箱里跑出的真实 PoC 定论。** 这把 LLM、静态分析、动态验证三种各自为政的技术拼成一条闭环。

---

## 2. 六阶段流水线（核心方法）

顺序的本质是**"先廉价过滤 → 再昂贵推理 → 最后真实验证"**的成本/精度渐进权衡。

| 阶段 | 名称 | 引擎 | 作用 | 关键产出/数据 |
|---|---|---|---|---|
| 1 | 代码解析 | 静态 | 语言特定 AST 解析器抽取所有函数，记录签名/函数体/位置/调用关系，构造**双向调用图**；Python/Go 用原生 AST 库，JS/TS 用 ts-morph，其余 tree-sitter；归一化为统一中间表示 | 调用图 |
| 2 | 分析单元生成 + **可达性过滤** | 静态 | 每个单元 = 目标函数 + ≤3 层深度依赖内联 + 入口元数据；从入口（HTTP/CLI/WebSocket/文件读）BFS，只保留可达函数 | **OpenSSL 15232→390 单元（~97%）、Grafana 18500→994（~94.6%）** |
| 3 | 暴露分类 | **LLM (Sonnet)** | 工具辅助下迭代探索调用方/被调实现/调用路径，归入四类：Exploitable / Vulnerable-Internal / Security control / Neutral，仅 Exploitable 继续 | OpenSSL 390→49（再降 ~87%）；**最贵，占成本 ~72.9%** |
| 4 | 漏洞检测 | **LLM (Opus)** | 语言无关提示词，回答三问：代码在做什么？输入从哪来？由此有什么安全风险？输出 `vulnerable/bypassable/inconclusive/protected/safe` | 376 候选 |
| 5 | 对抗性验证 | **LLM (Opus)** | 扮演受限远程攻击者（仅浏览器、无服务器/管理员凭证、不改服务器文件），为每个候选构造利用路径；须**多路径探索**才能判"无法利用"，须有**第三方受害者** | 确认 **190** |
| 6 | 动态验证 | **LLM (Sonnet) + 沙箱** | 自动生成 Dockerfile/测试脚本/依赖/必要时 docker-compose；受限沙箱（只读 FS、512MB、单 CPU、禁提权、120s 超时）执行；容器输出 `CONFIRMED/NOT_REPRODUCED/BLOCKED/INCONCLUSIVE/ERROR`；ERROR 回灌 LLM 修测试，最多重试 3 轮；**产物用完即销（瞬时执行）** | 复现 **144（75.8%）** |

> 设计上最巧妙的一点是**"语言无关"**：阶段 4 同一份提示词在 Python/JS/Go/C/C++/Ruby/PHP 间通用——安全推理在语义层面跨语言普遍，本质都是"追踪外部输入到敏感操作的路径"。

---

## 3. 关键设计要点

### 3.1 代码分解：函数级分析单元 + 三层依赖内联
把仓库切成**小而自洽**的单元：目标函数本体（Primary code）+ 最多三层深度依赖**跨文件内联**（Resolved dependencies）+ 入口元数据（是否来自 HTTP/CLI/WebSocket/文件读等外部输入）。每个单元都能被独立分析，绕开"塞整个仓库进上下文"的死结。

### 3.2 可达性过滤：从外部入口 BFS，砍 97% 分析面
从入口点对调用图做 BFS，只保留能被外部输入触达的函数，内部工具函数/测试辅助/管理脚本统统滤掉。**这一刀不消耗任何 token，却为后续 LLM 阶段省下海量成本**——这是"先廉价过滤"原则的极致体现。论文算账：若不做可达性过滤，把所有函数都跑阶段 3（中位成本计）需约 **23700 美元**，静态过滤把成本压缩超 96%。

### 3.3 对抗性验证：攻击者视角 + 多路径 + 受害者要求
不是让模型回答"有没有漏洞"，而是要求它**推理一条具体利用路径**，逐步走完认证、输入校验、平台边界等真实障碍。两条硬约束：
- **多路径探索**：必须尝试多条攻击思路才能下"无法利用"结论（单路径推理常错过可利用条件）。
- **受害者要求**：必须有第三方受影响，不能是"攻击者影响自己的数据"这种无意义场景。

评估中 49.5% 的候选在这一步被排除，主因：输入清洗阻断了攻击者控制的数据流、认证屏障无法绕过、漏洞只影响攻击者自己、平台保护机制（同源策略/云存储访问控制）。

### 3.4 动态验证：自动 PoC + 沙箱 + 瞬时执行
对仍可信的候选，**从零生成** Dockerfile + 攻击脚本 + 依赖清单（SSRF 等多服务场景还生成 docker-compose），在受限沙箱真实复现。关键设计——
- **结构化结论**：容器必须输出 JSON 归入 5 类，ERROR 回灌修正，最多 3 轮。
- **瞬时执行（ephemeral）**：所有 PoC/镜像/中间文件执行后立即销毁，**从不维护漏洞模板库**，每份证据都为当前漏洞从零生成（"第一性原理"，非套模板）。
- **不同类型复现率差异大**：命令注入 100%、路径穿越 88.9%、认证绕过 83.3%、Mass Assignment 76.9%（"输入直接驱动结果"的天然适合自动 PoC）；竞态、跨服务时序依赖类难复现 → 系统结论是**下界**（未动态复现 ≠ 不存在）。

### 3.5 基准污染反思：评估方法论
论文花大篇幅讨论被很多 LLM 安全研究忽视的**基准数据集污染**：Juliet / OWASP Benchmark / CVE 复现样本已在网上流传十年+，大概率进了训练语料；LLM 在"泄漏样本"通过率可比"未泄漏"高 4.9 倍；常用数据集标签错误率 20%–71%、重复率 17%–99%；Juliet 文件名甚至直接暴露漏洞类型（如 `CWE89_SQL_Injection__*`），模型可"快捷学习"猜答案。故 OpenAnt 选择**直接在活跃维护的真实开源项目上挖洞**——代码不是为基准而生、结论需独立验证，以此证明真实语义推理能力而非记忆复现。

---

## 4. 实验数据要点

- **漏斗**：8 仓库 64132 函数 → 可达性过滤后 2281 单元（96.4% 缩减）→ 暴露分类 586 外部可达单元（<原始 1%）→ 阶段 4 标记 376 候选 → 对抗验证确认 190 → 动态复现 **144（75.8%）**。
- **漏洞类型（30+ 类）**：IDOR/缺失授权 29、Mass Assignment/提权 26、SSRF 25、路径穿越/Zip Slip 18、XSS/内容注入 17、SQL/NoSQL 注入 11。工作流自动化平台（Flowise/n8n）漏洞密度最高（"允许用户执行任意工作流并对接外部服务"本身攻击面庞大）；成熟框架 Rails、密码学库 OpenSSL 确认漏洞较少。
- **成本**：总 1461.25 美元，暴露分类阶段占 72.9%。
- **配置**：阶段 3/6 用 Claude Sonnet 4，阶段 4/5 用 Claude Opus 4，temperature=0，无系统缓存。

---

## 5. 与 supernova GitNexus 轨的对照

supernova 是**双轨**（GitNexus 轨 = 确定性层 + 轻量 LLM 判定；LLM 轨 = 纯 LLM 独立），两条轨各自独立、只在合并器 verdict OR 交汇。OpenAnt 是**单轨闭环**。下表把 OpenAnt 六阶段**逐段映射到 GitNexus 轨**（刻意不映射到 LLM 轨，原因见 §7）。

| OpenAnt 阶段 | supernova GitNexus 轨现状 | gap |
|---|---|---|
| 1. 代码解析（AST + 双向调用图 + 统一 IR，6 语言） | `run_code_index`/`build_code_index()` 确定性 AST：提取函数块/调用边/候选入口，产 `parameter_graph.json`/`SinkCallSite` | **部分对齐**；多语言已支持（per-language sink 规则覆盖 py/ts/go/java/php），但"双向调用图 + 跨语言统一 IR"成熟度待核 |
| 2. 分析单元 + **可达性过滤（BFS 从入口，砍 97%）** | `rebuild_call_chains`（从确认入口点建 CallChain）+ Spec B tiered sink | **缺系统性的"可达性砍面"**：当前是"检测所有 sink 再建链"，而非"先从入口 BFS 砍掉不可达函数再分析"。**与 `run_code_index` 对大仓 >10min 超时（见 memory `pre-recon-gitnexus-blockage`）直接相关** |
| 3. 暴露分类（LLM 4 分类，砍 87%） | **无对应** | OpenAnt 独有的中间过滤层，GitNexus 轨 sink→builder→verdict 之间无此闸门 |
| 4. 漏洞检测（Opus + 语言无关提示词，3 问题） | `chain_verdict.py`（`run_claude_prompt` 单次结构化判定） | **概念对齐**；OpenAnt 用更强模型 + 三问式语言无关 prompt，supernova 是轻量单次判定（成本/精度定位更低） |
| 5. 对抗性验证（攻击者模拟 + 多路径 + 受害者要求） | **无对应** | chain_verdict 是单次判定，非攻击者模拟。**这是 OpenAnt 降假阳性的核心利器（49.5% 排除率）** |
| 6. 动态验证（自动 Dockerfile + PoC + 沙箱复现） | exploit 阶段是**黑盒 live exploit（针对 `web_url`）**，非源码层 PoC | **范式不同**：OpenAnt 在源码层自动生成 PoC + 容器复现；supernova 是部署后系统测试。属较大新增能力，超出 GitNexus 轨范畴 |

---

## 6. 可借鉴优化项（针对 GitNexus 轨）★

> 以下为候选，按"投入/收益/与现状契合度"排序。**均落在 GitNexus 轨（确定性层 + chain_verdict），不喂 LLM 轨**（双轨独立性铁律，见 §7）。是否落地、排优先级由后续 spec 决定。

### 优化项 O-1：可达性过滤前置（解决 `run_code_index` 大仓超时）— 高收益 / 中投入
- **痛点**：`run_code_index` 对大仓 >10min 超时拖死 agent（memory `pre-recon-gitnexus-blockage`，当前只有 `stop()` 5s 自拔止血）。
- **借鉴**：OpenAnt 阶段 2 从外部入口 BFS，只保留可达函数，砍 97% 分析面，**且不耗 token**。
- **落点**：在 `build_code_index` 之后、`sink_detector`/builder 之前，加一道"从确认入口点 BFS 调用图 → 标记可达函数集 → 仅对可达集做 sink 检测与建链"。
- **预期**：直接降低 `run_code_index`/确定性后处理对大仓的耗时与产物体积；与现有 `rebuild_call_chains`（已从入口点建链）天然衔接。
- **风险**：入口点裁决（pre-recon Phase 0）的完整性决定召回——入口漏了，可达集就漏。需保证入口候选不被过早过滤。

### 优化项 O-2：在 sink→verdict 之间加"暴露分类"过滤层（降 chain_verdict 调用量）— 中收益 / 低投入
- **借鉴**：OpenAnt 阶段 3 在"漏洞检测"前用一次 LLM 把单元分 Exploitable/Internal/Control/Neutral，砍 87%，**也把最贵的探索放这里**（占成本 72.9%）。
- **落点**：GitNexus 轨 `vuln_chain_builders` 候选链出来后、`chain_verdict` 之前，加一层"暴露/可达性分类"，把无外部暴露面的候选（纯内部 sink、不可达入口）先排除，减少昂贵的 verdict 调用。
- **注意**：这层本质是把 `chain_verdict` 的职责拆成"先分类、再精判"。supernova 的 `externally_exploitable`（可达性标签）已是 finding 级标签（公网/内部或跨服务），与 OpenAnt 的"单元级暴露分类"不同物——**不可把后者塞进前者覆盖语义**（合并器 `externally_exploitable` 解耦见 injection-recall spec 改动 3′）。

### 优化项 O-3：把 `chain_verdict` 从单次判定升级为对抗性验证（降假阳性）— 高收益 / 高投入
- **借鉴**：OpenAnt 阶段 5 扮演受限远程攻击者，**多路径探索 + 受害者要求**，评估里 49.5% 候选被排除。
- **落点**：`chain_verdict.py` 的判定 prompt 升级——不只回答 vulnerable/safe，而要给出"假设远程攻击者（无服务器凭证）能否触达此 sink"的利用路径推理；强制多路径；要求存在第三方受害者。
- **权衡**：当前 chain_verdict 定位是**轻量** LLM 判定（单次结构化输出，`run_claude_prompt`，非 agent）。升级为对抗模拟会增加单次成本/延迟。**需在"GitNexus 轨轻量判定"定位与"降假阳性"之间取平衡**——可考虑分级：低置信/高严重度候选才走对抗模拟。
- **与双轨关系**：这只增强 GitNexus 轨自己的判定质量，不引入对 LLM 轨的依赖，符合双轨消费模型。

### 优化项 O-4：源码层自动 PoC + 沙箱复现（范式扩展，超出 GitNexus 轨范畴）— 高收益 / 高投入 / 慎重
- **借鉴**：OpenAnt 阶段 6 从零生成 Dockerfile + PoC，沙箱复现，输出 `CONFIRMED/NOT_REPRODUCED`。
- **落点**：与 supernova 现有 exploit 阶段（黑盒 live exploit on `web_url`）是**不同范式**——OpenAnt 是源码层、可在上线前完成、不依赖部署环境。
- **判断**：这不是 GitNexus 轨的小修，而是白盒→explopt 闭环的能力扩展。**建议单独立项评估**（是否值得引入源码层 PoC 生成、沙箱执行 infra、瞬时清理机制），不宜混入 GitNexus 轨优化。

---

## 7. 边界与约束（双轨消费模型）⚠️

把 OpenAnt 单轨闭环思路映射进 supernova 时，**务必守住双轨铁律**（CLAUDE.md §1、memory `dual-track-consumption-model`）：

1. **可达性过滤 / 暴露分类 / 对抗验证，都只作用于 GitNexus 轨**（确定性层 + chain_verdict），**不得把任何产物经 hints/prompt 喂进 LLM 轨**。`static_dataflow_hints.md` → `_static-dataflow-hints.txt` → `@include` 的旧耦合已拆除（injection-recall spec 改动 4b），勿以"借鉴 OpenAnt"为由重建。
2. **OpenAnt 的"可达性过滤" ≠ supernova 的 `externally_exploitable`**：前者是**分析面缩减**（砍掉不可达函数、降 LLM 阶段成本），后者是**finding 级可达性标签**（公网 true / 内部或跨服务 false，且不能被 verdict 覆写）。两者解决不同问题，不可互相替代或混用语义。
3. **OpenAnt 不擅长"功能缺失/协议解析不一致/跨子系统逻辑错误"**——它瞄准"外部输入流向危险操作"的结构性漏洞（注入/路径穿越/SSRF/权限绕过/XSS）。supernova 的 auth/authz 属 missing-control，OpenAnt 的流水线不直接覆盖（supernova 走 authz 候选 + LLM 判定、auth config 扫描器，与 OpenAnt 思路不同源）。
4. **OpenAnt 结论是"下界"**（未动态复现 ≠ 不存在）；supernova 若引入动态验证，同样只能给确认证据、不能因"未复现"否定 finding。

---

## 8. 参考链接

- 速读文章（本笔记来源）：https://mp.weixin.qq.com/s/apu4-rIhU8ZqynmvXJIPvQ
- 论文（文章引用，未核验）：https://arxiv.org/pdf/2606.19149
- 代码（文章引用，未核验）：https://github.com/knostic/OpenAnt （Apache 2.0）

---

**下一步（建议，非承诺）：** 若要落地某个优化项，建议从 **O-1（可达性过滤前置，直击 `run_code_index` 超时）** 起，按 `docs/superpowers/specs/` 流程开设计 spec——它与现有 `rebuild_call_chains`、`sink_detector`、Spec B tiered sink 衔接最自然，收益最直接。O-3/O-4 需独立评估成本与定位。
