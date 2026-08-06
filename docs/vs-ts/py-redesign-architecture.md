# supernova-py 重构设计架构图解

> 对比对象：`/root/shannon`（TypeScript 原始版，下称 **TS 版**，`main` 分支） vs `/root/shannon-py`（Python 重构版，下称 **PY 版**，分支 `feat/fork-py`）。
> 本文档定位：**以图为主的「设计架构总览」**——把 PY 重构的核心设计（双轨、双引擎、补召回、组合漏洞、黑盒多角色账号验证、跨仓、PoC、cost 计费）用架构图串成一张全景，回答「这些设计长什么样、怎么连起来」。
>
> 与同目录其它文档分工（见 [`README.md`](./README.md)）：
> - [`scan-effectiveness-gains-vs-ts.md`](./scan-effectiveness-gains-vs-ts.md) = **效果对照矩阵**（W/B/P 编号 + 验证状态），讲「效果提升在哪」；
> - [`refactor-scan-optimization-vs-ts.md`](./refactor-scan-optimization-vs-ts.md) = **踩坑叙事**，讲「踩到什么坑 → 怎么排查 → 怎么改」；
> - [`intra-first-taint-mechanism.md`](./intra-first-taint-mechanism.md) / [`second-order-storage-taint-mechanism.md`](./second-order-storage-taint-mechanism.md) = **单机制深挖**；
> - **本文** = **设计架构图解**，讲「整体设计长什么样」，以图为主，单机制只做要点 + 外链。
>
> 验证口径沿用主文档：✅ 真机已验 / ⏳ 待真机。效果验证状态标对应能力点编号（W/B/P）。架构不变量见 [`../../CLAUDE.md`](../../CLAUDE.md) §1-§2。

---

## 0. 全景

TS 版只有一条腿：每个漏洞类跑一个 LLM agent，自己读 recon、自己 grep、自己追 source→sink、自己下 verdict，没有代码索引、没有规则库、没有调用图、单仓视角。漏了就彻底漏，没有第二条腿对账。

PY 重构的核心就是给这条单轨加一条确定性兜底腿，并围绕这条新腿把整件事做成一个可演进的设计：

- **双轨 verdict OR**——确定性轨和 LLM 轨各跑各的，合并器取并集，任一轨挂掉不归零；
- **多层补召回**——确定性这条新腿不是装上就灵，规则不全、链会断、大仓会超时，要一层层补；
- **双引擎可互换**——底座抽象成 `BaseProvider`，claude-agent-sdk 和 openai-agents 同一份 prompt 都能跑；
- **组合漏洞**——stored XSS / 二阶 SQLi 这种跨函数、跨存储介质的链，用存储中转 join 程序化召回；
- **黑盒多角色账号验证**——越权检测不再单账号猜，多身份配 baseline 打硬证据；
- **跨仓**——多服务多仓库编排出服务拓扑和信任边界，补 TS 单仓盲区；
- **结构化 PoC** + **per-profile cost 计费**——交付质量和成本核算。

下图是全景，后面各节展开。

```
                          ┌─────────────────────────────────────────────┐
                          │          待测目标（代码仓 / web url）          │
                          └─────────────────────────────────────────────┘
                                            │
        ┌───────────────────────────────────┴───────────────────────────────────┐
        │                                                                       │
        ▼                                                                       ▼
┌───────────────────────┐                                           ┌────────────────────────┐
│   白盒双轨（§3-§5）    │                                           │  黑盒 exploitation（§6） │
│                       │                                           │                        │
│  GitNexus 确定性轨     │  verdict OR  │  LLM 轨（纯 LLM，TS 式）      │  exploitation-only      │
│  + 多层补召回（§4）    │◄────────────►│  vuln-*.txt agent           │  + endpoint_verify      │
│  + 组合漏洞（§4⑥）     │              │  自给自足                   │  + 多角色账号验证（§6）   │
│  + authz 深度 agent(§5)│              │                             │  + verdict 4 层校验     │
└───────────┬───────────┘              └──────────────┬──────────────┘  └───────────┬────────────┘
            │          exploitation_queue.json 文件桥接  │                             │
            └──────────────────────┬─────────────────────┘                             │
                                   ▼                                                   │
                       ┌────────────────────────┐    externally_exploitable==True      │
                       │  PoC 产物化（§8）       │◄───────────────────────────────────┘
                       │  curl + Burp raw       │
                       └───────────┬────────────┘
                                   ▼
                       ┌────────────────────────┐
                       │  报告 + cost 计费（§10）  │
                       └────────────────────────┘

        全程经双引擎抽象（§2）：SUPERNOVA_AI_PROVIDER 切 claude-agent-sdk / openai-agents，业务层不感知
        跨仓（§9）：multi-repo 编排 + 跨仓关联 agent，TS 单仓 → PY 多仓拓扑
```

---

## 1. 分层单体 + 引擎无关的业务层

PY 是分层单体仓库，三个核心包 + 三个编排/接入包，依赖单向向下：

```
┌─────────────────────────────── 接入 / 编排层 ───────────────────────────────┐
│  packages/web   (FastAPI + SPA：扫描管理 / 认证档案库 / 实时页 / 报告页)      │
│  packages/worker(聚合白+黑盒 worker，共用镜像)                               │
│  packages/multi (multi-repo 编排 + 跨仓关联 agent)                          │
│  packages/combined (CLI 单进程聚合入口)                                     │
├────────────────────────────────────────────────────────────────────────────┤
│  packages/whitebox  (白盒 Temporal workflow / activities / prompts)         │
│  packages/blackbox  (黑盒 workflow / exploit+recon+endpoint_verify / report)│
├────────────────────────────────────────────────────────────────────────────┤
│  packages/core  (共享基础层：models / config / agents / code_index / services)│
└────────────────────────────────────────────────────────────────────────────┘
        依赖方向：web/worker → whitebox/blackbox → core；multi → core。core 不依赖上层。
```

