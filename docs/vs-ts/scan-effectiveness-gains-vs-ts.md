# supernova-py 相比原始 TS 版的安全扫描效果优势对照

> 对比对象：`/root/shannon`（TypeScript 原始版，下称 **TS 版**，`main` 分支） vs `/root/shannon-py`（Python 重构版，下称 **PY 版**，分支 `feat/fork-py`）。
> 范围：**白盒 / 黑盒安全扫描的安全效果**——能在 TS 版基础上多发现 / 多防误报 / 多覆盖哪些漏洞，以及交付质量。**不含实现细节**（代码路径、算法机制、行号），那些属于叙事版 [`refactor-scan-optimization-vs-ts.md`](./refactor-scan-optimization-vs-ts.md) 与机制深挖 [`second-order-storage-taint-mechanism.md`](./second-order-storage-taint-mechanism.md)。
> 组织方式：按「解决什么安全问题 → TS↔PY 能力对比 → 对安全效果的意义 → 效果验证」展开。
>
> **效果验证口径（重要）**：本文只认**安全效果**，不报测试数。
> - ✅ **真机效果已验证**：有真实扫描数据证明安全效果提升（召回 0→N、PoC 产出、verdict 修正等）。
> - ⏳ **效果待真机验证**：能力已实现就位，但尚无真机扫描数据证实效果提升。
>
> 单测 / 集成测**只证明功能能跑，与安全效果无关**，故全文不列测试数。架构不变量见 [`../../CLAUDE.md`](../../CLAUDE.md) §1；目录索引见 [`README.md`](./README.md)。

---

## 0. 对比基准：TS 版是「纯 LLM 单轨」

TS 版 `apps/worker/src/` 全仓 grep `gitnexus | parameter_graph | sink_rules | source_rules | taint | dataflow | code_index` **确定性引擎代码零命中**。检测链路只有一条腿：每个漏洞类各跑一个 LLM agent，自己读 recon、自己 grep、自己派 Task 子代理追 source→sink、自己下 verdict。**无代码索引、无 sink/source 规则、无 taint 图、无 AST、无任何确定性兜底**；单仓视角（`PipelineInput.repoPath` 是单一绝对路径）。

这带来三个先天弱点，PY 版的优势主线就围绕它们展开：
1. **单点召回**——agent 漏了就彻底漏，无第二来源对账；
2. **单仓盲区**——跨服务数据流 / 跨仓信任边界看不见；
3. **sink/source 无规则库**——清单写在 prompt 文本里，靠 LLM 记忆，不可演进、无程序化兜底。

> 说明：TS `apps/worker/dist/` 编译产物里有 `blackboxPipelineWorkflow` 与 `checkAuthzCoverage`，但 `.ts` 源码在任何分支（含 `feat/pro`）均不存在——历史 / Pro 闭源残留。本文以 **TS `main` 源码**为基准，Pro 产物特性在 §8.2 单列说明。

---

## 1. 总览：能力对照矩阵

