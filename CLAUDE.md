# CLAUDE.md — supernova 项目指南

> 本文件记录本 repo 的**架构不变量与关键约定**，给所有在此工作的 agent / 贡献者。每次会话自动加载。改动前请先读对应小节。

---

## 1. 双轨概念（最重要架构不变量）★

supernova 的注入 / xss / ssrf 白盒检测是**双轨**，两条轨**各自独立、只在合并器（verdict OR）交汇**：

- **GitNexus 轨**：确定性层产物（`parameter_graph.json` / `SinkCallSite`）→ `vuln_chain_builders/*_builder.py` 提候选链 → `chain_verdict.py` 跑**多轮 verdict agent 深判**（`run_gitnexus_verdict_agent`：agent 自主 grep/read 验证链快照，`max_turns=30`（`SUPERNOVA_GITNEXUS_VERDICT_MAX_TURNS`），护栏 `SUPERNOVA_CHAIN_VERDICT_MAX_AGENTS=200`；仅 inj/xss/ssrf；2026-08-27 `7b9b64a2` 从轻量单次判定升级，llm_client 单次路径仅留测试/降级）→ `<vuln>_gitnexus_queue.json`。**这条轨的产物由 LLM 分析。** authz 走独立的 `run_authz_gitnexus_judge`（同为多轮 agent，30min 活动窗口），见本节末「auth/authz 特殊」段。**容量铁律（2026-08-27 NodeGoat 首扫事故）**：多轮深判单链 10-60s，`run_gitnexus_chain_verdict` 的 `start_to_close_timeout` 必须按 `链数 ÷ 并发 × 单链耗时` 重估（27 条串行需 15-20min，曾超 15min 旧窗口致 3 次重试全爆、白盒 failed）；改判定形态/并发度时同步调窗口，且重试无检查点会从头重跑全部链。
- **LLM 轨**：`vuln-*.txt` agent，**纯 LLM 分析**——读 recon + 自己 grep + 自己追链，**保持与原始项目 `/root/shannon` 一致**（TS 无确定性层，100% 自给自足）。→ `<vuln>_exploitation_queue.json`（LLM 产物）。

**铁律（易踩，反复强调）：**
- **不要把确定性层产物喂进 LLM 轨 prompt。** LLM 轨靠自身方法论 + 双轨 OR 由 GitNexus 轨独立补召回；把确定性结果喂 LLM 轨会让它依赖确定性层（而确定性层 / GitNexus 经常超时 / 不可用），破坏独立性。**确定性→LLM 轨的 hints 桥梁（原 `static_dataflow_hints.md` 产物 + `prompts/shared/_static-dataflow-hints.txt` partial + `@include`）已彻底拆除——勿重建**（2026-06-26 injection-recall follow-up）。任何新 prompt 不得 `@include` 确定性产物；`packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py` 锁定此不变量。
- **改 LLM 轨 prompt** 时，源只从 recon + grep 派生（TS 式），不引确定性 hints。
- **扩 sink 覆盖** 分两条路：LLM 轨改 prompt 清单（`vuln-*.txt`），GitNexus 轨改规则 YAML（确定性 sink 改 `packages/core/src/supernova_core/code_index/data/sink_rules.yml`、补召回候选改同目录 `sink_candidates.yml`、source 改 `source_rules.yml`；detector `.py` 只管加载/匹配逻辑，不再内联规则）。
- **合并**：`run_merge_dual_track_queues`（`dual_track_merger.py`）做 verdict OR；`externally_exploitable` 是**可达性标签**（true=公网 / false=内部或跨服务），**不能被 verdict 覆写**。