关键设计：**业务编排层引擎无关**。白盒/黑盒 workflow 只调统一入口 `run_claude_prompt()`，不关心底下跑的是 claude-agent-sdk 还是 openai-agents——引擎切换是 core 层 `agents/` 的事，业务侧零改动。这跟 TS 版有结构差异：TS 只有一个 `claude-executor.ts` 直连 Claude Code CLI，没有抽象层；PY 把「调 LLM」抽成 `BaseProvider`，于是双引擎能互换。

> 文件：`packages/core/src/supernova_core/agents/executor.py:66`（`AgentExecutor`）、`runner.py:116`（`run_claude_prompt`）。

---

## 2. 双引擎抽象：claude-agent-sdk / openai-agents 可互换

```
        业务侧（whitebox / blackbox / core）
                    │
                    │  run_claude_prompt(prompt, cwd, model_tier, output_format, ...)
                    ▼
          ┌─────────────────────┐
          │   AgentExecutor      │   统一编排：prompt 加载 / git checkpoint /
          │   (executor.py)      │   deliverable 校验 / 指标采集 / 失败记账
          └─────────┬───────────┘
                    │  provider.call(prompt, ...)
                    ▼
          ┌─────────────────────┐
          │   BaseProvider(ABC)  │   providers.py:63  抽象基类，唯一方法 .call()
          │   create_provider()  │── SUPERNOVA_AI_PROVIDER env 路由
          └─────┬───────────┬───┘
        glm-anthropic        glm-openai
                │                │
                ▼                ▼
   ┌────────────────────┐  ┌─────────────────────────────┐
   │ AnthropicProvider   │  │ OpenAIProvider              │
   │ = claude-agent-sdk  │  │ = openai-agents(纯框架)      │
   │ 底层 = Claude Code   │  │ Agent/Runner/handoffs 原语   │
   │  CLI 子进程          │  │                             │
   │                    │  │ tools_openai/{bash,fs,web,   │
   │ CLI 自带全套内置工具 │  │  task}.py  自维护工具集       │
   │ Agent 子代理委派     │  │ task function_tool +        │
   │ （零工具代码）       │  │  _make_subagent_runner 对齐  │
   └────────────────────┘  └─────────────────────────────┘
```

两引擎在**代码流程上完全一样**（同一份 prompt、同一套 `run_claude_prompt` 调用），差异只在底层智能体能力的来源：

| 维度 | claude-agent-sdk（`glm-anthropic`） | openai-agents（`glm-openai`） |
|---|---|---|
| 底层 | Claude Code CLI 子进程 | 纯框架（Agent/Runner/handoffs） |
| 工具来源 | CLI 自带全套内置工具（Read/Bash/Grep/Edit + `Agent` 子代理委派） | 框架不附通用内置工具，必须自维护 `tools_openai/{bash,fs,web,task}.py` |
| 子代理委派 | CLI 内置 `Agent` 工具，零代码（prompt 里 "delegate to Task Agent" 直接驱动） | 手写 `task` function_tool + provider 注入 `_make_subagent_runner`，对齐 CLI |
| 对齐 TS | 100%（同 TS `claude-executor.ts`：`bypassPermissions` / 无 `allowedTools` / `maxTurns=10_000`） | 功能性对齐（同一份 prompt 能跑）+ 自维护成本 |

差异根因（CLAUDE.md §2 反复强调，别当退化去「修」）：CLI 是运行时，自带工具；纯框架不是，要自己造。原始 TS 的 subagent 分发靠 CLI 内置、零代码，claude 轨 100% 对齐；openai 轨注定要自造委派工具（手写 `task` 或 SDK 的 `as_tool` 都行，都逃不掉维护子 agent 定义）。这是 SDK 哲学差异，不可消除。

Task 5（`feat/fork-py`）已把对齐落实：openai 引擎经 `tools_openai/task.py` 的 `task` function_tool 对齐 CLI 的 `Agent` 子代理委派，两引擎跑同一份 vuln prompt，prompt 不改。真机探针各一份：`scripts/validate_glm_task_probe.py`（claude 轨）、`scripts/validate_openai_task_probe.py`（openai 轨，已验 PASS，GLM 正确发起 `task` 子代理委派并产出 SQLi 判定）。

> 文件：`agents/providers.py`（`BaseProvider`、`create_provider`、`resolve_tier_model`）、`providers_anthropic.py`、`providers_openai.py`、`tools_openai/{exec,fs,web,task}.py`。

---

## 3. 双轨设计：确定性轨 + LLM 轨，verdict OR ★核心不变量

这是 PY 相对 TS 最核心的增量，也是项目最重要的架构不变量（CLAUDE.md §1）。

```
                           代码仓
                             │
          ┌──────────────────┴──────────────────┐
          │                                     │
          ▼                                     ▼
┌───────────────────────────┐          ┌───────────────────────────┐
│   GitNexus 确定性轨        │          │   LLM 轨（纯 LLM）         │
│                           │          │                           │
│ code_index + 规则库        │          │ vuln-*.txt agent           │
│ + taint 传播图             │          │ 读 recon + 自 grep         │
│ → vuln_chain_builders     │          │ + 自派 Task 子代理追链      │
│   提候选链                 │          │ （TS 式，自给自足）         │
│ → chain_verdict            │          │                           │
│   (轻量 LLM 单次判定,      │          │ ⚠ 铁律：不吃确定性产物     │
│    非 agent)               │          │   （不 @include 确定性层）  │
└─────────────┬─────────────┘          └─────────────┬─────────────┘
              │                                      │
   <vuln>_gitnexus_queue.json           <vuln>_exploitation_queue.json
              │                                      │
              └──────────────────┬───────────────────┘
                                 ▼
                  ┌──────────────────────────────┐
                  │  dual_track_merger（合并器）  │
                  │  按 (vuln_type,location,sink) │
                  │  去重 + verdict 取并集 OR      │
                  │                              │
                  │  vulnerable = vul(llm)        │   ◄── dual_track_merger.py:157
                  │        OR vul(gitnexus)       │
                  └──────────────┬───────────────┘
                                 │
            ┌────────────────────┼────────────────────┐
            ▼                    ▼                    ▼
     both = high          llm-only =           gitnexus-only =
     (两轨都报,            needs_review         needs_review
      confidence=high)    (单轨报,待复核)       (单轨报,待复核)
```