| # | 解决的安全问题 | TS 版能力 | PY 版能力 | 对安全效果的意义 | 效果验证 |
|---|---|---|---|---|---|
| W1 | 单轨漏报无对账 | 纯 LLM 单轨，无兜底 | 确定性轨 + LLM 轨，verdict OR | 召回对账 + 任一轨挂不归零 | 架构级，经 W6/W7 兜底实例印证 |
| W2 | sink 靠 LLM 记忆 grep | prompt 文本清单 | 五语言规则库程序化匹配 | 不依赖 LLM 的确定性召回基线 | ⏳ 待真机 |
| W3 | 规则盲区类漏检 | 无 | LLM 补召回规则未覆盖的 sink | NoSQL / log4j / 框架特有 sink | ⏳ 待真机 |
| W4 | stored XSS / 二阶 SQLi | 当攻击技术给 exploit 试 | 确定性 join 连 write→存储→read→sink | 二阶漏洞系统召回 | ⏳ 待真机 |
| W5 | handler 不入链致整类全空 | 无机制 | intra-first taint + source 补召回 | 结构性全空修复 | ⏳ 待真机（NodeGoat 0→N） |
| W6 | authz IDOR 无确定性兜底 | 纯 LLM 探索 | 确定性候选 + 多轮深度 agent + 框架端点 | 系统性 IDOR 召回 | ✅ **真机 0→21** + 4→0 丢弃修复 |
| W7 | 调用链空壳建不起链 | 无确定性调用链 | GitNexus 原生 process trace | 链不再空壳 | ✅ **真机 0→21** |
| W8 | SSRF 等局部变量对象链全丢 | 无机制 | 超时跳过 fallback + 参数深提取 | SSRF / 部分 SQL·命令注入跨函数链 | ⏳ 待真机重扫 |
| W9 | 跨服务数据流盲区 | 单仓，不支持多仓 | multi-repo 编排 + 跨仓关联 agent | 跨服务候选链 | ⏳ 待真机 |
| B1 | 前缀路由致端点假阴性 | 直接拿源码路径打 live | 端点存活 + 路由前缀探测 | 防假阴性 + 省 exploit 预算 | ⏳ 待真机 |
| B2 | auth 100% 信 LLM 判定 | 仅信 `login_success` | auth-state cookie/origin 交叉验证 | 防误杀 / 防幻觉成功 | ⏳ 待真机量化 |
| B3 | exploit verdict 全 Unverified | 直接写 evidence 无校验 | verdict 4 层校验 + 归一化 | 防已利用漏洞报为未验证 | ✅ **真机 9 全 accepted** |
| B4 | 漏洞静默消失 | 无 | coverage gap 可视化 | queue 有 evidence 无的条目显式标出 | ⏳ 已落地 |
| B5 | 黑/白盒关系不可追溯 | 无编码 | scan_id 编码白盒血缘 | 可审计 | ⏳ 已落地 |
| P1 | PoC 散在 markdown 人工抠 | 自由格式 evidence | curl + Burp raw 结构化产物 | 「拿来就能打」的交付 PoC | ✅ **真机 11 个 curl PoC** |

---

## 2. 白盒 · 召回对账：双轨 verdict OR（W1）

**解决的问题**：TS 版召回 100% 押在单个 vuln agent 上。agent 受上下文窗口、注意力、单轮 token 上限、超时影响，漏报是常态且**漏了不知道漏了**（无第二来源对账）；一旦某一类整段翻车（超时 / spending cap / 走神），该类直接归零。

**能力对比**：
- **TS**：单腿，纯 LLM，无兜底。
- **PY**：双腿——确定性轨（代码索引 + 规则 + taint 图产候选链）与 LLM 轨（保持 TS 原样的纯 LLM 分析）**各自独立**，只在合并器做 verdict OR：任一轨判 vulnerable 即最终 vulnerable，两轨都报升高置信度。两轨链来源不同、互不喂数据，所以 OR 成立——这是项目最核心的架构不变量（CLAUDE.md §1）。

**对安全效果的意义**：召回率从单轨的 `p` 提升到 `1-(1-p_det)(1-p_llm)`；一条轨挂了另一条兜底，不再因单点失败整类归零。

**效果验证**：架构级能力，其兜底价值难以用单次真机直接证明（效果是概率性的），但 **W6/W7 的 authz 0→21 正是确定性轨独立兜回、LLM 轨整段超时归零场景下的硬实例**。代价：token 翻倍（初期明确不省 token 换少漏）；可关 LLM 轨走纯确定性兜底。

> 实现机制（合并器 OR 语义、保守降级、降级报告、开关语义）见 [叙事版 §1](./refactor-scan-optimization-vs-ts.md)。

---

## 3. 白盒 · 召回基线：规则化 sink/source + 盲区补召回（W2 / W3）

**解决的问题**：TS 版 sink/source 是 prompt 文本清单（如「DB calls, raw SQL | exec, system | include, require | pickle.loads」），LLM 自己 grep 找。无程序化匹配、无参数位标注、无反哺——**漏 grep 一个 sink 就漏报，无兜底**；且清单之外的盲区类（NoSQL 注入、log4j JNDI、Path traversal）无规则可演进。

**能力对比**：
- **TS**：自然语言清单，靠 LLM 记忆与 grep。
- **PY**：五语言（Python / TS·JS / Go / Java / PHP）+ 主流框架的规则库**程序化匹配**，命中即产 sink/source，**不依赖 LLM 记忆或创意**；规则未覆盖的可疑调用再由轻量 LLM 判定补召回，产可区分的「软 sink」，并反哺规则库迭代。