**双轨可配置（演进方向，2026-06-26 提出，设计中 ★）：**
- **战略**：把双轨从"固定并行"演进为**可配置开关**——**GitNexus 轨做可靠兜底，LLM 轨做可选的纯 LLM 增强（创意轨）**。**token 紧张时关闭 LLM 轨**（靠 GitNexus 轨兜底），**token 宽裕时打开 LLM 轨**（双轨 OR）。默认 LLM 轨**开**（env `SUPERNOVA_LLM_TRACK_ENABLED`，默认 `"1"`）。**★`SUPERNOVA_LLM_TRACK_ENABLED=0` 语义收窄（2026-07-14，plan smooth-wandering-dolphin）：只关 inj/xss/ssrf 的 vuln agent（taint，GitNexus chain_verdict 主干兜底）；pre-recon / recon / authz / auth 的 LLM 全保留**——authz Vertical/Context + auth 是 GitNexus 做不了的（GitNexus authz 轨只做 IDOR，auth 无确定性轨），且 authz 的输入（recon 角色模型 §7 / 多步工作流 §8.3）GitNexus 也不产，关了会 authz/auth 失明降安全效果。**authz 的 GitNexus 兜底经深度 agent（吃确定性候选）实现，非轻量判定**（2026-07-02 epic：spec-0 基础设施 + spec-1a authz 深度判定 + spec-1b 候选来源扩展），关轨时与 authz vuln agent 双轨 OR。**auth 不走 GitNexus 轨**（纯 LLM 轨 `vuln-auth` agent，对齐原始 shannon；曾有的 spec-2a spike / spec-2b auth 深度 agent 已于 2026-07-14 删除——`auth_config_scanner` 踩下条铁律「确定性产物不喂 LLM 轨 prompt」+ CORS 越界被裁的 misconfig，详见 plan zazzy-roaming-shamir；authz GitNexus 轨保留）。
- **为支撑"GitNexus 轨独立兜底"**，GitNexus 轨从纯确定性演进为"**确定性兜底 + 可选 LLM 补召回**"：`sink_detector` 规则（`data/sink_rules.yml`）未命中的可疑 call（由 `data/sink_candidates.yml` 候选模式表按语言+receiver 精确筛选，已替换旧版 flat 子串正则）用轻量 LLM 找 sink，产 `rule_id="llm-discovered"` 的软 `SinkCallSite`（与规则 sink 同流、可区分）+ `rule_gap_report.json` 反哺规则库优化。LLM 不可用（stub / 超时）时退回纯规则 + `is_entry_hint`（`deterministic-fallback` 立场 B 成果，作为"LLM 不可用档"，不浪费）。
- **不变的铁律**：LLM 轨仍纯 LLM 自给自足、不吃确定性产物（上条铁律不动）；本次演进只动 GitNexus 轨自己接 LLM + 加 LLM 轨开关，**不破坏双轨独立性**。
- **设计 spec**：`docs/superpowers/specs/2026-06-26-gitnexus-llm-sink-discovery-design.md`（撰写中，brainstorming 进行时）。

**auth / authz 特殊：** 它们不是 source→sink taint（属 missing-control），确定性 sink 规则不覆盖。authz 有自己的"GitNexus 风格"轨（`run_authz_gitnexus_judge`：IDOR 候选 + **深度 agent 判定**——`run_gitnexus_verdict_agent` 多轮，候选>0 吃候选深判 owner 检查、候选=0 自主探索 IDOR；候选来源扩展 OpenAPI/框架 2026-07-02 spec-1b 落地）。**auth 走纯 LLM 轨**（`vuln-auth` agent 9 类方法论，对齐原始 shannon；曾有的 auth GitNexus 轨——config 扫描器 `auth_config_scanner` + 深度 agent `run_auth_gitnexus_judge`——已于 2026-07-14 删除：`auth_config_scanner` 踩本节铁律「确定性产物不喂 LLM 轨 prompt」+ CORS 越界被裁的 misconfig，详见 plan zazzy-roaming-shamir；authz GitNexus 轨保留）。所以"扫不出"时先分清是哪条轨、哪个 vuln 类。

详见 `docs/architecture/overview.md`、`docs/superpowers/specs/2026-06-25-injection-recall-port-design.md`。

---