**OR 为什么成立（铁律）**：两轨链来源不同（确定性产链 vs LLM 自主探索），互不喂数据——确定性轨挂了 LLM 轨照跑，反之亦然。所以 `vulnerable = _is_vulnerable(llm) or _is_vulnerable(gitnexus)`（`dual_track_merger.py:157`）。一旦把确定性产物喂进 LLM 轨 prompt，LLM 轨就会依赖确定性层（而确定性层经常超时、不可用），独立性就破了。这条铁律由 `tests/prompts/test_static_dataflow_hints_decoupling.py` 锁定。

**`externally_exploitable` 是可达性标签，不是 verdict**（`dual_track_merger.py:105`）：true=公网可达 / false=内部或跨服务。它不能被 verdict 覆写。authz 三类的 ee 走 per-class OR（`_authz_ee_or`），守 inj 跨服务语义。

**双轨可配置**：`SUPERNOVA_LLM_TRACK_ENABLED`（默认 `"1"`）。`=0` 时只关 inj/xss/ssrf 的 taint agent（GitNexus chain_verdict 主干兜底）；pre-recon / recon / authz / auth 的 LLM 全保留——authz Vertical/Context + auth 是 GitNexus 做不了的，关了会失明。token 紧张关 LLM 轨走纯确定性兜底，token 宽裕开双轨 OR。

**代价**：token 翻倍（初期明确不省 token 换少漏）。

> 效果与真机硬证据（authz 0→21 正是确定性轨独立兜回、LLM 轨整段超时归零的场景）见 [主文档 §2 / §4.1](./scan-effectiveness-gains-vs-ts.md)（W1 / W6-W7）。合并器 OR 语义、降级、开关的踩坑叙事见 [叙事版 §1](./refactor-scan-optimization-vs-ts.md)。

---

## 4. 补召回机制全景：让确定性这条腿真正能下脚

双轨落地后马上暴露：GitNexus 轨不是装上就灵——规则不全、taint 追链会断、大仓会超时，每一类都让这条新腿瘸掉，等于双轨退化回单轨。补召回就是一层层把这些瘸的地方接上。下图是 GitNexus 轨从代码到候选链的完整流水线：

```
                              代码仓
                                │
                   ┌────────────▼────────────┐
                   │  code_index 确定性层     │
                   │  build_code_index        │
                   └────────────┬────────────┘
                                │
         ┌──────────────────────┼──────────────────────┐
         ▼                      ▼                      ▼
  ┌──────────────┐      ┌──────────────┐       ┌──────────────┐
  │ ① sink 召回   │      │ ② source 召回 │       │ ③ 调用链      │
  │ sink_rules   │      │ source_rules │       │ process trace │
  │ .yml 五语言  │      │ .yml         │       │ (GitNexus 原生)│
  │ 规则匹配     │      │              │       │ 非自造 BFS     │
  └──────┬───────┘      └──────┬───────┘       └──────┬───────┘
         │ 未命中              │ 含 sink 函数          │
         ▼                     ▼                       │
  ┌──────────────┐      ┌──────────────┐               │
  │ sink_candidates│     │ source 补召回 │               │
  │ .yml 候选筛选  │     │ (对含 sink 的 │               │
  │ 按语言+receiver│     │  函数跑规则 + │               │
  │ 精确筛选       │     │  LLM 软 source)│              │
  │ (替旧 flat 正则)│    └──────┬───────┘               │
  └──────┬───────┘             │                       │
         ▼                     ▼                       │
  ┌──────────────┐      ┌──────────────┐               │
  │ LLM 补召回    │      │ ④ intra-first │               │
  │ 软 SinkCallSite│     │ taint         │               │
  │ rule_id=      │      │ (含 sink 函数 │               │
  │ "llm-discovered"│     │  直接产单步   │               │
  │ + rule_gap_   │      │  taint,不经链)│               │
  │   report 反哺 │      └──────┬───────┘               │
  │ LLM 不可用 →  │             │                       │
  │  is_entry_hint│             │                       │
  └──────┬───────┘             │                       │
         │                     │                       │
         └─────────────────────┼───────────────────────┘
                               ▼
                   ┌─────────────────────────┐
                   │  taint 传播图            │
                   │  parameter_graph.json   │
                   │  + ⑤ 超时 fallback 兜底  │   超时/异常跳过的 sink 函数
                   │    taint（不丢弃）       │   产兜底 IntraResult 填回
                   │  + ⑥ 二阶存储 join       │
                   └────────────┬────────────┘
                                ▼
                   ┌─────────────────────────┐
                   │  vuln_chain_builders    │
                   │  inj / xss / ssrf /     │
                   │  second_order           │
                   └────────────┬────────────┘
                                ▼
                   ┌─────────────────────────┐
                   │  chain_verdict          │
                   │  (轻量 LLM 单次判定)     │
                   └────────────┬────────────┘
                                ▼
                      <vuln>_gitnexus_queue.json
```

六层补召回，每层治一类「整类归零」的根因：