**对安全效果的意义**：提供一条不依赖 LLM 的确定性召回基线；覆盖 TS prompt 清单不覆盖的盲区类；规则库可随实战演进，越扫越准。

**效果验证**：⏳ 能力就位，效果待真机验证（真机重扫验 sink 从「LLM 漏 grep」变「规则必然命中」）。

> 踩坑叙事（Java receiver 漏检、fastjson/Jackson 缺规则）见 [叙事版 §2.1](./refactor-scan-optimization-vs-ts.md)。

---

## 4. 白盒 · 召回盲区结构性修复

这一节是 PY 相比 TS 最硬的增量：**把 TS 纯 LLM「靠运气追到」的盲区，变成「程序化兜底必然召回」**。

### 4.1 authz IDOR 确定性轨 + 调用链下沉（W6 / W7） ✅

**解决的问题**：authz 属 missing-control（非 source→sink taint），确定性 sink 规则不覆盖；TS 版纯靠 LLM agent 从 recon 预候选一个个追代码，**漏了就漏了，无兜底**。更糟的是调用链建不起来——authz 轨曾实测候选 0 条，IDOR 路径完全无法构建。

**能力对比**：
- **TS**：纯 LLM 探索，无确定性候选、无 ownership 谓词检测、无框架端点分析。
- **PY**：确定性 IDOR 候选（用户可控源 + 可达 side-effect sink + 无 ownership 守卫）+ 多轮深度 agent 判定 owner/guard + 框架自动生成端点识别；调用链来源下沉到 GitNexus 原生 process trace，链不再空壳。

**对安全效果的意义**：把 horizontal/vertical IDOR 从「LLM 偶尔追到」变「系统性召回」。

**效果验证**：✅ **真机硬证据——authz 候选 0→21**（statement_template_svr，调用链下沉后实测）；另有探索分支静默丢数据 bug 修复（agent 产出 4 个候选、落地 0 条 → 修复后不再丢）。

> 排查过程见 [叙事版 §2.3 / §3](./refactor-scan-optimization-vs-ts.md)。

### 4.2 source 补召回 + intra-first taint（W5）

**解决的问题**：路由注册模式（`app.get('/path', handler)`）下 handler 函数不被识别为入口 → source 漏扫 → handler 不进调用链 → **整类 GitNexus 轨全空**（NodeGoat injection/xss/ssrf 三类 0 flow）。

**能力对比**：
- **TS**：无机制，LLM 漏追即漏报。
- **PY**：对含 sink 的函数直接产不经调用链的单步 taint（intra-first），source 识别扩到含 sink 函数，结构性修复「整类全空」。

**对安全效果的意义**：框架路由注册写法的入口漏识别，此前导致整类召回归零；修复后变必然召回。

**效果验证**：⏳ 能力就位，NodeGoat 三类 0→N 待真机验证。

> 完整场景复盘（四步根因链 + 方案取舍 + 实测效果）见 [`intra-first-taint-mechanism.md`](./intra-first-taint-mechanism.md)。

### 4.3 SSRF taint 断链修复（W8）

**解决的问题**：「sink 参数是局部变量对象」（如 `httpClient.execute(request)`，真正污点在背后构造的 `ip`/`port`）的跨函数 taint 链**全丢**；超时的 sink 函数被整段跳过而非断链保留。受影响的不止 SSRF，还有参数经 `String.format` 构造后传入的部分 SQL/命令注入。

**能力对比**：
- **TS**：无机制。
- **PY**：超时/异常跳过的 sink 函数产兜底 taint 填回（不丢弃）+ sink 参数深提取回溯。

**对安全效果的意义**：一类「参数间接构造」的跨函数污点链此前全丢，修复后召回。

**效果验证**：⏳ 能力就位，sentinel_dashboard SSRF=0 待真机重扫验证。

### 4.4 二阶存储中转污点（W4）

**解决的问题**：stored XSS / 二阶 SQLi 在双轨都系统性漏——write 端做了净化被判 safe 丢弃，read 端信任存储数据直接进 sink 却不被识别为污点源。

**能力对比**：
- **TS**：把「second-order injection」当**攻击技术**列给 exploit agent 尝试，非分析阶段的跨函数追链机制，发现靠 LLM 偶尔追到。
- **PY**：确定性层引入「存储中转」概念，程序化连接 write→存储→read→sink，按存储介质与表名 join。