## 2. 双引擎（claude-agent-sdk / openai-agents）

项目拥有**双引擎**，经 `SUPERNOVA_AI_PROVIDER` 切换：
- **claude-agent-sdk**（profile `glm-anthropic`）：底层 Claude Code CLI。
- **openai-agents**（profile `glm-openai`）：openai 兼容 Chat Completions。

**关键约定：**
- **两个引擎在代码流程上是一样的**——supernova 经统一抽象调用（`packages/core/src/supernova_core/agents/` 的 `BaseProvider` + `run_claude_prompt`），业务侧（whitebox / blackbox / core）不感知用哪个引擎。
- **差异只在核心智能体能力**：claude-agent-sdk（CLI）原生全套工具，含**子代理委派**（CLI v2.1.x 该 tool 名 `Agent`，原 `Task`）；openai-agents 经 `tools_openai/build_tools()` 暴露工具集（含 `task` 子代理委派，Task 5 已对齐 CLI）。
- **核心差异根因：CLI 运行时 vs 纯框架（易踩，别反复绕）**——claude-agent-sdk 底层是 **Claude Code CLI 子进程**，CLI **自带全套内置工具**（`Task`/`Agent` 子代理委派 + Read/Bash/Grep/Edit…），故 claude 轨像原始 TS（`shannon/apps/worker/src/ai/claude-executor.ts`：`permissionMode: bypassPermissions`、无 `allowedTools`、`maxTurns: 10_000`）**白嫖 CLI 内置、零工具代码**（`_build_options` 只配 permission/env，不碰工具）；openai-agents 是**纯框架**（`Agent`/`Runner`/`handoffs`/`as_tool`/`function_tool` 编排原语），**不附通用内置工具**（少数 hosted tools 要 OpenAI 后端，第三方 Chat Completions 端点用不了），故 openai 轨**必须自维护** `tools_openai/{bash,fs,web,task}.py`。**后果**：原始 shannon 的 subagent 分发 = CLI 内置、**零代码**（prompts 里 "delegate to Task Agent" 直接驱动 CLI 内置 Task 工具）；claude 轨 100% 对齐；**openai 轨注定要自造委派工具**（手写 `task` 或 SDK 原生 `as_tool` 都行，**都逃不掉维护子 agent 定义**），对齐是"功能性的"（同一份 prompt 能跑）+ 带自维护成本——SDK 哲学差异，**不可消除，别当退化去"修"**。`as_tool` 与手写 `task` 功能等价、无致命缺陷（as_tool 透传父业务 context / 自带 approval·tracing 隐式行为面 / input 默认单参 `{input}`）；选手写 `task` 为可控性更窄 + 已验证 PASS（`validate_openai_task_probe.py`），非能力差异。
- **引擎的智能体能力是 agent 方维护的，当前项目只是使用**——`packages/core/src/supernova_core/agents/` 是项目的 agent 集成层（对接两套 SDK、暴露统一工具/能力），上游 SDK（claude-agent-sdk / openai-agents）提供底层能力；业务侧只 `run_claude_prompt(...)`，不直接碰 SDK。
- **能力对齐已落实**（Task 5，`feat/fork-py`）：openai 引擎经 `tools_openai/task.py` 的 `task` function_tool + provider 注入 `_make_subagent_runner`，对齐 CLI 的 `Agent` 子代理委派——两引擎跑同一份 vuln prompt（**prompt 不改**），双引擎流程对齐。
- **两个引擎都要支持、流程一致（可互换）**——不要"切到 glm-anthropic 了事"丢 openai 引擎，也不要让 openai 退化成单 agent 使两引擎行为分叉。
- **实测**：GLM 在 claude-agent-sdk 能正确驱动 `Agent` 子代理委派（`scripts/validate_glm_task_probe.py`，2/2 可复现）；glm-anthropic 不瘫、与原始 TS 一致。openai 引擎已补 `task` 工具（Task 5），**glm-openai 侧 `scripts/validate_openai_task_probe.py` 真机已验证 PASS 且可复现**（GLM 正确发起 `task` 子代理委派，子代理读码追链，产出 SQLi 判定；2026-06-27 首跑 68.7s、2026-06-28 复跑 57.8s，均 3 turns/success=True/不违规直接读）。改 agent/tool 行为后，用 `scripts/validate_*_task_probe.py` 类探针在对应引擎实测。
- **web 进程零 agent 执行点（2026-09-03 起，守护测试锁定）**——web 侧 LLM 能力一律经 temporal 提交 worker（`AuthValidationWorkflow` / `TopologyAnalysisWorkflow` 模式），web 包不得 import `PromptManager` / `supernova_core.agents.runner`（`packages/web/tests/test_web_never_runs_agents.py` 锁定）。踩坑史：拓扑预分析曾在 web 进程内跑 agent，web 镜像漏拷 prompts 致必失败 + web 镜像不带 node/claude（claude-agent-sdk 引擎即失败）——worker 容器天然资源齐（prompts/node/claude/chromium）。**给 web 加新 LLM 功能时走 temporal 提交，勿在 web 进程起 agent**；web Dockerfile 也别再 COPY prompts。