| # | 补召回层 | 治的根因 | 关键文件 | 效果 |
|---|---|---|---|---|
| ① | **sink 规则匹配** | TS sink 靠 prompt 文本清单，漏 grep 就漏报 | `code_index/data/sink_rules.yml`（五语言规则库） | W2 ⏳ |
| ② | **LLM 补召回（软 sink）** | 规则盲区类（NoSQL、log4j、框架特有 sink） | `sink_candidates.yml` + `sink_discovery_llm.py`；LLM 不可用退 `is_entry_hint` | W3 ⏳ |
| ③ | **source 补召回 + intra-first taint** | handler 不入链 → 整类全空（NodeGoat 三类 0 flow） | `source_discovery_llm.py` + `chain_propagator.py::produce_intra_first_taint_flows` | W5 ⏳（NodeGoat 0→N） |
| ④ | **调用链下沉 process trace** | 自造 BFS 拼空壳链、cypher LIMIT 撞 readline 崩 | `gitnexus_call_graph.py` + `process_trace_reader.py` | W6/W7 ✅ **authz 0→21** |
| ⑤ | **SSRF 断链 fallback** | 「sink 参数是局部变量对象」跨函数链全丢 + 超时整段丢 | `code_index/__init__.py::backfill_skipped_taint_fallback` | W8 ⏳ |
| ⑥ | **二阶存储 join（组合漏洞）** | stored XSS / 二阶 SQLi 双轨系统性漏 | `storage_rules.yml` + `second_order_join.py` + `second_order_builder.py` | W4 ⏳ |

**② 的候选筛选设计**（`sink_candidates.yml`）：替掉旧版 flat 子串正则，按 `languages` + `receivers_any` 精确筛选——只决定「要不要送 LLM」，不产 SinkCallSite；宽词（where/format/open/fetch）去噪（这些交给确定性规则 + LLM 轨链，不靠补召回宽词）；go/java 大小写敏感（导出方法首字母大写是语义），其余不敏感。

### 4.1 组合漏洞：二阶存储中转 join（⑥ 详解）

stored XSS、二阶 SQLi 这类**组合漏洞**，双轨都系统性漏：write 端做了净化被判 safe 丢弃，read 端信任存储数据直接进 sink 却不被识别为污点源。根因是确定性层没有「存储中转」这个概念——source_rules 没有 storage-read、sink_rules 没有 storage-write、propagator 只连单跳。

PY 引入三个抽象把这条链程序化接上：

```
   write 端                         存储介质                    read 端
   StorageWritePoint                StorageNode                 StorageReadPoint
   (非危险 sink,不进                 (medium, token)             (新 source flavor=storage,
    sink_call_sites,避免             join 枢纽                    进 source_points)
    单跳轨误报 DB 写入)
        │                                │                          │
        │  write 端 tainted              │   按 (medium,token)        │  read 端单跳 vulnerable
        └─────────────►  二阶 verdict ◄──┴──────────────────────────┘
                        write tainted ∧ read vulnerable
```

- `StorageWritePoint` 故意不算危险 sink（不进 `sink_call_sites`），避免单跳轨把普通 DB 写入误报成漏洞；
- `StorageReadPoint` 是新 source 风味（flavor=storage），进 `source_points`；
- `StorageNode` 是 `(medium, token)` 的 join 枢纽，按存储介质（db/config/cache/file）和表名 token 二分图匹配——刻意用 O(|W|×|R|) 二分图而不是 BFS，因为「误连比漏报更糟」；
- **判定模型**：write 端 tainted ∧ read 端单跳 vulnerable = 二阶 verdict。read 端复用单跳 `chain_verdict`；
- token 边界守严：只有字面量 token（db/config/cache/file）能静态 join，动态/拼接 token 留给 LLM 轨 Task 4 hunter——静态解析不了的不硬连。

双轨协同：GitNexus ⑥ 走字面量 token（关轨兜底），LLM 轨走二阶方法论（开轨增强），verdict OR。守铁律 A——确定性产物不喂 LLM 轨。

> 完整分析逻辑（谁写锚点 / 产物 / 谁用 / 判定模型 / 四介质覆盖 / table-name 推断）见 [`second-order-storage-taint-mechanism.md`](./second-order-storage-taint-mechanism.md)。踩坑叙事见 [叙事版 §2.5](./refactor-scan-optimization-vs-ts.md)。

> ③ 的四步根因链见 [`intra-first-taint-mechanism.md`](./intra-first-taint-mechanism.md)；其余各层踩坑叙事见 [叙事版 §2](./refactor-scan-optimization-vs-ts.md)。

---

## 5. authz 深度 agent 轨：missing-control 的特殊处理

authz（和 auth）不是 source→sink taint，属 missing-control，确定性 sink 规则不覆盖。所以 authz 有自己的「GitNexus 风格」轨——**关键差异：用深度 agent（多轮），不是 taint 的轻量单次判定**，因为 authz 要追 owner 逻辑、guard 顺序，比 taint 链判定重得多。

```
                       GitNexus 确定性产物
                   （process trace + 端点 + sink）
                                │
                                ▼
                ┌───────────────────────────────┐
                │  authz IDOR 候选生成           │
                │  用户可控源 + 可达 side-effect │
                │  sink + 无 ownership 守卫      │
                │  + 框架端点 / OpenAPI 扩展     │
                └───────────────┬───────────────┘
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
            候选 > 0                    候选 = 0
                    │                       │
                    ▼                       ▼
   ┌───────────────────────────┐  ┌───────────────────────────┐
   │ run_gitnexus_verdict_agent │  │ agent 自主探索 IDOR        │
   │ （多轮深度 agent）          │  │ （从 process trace entry   │
   │ 吃候选深判 owner/guard     │  │  出发找未守卫 sink 路径）   │
   └─────────────┬─────────────┘  └─────────────┬─────────────┘
                 │                              │
                 └──────────────┬───────────────┘
                                ▼
                   authz_gitnexus_queue.json
                                │
                                │  与 LLM 轨 vuln-authz 双轨 OR
                                ▼
                          最终 authz 漏洞
```