**对安全效果的意义**：二阶漏洞从「靠 LLM 创意」变「程序化系统召回」。

**效果验证**：⏳ 能力就位，关轨重扫验二阶 finding 非空待真机验证。

> 完整机制分析（谁写锚点 / 产物 / 判定模型 / 四介质覆盖）见 [`second-order-storage-taint-mechanism.md`](./second-order-storage-taint-mechanism.md)。

---

## 5. 白盒 · 覆盖面扩展：跨仓微服务关联（W9）

**解决的问题**：TS 版单仓扫描，A 服务写消息队列、B 服务消费后拼 SQL 这类**跨服务数据流与跨仓信任边界完全盲区**；`vuln-authz.txt` 自承局限 "Untraced Microservice Calls... could not be analyzed without their source code"。

**能力对比**：
- **TS**：不支持多仓输入。
- **PY**：声明式 multi-repo 编排 + 跨仓关联 agent，推断服务拓扑 / 信任边界 / 跨服务候选数据流；黑盒侧可加载拓扑做 gateway 层验证。

**对安全效果的意义**：这是覆盖面的硬扩展——TS 连多仓输入都不支持，谈不上跨服务召回。

**效果验证**：⏳ 能力就位，真机 multi-repo 冒烟待跑。

---

## 6. 黑盒 · exploitation 阶段增强（B1–B5）

> 口径前提：两边黑盒都是 **exploitation-only**（不独立发现漏洞，复用白盒 queue，对齐 TS 基线）。本节是 PY 在 exploitation 阶段的安全效果增量，不是「黑盒整体更强」。

### 6.1 端点 live 验证 + 路由前缀发现（B1）

**解决的问题**：真实部署常经网关/ingress 前缀路由（源码 `/api/users` → 部署 `/v2/app/api/users`）。TS 版直接拿源码路径打 live target，404 → 误判漏洞不可利用（**假阴性**）。

**能力对比**：TS 无端点探测；PY 在 exploitation 前用 LLM 验证端点存活，源码路径 404 时智能尝试常见前缀，确认后同组端点套用。

**对安全效果的意义**：防前缀路由假阴性 + 省 exploit 预算 + 降级零回归。**效果验证**：⏳ 待真机。

### 6.2 auth 客观交叉验证（B2）

**解决的问题**：TS 版 100% 依赖 LLM 的 `login_success` 判定。LLM 误判 `false`（浏览器实际已登录）= 整个扫描因「认证失败」中止；LLM 幻觉报成功但未实际登录 = 带坏 auth 跑完全程。

**能力对比**：TS 仅信 LLM 结构化输出；PY 用 auth-state（cookie/origin）交叉验证——LLM 报成功再验 auth-state 防幻觉，LLM 报失败则复查 auth-state 覆盖误判防误杀。

**对安全效果的意义**：防认证误判导致的中止或带病跑全程。**效果验证**：⏳ 已落地（覆盖 GLM 结构化输出误填的实际场景），效果待真机量化。

### 6.3 exploit verdict 校验（B3） ✅

**解决的问题**：TS 版 exploit 产出的 verdict 直接写 evidence，只校验文件存在与 JSON 可解析，**不校验内容**。LLM 不严格遵守 schema（severity 大小写、富结构 steps）→ 严格校验全拒 → **报告全 Unverified**（实际已利用的报为未验证，假阴性）。

**能力对比**：TS 无内容校验；PY 先归一化（severity/扁平化/序列化）挽救可挽救的 verdict，再校验，且 ID 必须在 queue 中防幻觉。

**对安全效果的意义**：防已利用漏洞被报为未验证。**效果验证**：✅ **真机 9 个 verdict 全 accepted**（修复前报告全 Unverified）。

### 6.4 覆盖盲区可见性（B4）

**解决的问题**：queue 里有但 evidence 里没有的条目会**静默消失**，分析师不知道哪些没验证。

**能力对比**：TS 无；PY 计算每类 exploit 的覆盖度，未覆盖条目幂等追加进 evidence 显式标出待人工复核。

**对安全效果的意义**：防漏洞静默消失（漏检可见性 / 可审计）。**效果验证**：⏳ 已落地。

### 6.5 结果溯源（B5）

**解决的问题**：黑盒扫描与白盒来源的关系不可追溯。