---

## 3. 关键参考

- **设计 / 计划**：`docs/superpowers/`（spec/plan 工作目录，**先看 `README.md` 主题索引**）；活跃层 `specs/`、`plans/`（日期 >2026-06-15）+ 归档 `specs/archive/`、`plans/archive/`（≤2026-06-15，历史已完成）。例：`2026-06-25-injection-recall-port-{design,plan}.md`。
- **架构总览**：`docs/architecture/overview.md`、`docs/whitebox-refactoring-assessment.md`、`docs/gap/`（gap 分析）。
- **测试陷阱**：全套 pytest 有预存挂起 / 失败（见各 package 的 test 说明）——只跑改动相关测试文件，勿广跑全套。
- **分支**：`feat/fork-py`（本地多项改动未 push；动代码前先看 `git log` 与 memory 了解在途工作）。
- **预存问题（真根因 2026-07-08 已修）**：pre-recon 的 `run_code_index`(GitNexus) 曾卡死（step 0 数十分钟、$0 LLM 成本）。**真根因不是"大仓索引慢 >10min 超时"（那是叠加症状），而是 `GitNexusEngine.ensure_indexed()` 漏调 `gitnexus index` 注册进全局 registry（`~/.gitnexus/registry.json`）**：`.gitnexus/` 已存在就被 skip analyze、永不补注册 → `gitnexus mcp` 从 registry 发现 0 个仓（不读仓内 `.gitnexus/`）→ 查询解析不到 repo → readline 死锁。GitNexus 1.6.8 是两步式：`analyze` 建仓内 `.gitnexus/`、`index` 注册全局 registry。修复：`ensure_indexed` analyze/skip 后幂等调 `gitnexus index <repo>`，index 失败→`success=False` 走 `PentestError` fail-fast 不再死等（TDD，15 测试绿，feat/fork-py 本地未 push）。现场止血：`gitnexus index <repo>`（秒级，不重新分析）。GitNexus 真不可用（CLI 没装 / 索引坏）仍会影响所有确定性轨。

---

## 4. cost 计费（per-profile 定价 + 双引擎统一自算，2026-07-09）

supernova 的 LLM 成本核算**双引擎统一自算**——claude（`providers_anthropic._extract_cost`）/ openai（`openai_result_mapper`）引擎都经 `agents/pricing.py::compute_cost(model, usage)` 按 token 用量 × 价目表算 cost（claude 引擎**不再读 SDK `total_cost_usd`**），消除双引擎不对称。