auth 走纯 LLM 轨（`vuln-auth` agent 9 类方法论，对齐原始 shannon；曾有 auth GitNexus 轨，2026-07-14 删了——踩「确定性产物不喂 LLM 轨 prompt」铁律 + CORS 越界被裁的 misconfig）。

已修一个致命 bug：探索分支 agent 产了 4 个 IDOR 候选，落地 0 条——探索 prompt schema 没 ID 字段，`parse_lenient` 把缺必填 ID 的条目全丢了且不报。修法是 `_parse_gitnexus_verdict_output` parse 前补序列化 ID + 读 `warnings` 打日志。真机 hr_20260713 实测 4→0 修复。

> 效果与硬证据（authz 0→21、候选丢弃修复）见 [主文档 §4.1](./scan-effectiveness-gains-vs-ts.md)（W6）；排查叙事见 [叙事版 §3](./refactor-scan-optimization-vs-ts.md)。

---

## 6. 黑盒 exploitation 链 + 多角色账号验证 ★增强可信度

### 6.1 exploitation-only 基线 + 多角色认证档案

两边黑盒都是 exploitation-only（不独立发现漏洞，复用白盒 queue，对齐 TS 基线）。PY 在 exploitation 阶段补了端点验证、auth 客观校验、verdict 校验，并新增了 TS 完全没有的**多角色认证档案库**和**独立登录验证**：

```
┌─────────────────────────── 认证档案库（PY 新增，TS 无）──────────────────────────┐
│                                                                                  │
│  configs/*.yaml ──seed_from_config(启动)──► workspaces/.system/auth-profiles.yaml │
│  (已写好的登录配置)                          (全局共享只读系统档案, 所有 ws 可见)  │
│                                                                                  │
│                            workspaces/<ws>/auth-profiles.yaml                    │
│                            (per-ws 用户档案, Fernet 字段级加密)                   │
│                                                                                  │
│  AuthProfile { name, login_url, login_type, login_flow,                          │
│                credentials: [AuthProfileCredential×N 多角色], scope }            │
│  AuthProfileCredential { role, username, password, totp_secret,                  │
│                          email_login, verify_status }                            │
│                                                                                  │
│  透明 fallback：get(ws,id) ws 优先 → miss → .system；read(ws) 合并两段            │
│  系统档案只读守卫：update/delete scope=system → 403；"." 开头 ws 名拒（防碰撞）    │
└──────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        │ 选档案（不选角色）→ 读所有 credentials[]
                                        │  → derive_privilege_tier(role) 推 high/low
                                        │  → primary=首个 low，其余进 accounts[]
                                        ▼
┌─────────────────────────── 独立验证（AuthValidationWorkflow，PY 新增）──────────────┐
│  "测试登录"：不跑扫描、只测登录（TS 无独立入口）                                    │
│  过程落盘 events.ndjson + 非阻塞 describe() 查状态（修 result() 阻塞误判 failed）  │
│  + verify-log 端点回看 + finally 只删明文 scan-config.yaml 保留诊断产物             │
│  verify_status: unverified → success / failed(failure_point)                      │
└──────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────── 黑盒 exploitation 链（exploitation-only）──────────────┐
│                                                                                    │
│  白盒 exploitation_queue.json ──文件桥接──► endpoint_verify agent                  │
│  （复用白盒结果，reuse_whitebox_scan_id 必填）   (B1 端点存活+路由前缀探测)          │
│                                                    │                               │
│                              auth-state 客观交叉验证 (B2 防 LLM 误判)              │
│                                                    ▼                               │
│                                          exploit × 5 agent                         │
│                          ┌───────────────────────────────────────────┐            │
│                          │  多身份 authz 对比协议（§6.2）             │            │
│                          │  N 身份 N session + auth-state 文件桥接    │            │
│                          └───────────────────┬───────────────────────┘            │
│                                              ▼                                     │
│                              verdict 4 层校验 + 归一化 (B3)                         │
│                              + coverage gap 可视化 (B4) + 血缘溯源 (B5)            │
└──────────────────────────────────────────────────────────────────────────────────┘
```

档案库的几个设计点：

- **数据结构**：`AuthProfile` 持有 `credentials: [AuthProfileCredential×N]`，一条档案多个角色（admin/user/viewer…）。`login_flow` 取代了原始 TS 必填的 `success_condition` 死字段，判定并入自然语言步骤（"成功标志:URL 含 /dashboard"）。
- **存储**：per-ws 落 `workspaces/<ws>/auth-profiles.yaml`，系统级落 `workspaces/.system/auth-profiles.yaml`。复用 `CredentialVault` 的 Fernet 字段级加密（password/totp_secret）。
- **透明 fallback**：`get(ws, id)` 先查 ws 档案，miss 再查 `.system`；`read(ws)` 合并两段返回。写方法用纯段读 `_read_segment`（不用合并版 `read`），否则系统档案会被错误持久化到 ws 文件。
- **系统档案 seed**：`configs/*.yaml` 启动时经 `seed_from_config` seed 成全局共享只读系统档案（所有 ws 可见），按 name 去重、幂等。系统档案 update/delete 返 403。
- **`scan-config.yaml` 明文边界**：持久化层加密，但 per-scan 的 `scan-config.yaml` 必须明文——它是 core 合流点（web 写 YAML → core `parse_config` 直读，core 不解密）。明文债靠 0600 权限 + 用后即删缓解。

### 6.2 多角色账号如何增强越权可信度（核心）

单身份黑盒 authz 是**根本缺陷**，不是便利性缺口：证明「user A 访问了 user B 的私有数据」需要 B 的数据做 baseline，得登录成 B。单账号只能枚举 ID 看 200，分不清「别人的私有数据」和「我本就被允许看的数据」。多角色档案解的就是这个：