**能力对比**：TS 无编码；PY 黑盒 scan_id 编码白盒血缘（`<wb_scan_id>~<N>`），从黑盒即可追溯白盒。

**对安全效果的意义**：可审计（非直接召回提升）。**效果验证**：⏳ 已落地。

---

## 7. 产物质量：PoC 产物化（P1） ✅

**解决的问题**：TS 版 PoC 散在 exploit 阶段自由格式 markdown 里，无结构化 Burp 产物；`externally_exploitable` 可达性标签下游没专门消费。交付甲方的「拿来就能打」的 PoC 要人工从 markdown 抠。

**能力对比**：TS 自由格式 evidence；PY 针对 `externally_exploitable==True` 的漏洞纯后处理产 curl + Burp raw PoC（黑白盒各一份），真实 token 不持久化，置信度三档标注。

**对安全效果的意义**：产物质量从「markdown 抠 curl」到「一键产出可复用 curl + Burp raw 包」。

**效果验证**：✅ **真机硬证据——对历史 session 实测产出 11 个 curl PoC**（XSS×2 / AUTHZ×4 / SSRF×1 + 变体）。

---

## 8. 诚实边界：PY 不优于、或待验证、或代价

### 8.1 多数确定性召回增强 = 能力就位，效果待真机验证

sink 规则补齐、二阶存储、intra-first taint、SSRF 断链修复、跨仓关联——**能力均已实现，但安全效果多数未经真机扫描证实**。本文不把「能力就位」当「效果已验」。真机已验的硬证据仅四处：**authz 0→21**（W6/W7）、**authz 候选 4→0 丢弃修复**（W6）、**exploit verdict 9 全 accepted**（B3）、**11 个 curl PoC 产出**（P1）。

### 8.2 黑盒安全效果两边基本相当；PY authz 对账兜底反而比 TS Pro 少一道

两边黑盒都是 exploitation-only，主体 exploit prompts / 登录方式 / scope / ROE / 证明标准基本对齐。**客观反向差距**：TS Pro 产物（`dist/`，源码已不在任何分支）的 `checkAuthzCoverage` 是纯数据减法（端点全集 − queue − safe_vectors = 漏判端点，advisory 写进报告标「authz 未测请人工复核」）；PY 黑盒只有 queue 内部的覆盖度可视化（universe = queue，非真实攻击面全集），**没有这道 agent 跑完后的机械确定性兜底**。该兜底是 advisory（提漏检可见性 / 合规可审计性，不提召回），优先级中低。

### 8.3 两边共同缺失

**authz 双账号 baseline**：TS `main` 与 PY 均无。两边都单账号，horizontal IDOR 缺 victim baseline、vertical 缺 admin baseline，无法产硬 EXPLOITED 证据，只能 POTENTIAL / 枚举 ID 看 200。

### 8.4 双轨代价

token 翻倍（初期明确不省 token 换少漏）；可关 LLM 轨走纯确定性兜底（仅关 inj/xss/ssrf 的 taint agent，authz/auth 的 LLM 全保留——这两类 GitNexus 做不了）。

### 8.5 不算安全效果优化的项

双引擎（claude-agent-sdk / openai-agents）对齐、cost 计费、稳定性工程（chunk threshold / 文件级聚合 / sandbox 修复）是「让确定性轨在大仓上能跑完」的前提——这些是工程 / 可靠性，不直接提召回准确率，本文不展开（详见 [叙事版 §6](./refactor-scan-optimization-vs-ts.md)）。

---

## 9. 一句话结论

PY 版相比 TS 版的核心增量是**给单轨纯 LLM 加了一条确定性兜底腿并把它真正跑通**（authz 0→21、authz 候选丢弃修复、exploit verdict 9 全 accepted、11 个 curl PoC 是已验证的硬证据），外加**跨仓视角**（TS 单仓 → PY 多仓拓扑 + 信任边界）和**结构化 PoC 产物**（markdown 抠 curl → 一键 curl + Burp raw）两个 TS 版完全没有的能力；黑盒侧 PY 在 exploitation 阶段补了端点验证 / auth 客观校验 / verdict 校验等防误报增强，但整体安全效果两边基本相当，且 PY 黑盒 authz 对账兜底反而比 TS Pro 少一道（advisory，非召回下降）。确定性轨的多数召回增强目前是「能力就位、效果待真机验证」状态——真机全量验证是下一步。