- **价目表 per-profile 化**：内置 `BUILTIN_PRICING_CNY`（GLM + DeepSeek，含 glm-5.3-flash 2026-08-28 核对；默认 CNY）∪ `SUPERNOVA_PRICING_OVERRIDE` 指向的 JSON 文件（经 env_loader `override=True` 天然 per-profile，切 profile 即切定价）。override 新 schema：`{"currency":"CNY"|"USD","models":{model:{input,output,cache_read,cache_creation}}}`（单位：本币/百万 token）；旧 flat schema `{model:{...}}` 回落 CNY。示例见 `.env.profiles.example/*.pricing.json`。
- **4 档计费**：`cost = (input×P_in + cache_creation×P_cc + cache_read×P_cr + output×P_out)/1e6`（本币直达，**不再 ÷ 汇率**——单 session cost 是 cost_currency 币种金额）。**input_tokens 须已归一为不含 cache 命中**（openai mapper 负责 `max(raw-cached, 0)`）。
- **字段语义不变量**：全链路保留 `cost_usd`/`total_cost_usd` 字段名（值 = cost_currency 币种金额，非真美元），新增 `cost_currency: str`（默认 `"USD"`）。展示层（CLI renderer / Web 前端 `fmtCost`）按 `cost_currency` 显示 ¥/$。旧 session.json（无 cost_currency）读时默认 USD。
- **未知模型** → `CostAmount(0.0, currency)` + warning（守「不假估算」），可经 `SUPERNOVA_PRICING_OVERRIDE` 补充。
- **全局价目表 web 管理（2026-08-28）**：定价优先级链 `内置 < profile env（SUPERNOVA_PRICING_OVERRIDE）< 全局表（web 管理）< 工作区覆盖`。全局表 = `<workspaces_dir>/pricing.json`（web 设置页 admin 编辑，`PricingStore` 原子写），web 进程启动 `create_app` 经 `os.environ.setdefault("SUPERNOVA_GLOBAL_PRICING", ...)` 注入路径（worker 子进程继承；CLI 直跑未设 → 零行为变化）；**界面保存全局表 = 完整生效表快照，保存即接管（压过）profile env 层**。工作区覆盖 = `<ws>/pricing.override.json`（SSOT = 文件存在性，**不写 ws config env 段**——env 文本框「文本=完整定义」契约），`scan_manager._resolve_env_overrides` 注入压过 env 文本段手写同键。`pricing.py` 每次现读文件 → 落盘即生效（worker 下一次计费用新价，无需重启；历史 session cost 已落盘不变）。API：`GET/PUT/DELETE /api/pricing`（全员/admin/admin）+ `/api/workspaces/{ws}/pricing`（member/manager/manager）。前端 `PricingEditor` 列序 = `模型|输入|输出|缓存读取|缓存写入|…`（2026-08-28 修「列名和值对不上」：输入/输出相邻对齐官方定价页序，缓存档靠后成组——缓存档插中间曾致抄数错位）。
- **模型级币种（2026-08-28）**：价格对象内可选 `currency` 键（仅 `"CNY"|"USD"`）覆盖表级默认——混合币种表（GLM CNY + 海外模型 USD）可表达；`compute_cost` 取值链 `模型级 → 表级（最高优先非空层）→ CNY`；缺省/垃圾回落表级；**高层整对象替换时 currency 随之覆盖（高层缺键 → 低层模型级丢失，与价格替换语义自洽）**。web 链路：`PricingStore.resolve_effective` 行输出 `currency` 兄弟字段（**null = 跟随表级，不 resolve 成具体值**——保住跟随语义，否则 ws 覆盖快照会把每行写成显式币种）；`PriceTiers.currency` 必须显式声明（pydantic v2 默认 ignore extra，漏声明 = 前端字段被静默丢弃）；前端每行「默认/¥/$」三钮，表顶切换语义 = 默认币种。混合币种 session 的聚合（`metrics_tracker` total/cost_currency）仍 last-wins + 直加，仅 warning 可观测（分币种聚合另列后续）。

详见 spec `docs/superpowers/specs/2026-07-09-per-profile-cost-pricing-design.md` + `docs/superpowers/specs/2026-08-28-global-pricing-console-design.md`。