```
   AuthProfileCredential[] 多角色
            │
            │ derive_privilege_tier(role)  (精确匹配 admin→high, 其余→low)
            ▼
   ┌────────────────────────────────┐
   │  build_comparison_matrix       │
   │                                │
   │  Vertical:  low × high         │   low=attacker, high=baseline
   │  Horizontal: low 两两          │   互为 attacker/victim
   └───────────────┬────────────────┘
                   │  示例: admin + user1 + user2
                   │  Vertical:  user1→admin, user2→admin
                   │  Horizontal: user1↔user2
                   ▼
   ┌────────────────────────────────┐    ┌────────────────────────────────┐
   │  Horizontal (IDOR)             │    │  Vertical                      │
   │  victim(low) 读自己资源=baseline│    │  baseline(high) 访问确认       │
   │  attacker(low) 读同一资源       │    │  admin-only 端点存在           │
   │  → 匹配 baseline ⇒ EXPLOITED   │    │  attacker(low) 访问同端点       │
   │  （跨用户私有数据访问硬证据）    │    │  → reach ⇒ EXPLOITED           │
   └────────────────────────────────┘    └────────────────────────────────┘
                   │
                   ▼  判定铁律（收紧误报）
   ┌────────────────────────────────┐
   │  无 baseline 的任何「成功访问」 │
   │  一律 POTENTIAL，不得 EXPLOITED │   新增 potential verdict 档
   │  （降级可追溯 downgrade_reason）│   （比单身份更严）
   └────────────────────────────────┘
```

- **role → tier 自动推导**（`derive_privilege_tier`）：精确匹配，不做子串启发式。admin→high（baseline），其余→low（attacker）。名单可经 `SUPERNOVA_AUTHZ_HIGH_PRIV_ROLES` 扩。
- **对比矩阵**：Vertical 是每个 low × 每个 high（low 攻、high 守）；Horizontal 是 low 两两（互为攻守）。
- **比较协议**：Horizontal——victim 读自己的资源做 baseline，attacker 读同一资源，数据匹配即 EXPLOITED（跨用户私有数据访问硬证据）。Vertical——baseline 确立 admin-only 端点存在，attacker 能 reach 即越权。
- **判定铁律**：EXPLOITED 必须有 baseline 对比佐证；无 baseline 一律 POTENTIAL，不得 EXPLOITED。新增 `potential` verdict 档，比单身份更严，降级可追溯。
- **N 身份 N session**：`BrowserEngine.session_flag(任意字符串)` 接受任意 session id（TS 要扩 `PlaywrightSession` 枚举）。primary attacker 用 `authz-exploit` session，其余 account `{id}` 用 `authz-exploit-{id}` session；**auth-state 文件桥接**登录阶段和 exploit 阶段（session 不同名，靠 `auth-state-{id}.json` 解耦）。
- **完全向后兼容**：单角色 → 全 POTENTIAL（=现状）；无 accounts → byte-identical 等同今天。

### 6.3 与 TS 的差异

| 维度 | TS 版 | PY 版 |
|---|---|---|
| 部署形态 | 仅 cli + worker，无 web UI | web 平台 + 认证档案库/管理页/ProfilePicker 全新增 |
| 认证配置 | `Authentication.credentials` 单个（非 list） | `credentials[]` 多角色 + Fernet 加密 + 系统档案 seed |
| `success_condition` | 必填字段 | D1-D4 删除（死字段），判定并入 `login_flow` |
| 多身份 authz | 有设计（`2026-06-14-authz-multi-account-design.md`）从未落地，零 commit | core `Account` 模型 + `derive_privilege_tier` + 多身份登录循环 + authz-exploit 多 session 协议 |
| 独立验证入口 | 无 | `AuthValidationWorkflow` + 可观测性（events.ndjson + verify-log） |

> 效果对照见 [主文档 §6](./scan-effectiveness-gains-vs-ts.md)（B1-B5）；黑盒 vs TS 能力对比见 [叙事版 §7](./refactor-scan-optimization-vs-ts.md)。

---

## 7. 攻击链构造：把单点漏洞串成多步攻击路径

上面 §3-§5 产出的都是**单点漏洞卡**（这个 source→sink 有没有漏洞）。但真实攻击是多步的——攻击者先写入、再触发渲染、最后窃取数据。攻击链构造就是把单点卡串成「攻击者视角的多步故事」，跟组合漏洞（§4.1，召回层）、PoC（§8，产物层）是三个不同层次：

- **组合漏洞**（§4.1）= 召回层，把 write→存储→read→sink 这条链**召回**成一个 finding；
- **攻击链**（本节）= 展示层，把多个 finding **串**成多步攻击路径，进报告「攻击链」章节；
- **PoC**（§8）= 产物层，把可利用漏洞**变**成 curl/Burp 包。

攻击链本身也是双轨结构——GitNexus 确定性组装 + LLM 攻击链 agent，merge 后进 `attack_chains.json`：

```
   GitNexus 各类已判定 finding          LLM 攻击链 agent
   {vt}_gitnexus_queue.json             attack_chains_llm_queue.json
   （单跳 source→sink,已 verdict）       （LLM 创意推断多步链）
            │                                    │
            ▼                                    │
   ┌────────────────────────────┐                │
   │ assemble_attack_chains      │   确定性跨端点关联 │
   │ (attack_chain_assembler.py) │                │
   │                            │                │
   │ ① stored XSS 链：           │                │
   │   injection 写(sink=storage)│                │
   │   + xss 渲染(source=storage)│                │
   │   按存储 token join → 3 步链 │                │
   │   (input→storage→render)    │                │
   │                            │                │
   │ ② IDOR 链：多个缺归属校验    │                │
   │   的对象 ID 端点聚合         │                │
   └─────────────┬──────────────┘                │
                 ▼                                 ▼
   ┌──────────────────────────────────────────────────┐
   │  merge_attack_chains（合并器）                     │
   │  GitNexus 链 + LLM 链 → attack_chains.json        │
   └──────────────────────┬───────────────────────────┘
                          ▼
   ┌──────────────────────────────────────────────────┐
   │  report_assembler.render_attack_chains            │
   │  → 报告「攻击链」章节（每条链: id/name/steps/       │
   │    vuln_type/severity/confidence）                 │
   └──────────────────────────────────────────────────┘
```

两套来源：

- **GitNexus 确定性组装**（`attack_chain_assembler.py`）：evidence-driven，每步都有 GitNexus finding 背书。stored XSS 把 injection 的写（sink 是存储）和 xss 的渲染（source 是存储）按存储 token（profiles/users/orders…）join 成三步链；IDOR 把多个缺归属校验的对象 ID 端点聚合成一条链。GitNexus 不可用就返回 `[]`，LLM 轨兜底。
- **框架/前端分析组装**（`route_chain_builder.py`，从 TS `route-chain-builder.ts` 移植）：框架推断端点 + 前端路由分析关联，产 XSS 四步链（input→storage→retrieval→render）和 IDOR 链。这是 PY 跟 TS 共有的部分。

**双轨铁律照守**：组装只读两轨各自的产物做合并，不反向喂 LLM 轨 prompt（CLAUDE.md §1）。两步组装 + 渲染都非 fatal——增强报告，不阻塞扫描（attack chains 挂了单点漏洞卡照常出）。

> 文件：`packages/core/src/supernova_core/code_index/attack_chain_assembler.py`（GitNexus 组装）、`services/route_chain_builder.py`（框架/前端组装）、`services/report_assembler.py:48`（`render_attack_chains`）；wiring：`whitebox/pipeline/activities.py:1721`（`run_attack_chain_assembly_v2`）→ `workflows.py:497`。攻击链汇总口径修复见 [叙事版](./refactor-scan-optimization-vs-ts.md)。

---

## 8. PoC 产物化：externally_exploitable → curl + Burp raw

```
   最终漏洞队列（双轨合并后）
            │
            │  PoCGenerator.generate() 过滤 externally_exploitable == True
            │  （GitNexus builders 设 ee=(verdict=="vulnerable")；
            │    LLM 轨 vuln-*.txt prompt 里 LLM 自填 ee）
            ▼
   ┌────────────────────────────────┐
   │  PoCGenerator (poc_generator.py)│   报告生成后纯后处理，不动判定链路
   │                                │
   │  按 vuln_class 路由：            │
   │  inj/xss/ssrf 有 witness_payload│──► 纯模板生成（to_curl / to_burp_raw）
   │  auth/authz + body/path 缺口    │──► 富信息 LLM 单次结构化生成
   │  authz 多身份                   │──► _build_authz_pair（attacker+baseline 对）
   └───────────────┬────────────────┘
                   ▼
   ┌────────────────────────────────┐
   │  产物（黑白盒各一份 PoC md）     │
   │  • curl 命令                   │
   │  • Burp raw 包                 │
   │  • 置信度三档标注               │
   └───────────────┬────────────────┘
                   │
                   ▼  置信度三档
   ┌────────────────────────────────┐
   │  ✓ 已确认  verdict=vulnerable   │
   │           或黑盒 ∈ accepted_ids │
   │  ● 高置信  confidence=high      │
   │  ⚠ 疑似    其余                 │
   └────────────────────────────────┘
   安全：真实 token 不持久化（<AUTH_TOKEN> 占位符）；host 优先 web_url 退 TARGET[:PORT]
```

TS 的 PoC 散在 exploit 阶段的自由格式 markdown 里，没结构化 Burp 产物，`externally_exploitable` 下游也没人专门消费。PY 针对 ee==True 的漏洞纯后处理产 curl + Burp raw，从「markdown 抠 curl」变成「一键产出可复用包」。

效果验证：✅ 真机对历史 session 实测产出 11 个 curl PoC（XSS×2 / AUTHZ×4 / SSRF×1 + 变体）。

> 文件：`packages/core/src/supernova_core/services/poc_generator.py`（`PoCGenerator`、`to_curl`、`to_burp_raw`、`classify_confidence`）；wiring：`whitebox/pipeline/activities.py:1220`（`generate_poc_report`）→ `workflows.py:604`。叙事见 [叙事版 §5](./refactor-scan-optimization-vs-ts.md)（P1）。

---

## 9. 跨仓微服务关联：TS 单仓 → PY 多仓拓扑

```
   multi-repo.yaml（声明式多仓编排）
            │
            │  packages/multi/orchestrator.py
            ▼
   ┌────────────────────────────────┐
   │  逐仓跑白盒双轨（各自 session）  │
   └───────────────┬────────────────┘
                   ▼
   ┌────────────────────────────────┐
   │  cross-repo-correlation agent   │   进程内跑（非 Temporal activity）
   │  （cross-repo-correlation.txt） │   per-edge asyncio.Semaphore(3) 并发
   │                                 │   单边 try/except 隔离
   │  推断：服务拓扑 / 信任边界 /     │
   │        跨服务候选数据流          │
   └───────────────┬────────────────┘
                   ▼
   跨服务候选链（A 服务写队列 → B 服务消费拼 SQL）
                   │
                   ▼  黑盒侧 --correlated-workspace flag 穿透 4 层
   加载 topology 做 gateway 层验证
```

TS 的 `PipelineInput.repoPath` 是单一绝对路径，不支持多仓输入，跨服务数据流和跨仓信任边界是纯盲区（`vuln-authz.txt` 自己都写了 "Untraced Microservice Calls... could not be analyzed without their source code"）。PY 做声明式 multi-repo 编排 + 跨仓关联 agent，推断服务拓扑、信任边界、跨服务候选数据流；黑盒侧加载拓扑做 gateway 层验证。关联 agent 在编排器进程内跑（非 Temporal activity，规避 child-workflow 负担），per-edge 用 `asyncio.Semaphore(3)` 并发 + 单边 try/except 隔离。这是覆盖面的硬扩展。

> 效果对照见 [主文档 §5](./scan-effectiveness-gains-vs-ts.md)（W9）；叙事见 [叙事版 §4](./refactor-scan-optimization-vs-ts.md)。

---

## 10. cost 计费：双引擎统一自算，per-profile 定价

```
   claude 引擎                      openai 引擎
   providers_anthropic              openai_result_mapper
   ._extract_cost                   (归一化 input_tokens)
        │                                │
        └────────────┬───────────────────┘
                     ▼  统一经
          ┌──────────────────────────┐
          │ agents/pricing.py        │
          │ compute_cost(model,usage)│   按 token 用量 × 价目表算 cost
          └────────────┬─────────────┘   （claude 不再读 SDK total_cost_usd）
                       │
                       ▼  价目表 per-profile 化
          ┌──────────────────────────┐
          │ GLM_PRICING_CNY(默认 CNY) │
          │ ∪ SUPERNOVA_PRICING_      │   env 指向 JSON 文件
          │   OVERRIDE (override=True)│   切 profile 即切定价
          └────────────┬─────────────┘
                       ▼  4 档计费
   cost = (input×P_in + cache_creation×P_cc + cache_read×P_cr + output×P_out) / 1e6
          （本币直达，不再 ÷ 汇率）

   字段语义不变量：cost_usd / total_cost_usd 字段名保留（值=cost_currency 币种金额，非真美元）
                  + 新增 cost_currency（默认 "USD"，旧 session 读时默认 USD）
   展示层：CLI renderer / Web fmtCost 按 cost_currency 显示 ¥/$
   未知模型 → CostAmount(0.0, currency) + warning（守「不假估算」）
```

TS 的 cost 靠 SDK 返回，双引擎不对称、未知模型容易假估算。PY 双引擎统一自算 + per-profile 定价，消除不对称：claude 和 openai 都经 `agents/pricing.py::compute_cost(model, usage)` 按 token 用量 × 价目表算，claude 不再读 SDK 的 `total_cost_usd`。价目表 per-profile 化，切 profile 即切定价。

> 详见 spec `docs/superpowers/specs/2026-07-09-per-profile-cost-pricing-design.md`（CLAUDE.md §4）。

---

## 11. 设计不变量（铁律）汇总

PY 重构的架构不变量，改动前必读（CLAUDE.md §1-§2）：

1. **双轨独立性**（§3）：GitNexus 轨与 LLM 轨链来源不同、互不喂数据，只在合并器 verdict OR 交汇。不要把确定性层产物喂进 LLM 轨 prompt（`tests/prompts/test_static_dataflow_hints_decoupling.py` 锁定）。
2. **`externally_exploitable` 是可达性标签**（§3）：true=公网 / false=内部或跨服务，不能被 verdict 覆写。
3. **LLM 轨纯 LLM 自给自足**（§3/§4）：补召回只动 GitNexus 轨自己接 LLM，不破坏双轨独立性。
4. **双引擎可互换**（§2）：两引擎流程一致、prompt 不改。不要「切到 glm-anthropic 了事」丢 openai 引擎，也别让 openai 退化成单 agent 使两引擎行为分叉。openai 自维护工具是 SDK 哲学差异，不可消除，别当退化去「修」。
5. **`SUPERNOVA_LLM_TRACK_ENABLED=0` 语义收窄**（§3）：只关 inj/xss/ssrf 的 taint agent；pre-recon/recon/authz/auth 的 LLM 全保留（GitNexus 做不了）。
6. **`scan-config.yaml` 明文边界**（§6）：持久化层加密，但 per-scan `scan-config.yaml` 必须明文（core 合流点，`parse_config` 不解密）；明文债靠 0600 权限 + 用后即删缓解。
7. **多身份判定铁律**（§6）：EXPLOITED 必须有 baseline 对比佐证；无 baseline 一律 POTENTIAL。
8. **组合漏洞 token 边界**（§4.1）：只有字面量 token 能静态 join，动态/拼接 token 留给 LLM 轨，静态解析不了的不硬连——误连比漏报更糟。

---

## 12. 一句话总结

PY 重构的设计核心是给 TS 的单轨纯 LLM 加一条确定性兜底腿并把它真正跑通：双轨 verdict OR（§3）保召回对账和鲁棒性，多层补召回（§4）让确定性腿能下脚，组合漏洞二阶 join（§4.1）补 stored XSS / 二阶 SQLi 系统性漏报，authz 深度 agent 轨（§5）补 missing-control，双引擎抽象（§2）让 claude/openai 可互换，黑盒多角色账号验证（§6）把单账号越权从「枚举 ID 猜测」变成「baseline 对比硬证据」，PoC 产物化（§8）把「markdown 抠 curl」变成「一键 curl + Burp raw」，攻击链构造（§7）把单点漏洞串成多步攻击路径，跨仓关联（§9）打开 TS 单仓盲区，per-profile cost 计费（§10）消除双引擎不对称。所有设计受一组铁律约束（§11），最核心的是双轨独立性——确定性产物绝不喂 LLM 轨 prompt。

> 各设计点的安全效果与真机验证状态见 [主文档能力矩阵](./scan-effectiveness-gains-vs-ts.md)；踩坑排查过程见 [叙事版](./refactor-scan-optimization-vs-ts.md)；目录索引见 [`README.md`](./README.md)。
