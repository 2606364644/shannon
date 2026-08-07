# supernova-py 扫描效果设计架构图解

> 衍生自 [`py-redesign-architecture.md`](./py-redesign-architecture.md)：聚焦**安全扫描效果**相关的设计，去掉与扫描效果无直接关系的两节——
> - 原 §1「分层单体 + 引擎无关的业务层」（仓库 / 包结构，纯工程组织，不影响扫描召回与判定）；
> - 原 §5「authz 深度 agent 轨」（其越权检测效果在本篇 §4 黑盒多角色账号验证体现，单列的 missing-control 深判机制不在此展开）。
>
> 其余设计（双轨、双引擎、补召回、组合漏洞、黑盒多角色、攻击链、PoC、跨仓、cost）全部保留，章节重排为连续编号。
>
> 对比对象：`/root/shannon`（TypeScript 原始版，下称 **TS 版**，`main` 分支）vs `/root/shannon-py`（Python 重构版，下称 **PY 版**，分支 `feat/fork-py`）。
>
> 本文档定位：**以图为主的「扫描效果设计总览」**——把 PY 重构里直接影响「能扫出什么、扫得多准、扫得多全」的设计（双轨、补召回、组合漏洞、黑盒多角色账号验证、跨仓、PoC）用架构图串成一张全景，回答「这些设计长什么样、怎么连起来、对扫描效果意味着什么」。引擎抽象（§1）与 cost 计费（§8）作为支撑扫描跑通与核算的底座一并收录。
>
> 验证口径沿用主文档：✅ 真机已验 / ⏳ 待真机。效果验证状态标对应能力点编号（W/B/P）。架构不变量见 [`../../CLAUDE.md`](../../CLAUDE.md) §1-§2。与同目录其它文档分工见 [`README.md`](./README.md)。

---

## 0. 全景

TS 版只有单一轨：每个漏洞类跑一个 LLM agent，自己读 recon、自己 grep、自己追 source→sink、自己下 verdict，没有代码索引、没有规则库、没有调用图、单仓视角。漏了就彻底漏，没有第二条轨对账。

PY 重构的核心就是给这条单轨加一条确定性兜底轨，并围绕这条新轨把整件事做成一个可演进的设计：

- **双轨 verdict OR**——确定性轨和 LLM 轨各跑各的，合并器取并集，任一轨挂掉不归零；
- **多层补召回**——确定性这条新轨不是装上就灵，规则不全、链会断、大仓会超时，要一层层补；
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
│   白盒双轨（§2-§3）    │                                           │  黑盒 exploitation（§4） │
│                       │                                           │                        │
│  GitNexus 确定性轨     │  verdict OR  │  LLM 轨（纯 LLM，TS 式）      │  exploitation-only      │
│  + 多层补召回（§3）    │◄────────────►│  vuln-*.txt agent           │  + endpoint_verify      │
│  + 组合漏洞（§3⑥）     │              │  自给自足                   │  + 多角色账号验证（§4）   │
└───────────┬───────────┘              └──────────────┬──────────────┘  └───────────┬────────────┘
            │          exploitation_queue.json 文件桥接  │                             │
            └──────────────────────┬─────────────────────┘                             │
                                   ▼                                                   │
                       ┌────────────────────────┐    externally_exploitable==True      │
                       │  PoC 产物化（§6）       │◄───────────────────────────────────┘
                       │  curl + Burp raw       │
                       └───────────┬────────────┘
                                   ▼
                       ┌────────────────────────┐
                       │  报告 + cost 计费（§8）  │
                       └────────────────────────┘

        全程经双引擎抽象（§1）：SUPERNOVA_AI_PROVIDER 切 claude-agent-sdk / openai-agents，业务层不感知
        跨仓（§7）：multi-repo 编排 + 跨仓关联 agent，TS 单仓 → PY 多仓拓扑
```

---

## 1. 双引擎抽象：claude-agent-sdk / openai-agents 可互换

> 扫描效果视角：双引擎保证「同一套扫描逻辑在两个 LLM 后端上都能跑通」——引擎切换不改 prompt、不改判定链路，扫描能力不因后端不同而分叉。这一节是底座，真正的召回 / 判定增量在 §2 之后。

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

## 2. 双轨设计：确定性轨 + LLM 轨，verdict OR ★核心不变量

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
                  │  vulnerable = vul(llm)        │   ◄── 合并器取并集
                  │        OR vul(gitnexus)       │
                  └──────────────┬───────────────┘
                                 │
            ┌────────────────────┼────────────────────┐
            ▼                    ▼                    ▼
     both = high          llm-only =           gitnexus-only =
     (两轨都报,            needs_review         needs_review
      confidence=high)    (单轨报,待复核)       (单轨报,待复核)
```

**OR 为什么成立（铁律）**：两轨链来源不同（确定性产链 vs LLM 自主探索），互不喂数据——确定性轨挂了 LLM 轨照跑，反之亦然。所以合并时取并集：LLM 轨或 GitNexus 轨任一判 vulnerable，结果就判 vulnerable。一旦把确定性产物喂进 LLM 轨 prompt，LLM 轨就会依赖确定性层（而确定性层经常超时、不可用），独立性就破了。这条铁律有专门测试锁定——禁止 LLM 轨 prompt 引用任何确定性产物。

**`externally_exploitable` 是可达性标签，不是 verdict**：true=公网可达 / false=内部或跨服务，它不能被 verdict 覆写。authz 三类各自的可达性按类取 OR，守住 inj 的跨服务语义。

**双轨可配置**：一个开关（`SUPERNOVA_LLM_TRACK_ENABLED`，默认开）控制 LLM 轨。关掉时只关 inj/xss/ssrf 的 taint agent，由 GitNexus 主干兜底；但 pre-recon / recon / authz / auth 的 LLM 全保留——这几样 GitNexus 做不了，关了会失明。所以语义是「token 紧张走纯确定性兜底，token 宽裕开双轨 OR」。

**代价**：token 翻倍——初期就是拿 token 换少漏，不省钱。

> 效果与真机硬证据（authz 0→21 正是确定性轨独立兜回、LLM 轨整段超时归零的场景）见 [主文档 §2 / §4.1](./scan-effectiveness-gains-vs-ts.md)（W1 / W6-W7）。合并器 OR 语义、降级、开关的踩坑叙事见 [叙事版 §1](./refactor-scan-optimization-vs-ts.md)。

---

## 3. 补召回机制全景：让确定性这条轨真正能落地

双轨落地后马上暴露：GitNexus 轨不是装上就灵——规则不全、taint 追链会断、大仓会超时，每一类都让这条新轨失效，等于双轨退化回单轨。补召回就是一层层把这些失效的环节接上。下图是 GitNexus 轨从代码到候选链的完整流水线：

```
   代码仓
     │
     ▼
   确定性层（代码索引 + 规则库）
     │
     ▼
   ┌──────────────────────────────────┐
   │  三路召回                          │
   │   ① sink     ② source    ③ 调用链 │
   └────────────────┬─────────────────┘
                    ▼
   taint 传播图（含断链兜底、二阶存储 join）
                    ▼
        候选链  →  轻量判定  →  漏洞队列

   六层补召回，每层治一类「整类扫不出来」的病 —— 详见下表
```

六层补召回，每层治一类「整类归零」的根因：

| # | 补召回层 | 治的根因 | 效果 |
|---|---|---|---|
| ① | **sink 规则匹配** | TS sink 靠 prompt 文本清单，漏 grep 就漏报 | W2 ⏳ |
| ② | **LLM 补召回（软 sink）** | 规则盲区类（NoSQL、log4j、框架特有 sink） | W3 ⏳ |
| ③ | **source 补召回 + intra-first taint** | handler 不入链 → 整类全空（NodeGoat 三类 0 flow） | W5 ⏳（NodeGoat 0→N） |
| ④ | **调用链下沉 process trace** | 自造 BFS 拼空壳链、cypher LIMIT 撞 readline 崩 | W6/W7 ✅ **authz 0→21** |
| ⑤ | **SSRF 断链 fallback** | 「sink 参数是局部变量对象」跨函数链全丢 + 超时整段丢 | W8 ⏳ |
| ⑥ | **二阶存储 join（组合漏洞）** | stored XSS / 二阶 SQLi 双轨系统性漏 | W4 ⏳ |

**② 候选筛选**：规则没命中的可疑调用，先按语言 + 接收者精确筛一遍，再决定要不要送 LLM——替掉旧版模糊的子串匹配，少送噪音。

### 3.1 组合漏洞：跨存储的二阶链怎么接（⑥ 详解）

stored XSS、二阶 SQLi 这类**组合漏洞**，两条轨都会系统性漏掉。根子其实很直白：write 端做了净化，被判成 safe 丢掉了；而 read 端信任存储里的数据、直接送进 sink，却没被认成污点源——两头对不上，链就断了。再往下挖，根因是确定性层压根没有「存储中转」这个概念：source_rules 里没有 storage-read，sink_rules 里没有 storage-write，propagator 也只连单跳。

PY 引入三个抽象把这条链程序化接上：

```
   写入端                存储中转                 读出端
   数据带污点 ───►  数据库/配置/缓存/文件  ───► 直接进 sink
   （被判 safe，丢）           │                （被当成可信数据）
                              │ 按「介质 + 表名」配对
                              ▼
        写端有污点   ∧   读端可利用   =   二阶漏洞
             （stored XSS / 二阶 SQLi 就是这样漏掉的）
```

- **写点**不算危险 sink——免得普通 DB 写入被误报；
- **读点**是新污点源——存储里读出来的数据，不再默认当干净的；
- **存储节点**是配对枢纽，按「介质 + 表名」把写点和读点连起来（刻意不顺着调用链找：误连比漏报更糟）；
- **判定**：写端有污点 ∧ 读端可利用 = 二阶漏洞；
- **边界**：只有字面量表名能静态连，动态/拼接的留给 LLM 轨。

双轨协同：GitNexus ⑥ 走字面量 token（关轨兜底），LLM 轨走二阶方法论（开轨增强），verdict OR。守铁律 A——确定性产物不喂 LLM 轨。

> 完整分析逻辑（谁写锚点 / 产物 / 谁用 / 判定模型 / 四介质覆盖 / table-name 推断）见 [`second-order-storage-taint-mechanism.md`](./second-order-storage-taint-mechanism.md)。踩坑叙事见 [叙事版 §2.5](./refactor-scan-optimization-vs-ts.md)。

> ③ 的四步根因链见 [`intra-first-taint-mechanism.md`](./intra-first-taint-mechanism.md)；其余各层踩坑叙事见 [叙事版 §2](./refactor-scan-optimization-vs-ts.md)。

---

## 4. 黑盒 exploitation 链 + 多角色账号验证 ★增强可信度

### 4.1 黑盒只验证、不找漏：复用白盒结果 + 多角色认证档案

先把黑盒的定位说清楚：黑盒**不负责发现漏洞**，它的活是拿着白盒已经找到的漏洞清单，到真实跑着的目标上去验证「这条漏洞到底能不能利用」。这和 TS 的定位一致（exploitation-only）。

验证阶段 PY 多做了几件事，让结果更可信：先给端点探活、再用登录态做客观交叉校验（防止 LLM 看走眼、把一个 200 当成越权）、最后 verdict 走多层校验。其中 TS 完全没有、也是本节重点的，是两样东西——**多角色认证档案库**（一次配好 admin / 普通用户等多个账号）和**独立登录验证**（不跑扫描、只测这些账号能不能登成功）：

```
   多角色认证档案（PY 新增，TS 无）
   一次配好 admin / 普通用户等多个账号，所有扫描共用
                  │
                  ▼
   独立登录验证（PY 新增）
   不跑扫描，只测这些账号能不能登成功
                  │
                  ▼
   黑盒验证链（exploitation-only）
   复用白盒漏洞清单 → 探活 → 登录态校验 → exploit → verdict 校验
```

档案库有几个值得一提的设计：

- **一条档案挂多个角色**：一条档案描述多个身份（admin / user / viewer…），一个登录入口挂多份凭证。
- **存储分两层**：每个 workspace 一份本地档案，再加一份全局共享的系统档案；敏感字段加密。
- **取档案透明回落**：先查本地，查不到自动找全局，使用者不用关心档案在哪。
- **系统档案只读**：全局档案不许改删，防误覆盖。
- **scan-config 必须明文**：它是上下层合流点（core 不解密），靠权限 + 用后即删兜底。

### 4.2 为什么多角色账号能让越权判定更可信（核心）

这一节是关键，先把「为什么」讲透。

要证明「用户 A 能看到用户 B 的私有数据」，你得先登录成 B，看一眼 B 的数据长什么样——这就是 baseline；然后再用 A 去访问同一个资源，只有当 A 拿到的数据和 B 的对得上，才能认定「这是真的越权」。光有一个账号可不行：你只能换个 ID 试试返回是不是 200，却根本分不清看到的「是别人的私有数据」还是「这数据本来我就有权看」。所以单账号 authz 不是图省事少做了一步，而是**原理上就证不了**。多角色档案，解的就是这个：

```
   多个角色账号
        │
        ▼
   分高低权：admin = 守方（baseline），其余 = 攻方
        │
        ▼
   两两对比
     · 水平（IDOR）：攻方读受害者的资源，数据对得上 baseline ⇒ 越权
     · 垂直：低权账号够到了 admin-only 端点 ⇒ 越权
        │
        ▼
   铁律：没有 baseline 撑腰，一律只记 POTENTIAL，不算 EXPLOITED
```

- **角色分高低权**：admin 当守方（baseline），其余当攻方。
- **对比矩阵**：垂直 = 攻方对守方；水平 = 攻方之间两两互攻。
- **怎么判越权**：水平（IDOR）——攻方拿到和受害者 baseline 一样的数据，就是越权（跨用户私有数据，硬证据）；垂直——低权账号够到 admin-only 端点，就是越权。
- **铁律**：没有 baseline 撑腰，一律只记 POTENTIAL，不算 EXPLOITED——比单账号更严。
- **向后兼容**：只配一个角色 → 全走 POTENTIAL（= 现状）；不配 → 和今天一样。

### 4.3 与 TS 的差异

| 维度 | TS 版 | PY 版 |
|---|---|---|
| 部署形态 | 仅 cli + worker，无 web UI | web 平台 + 认证档案库/管理页/ProfilePicker 全新增 |
| 认证配置 | `Authentication.credentials` 单个（非 list） | `credentials[]` 多角色 + Fernet 加密 + 系统档案 seed |
| `success_condition` | 必填字段 | D1-D4 删除（死字段），判定并入 `login_flow` |
| 多身份 authz | 有设计（`2026-06-14-authz-multi-account-design.md`）从未落地，零 commit | core `Account` 模型 + `derive_privilege_tier` + 多身份登录循环 + authz-exploit 多 session 协议 |
| 独立验证入口 | 无 | `AuthValidationWorkflow` + 可观测性（events.ndjson + verify-log） |

> 效果对照见 [主文档 §6](./scan-effectiveness-gains-vs-ts.md)（B1-B5）；黑盒 vs TS 能力对比见 [叙事版 §7](./refactor-scan-optimization-vs-ts.md)。

---

## 5. 攻击链构造：把单点漏洞串成多步攻击路径

上面 §2-§3 产出的都是**单点漏洞卡**（这个 source→sink 有没有漏洞）。但真实攻击是多步的——攻击者先写入、再触发渲染、最后窃取数据。攻击链构造就是把单点卡串成「攻击者视角的多步故事」，跟组合漏洞（§3.1，召回层）、PoC（§6，产物层）是三个不同层次：

- **组合漏洞**（§3.1）= 召回层，把 write→存储→read→sink 这条链**召回**成一个 finding；
- **攻击链**（本节）= 展示层，把多个 finding **串**成多步攻击路径，进报告「攻击链」章节；
- **PoC**（§6）= 产物层，把可利用漏洞**变**成 curl/Burp 包。

攻击链本身也是双轨结构——GitNexus 确定性组装 + LLM 攻击链 agent，merge 后进 `attack_chains.json`：

```
   GitNexus 已判定的漏洞          LLM 攻击链 agent
   （单跳 source→sink）          （创意推断多步链）
            │                            │
            └─────────────┬──────────────┘
                          ▼
                   合并 → attack_chains.json
                          ▼
                   报告「攻击链」章节

   典型链：stored XSS =「写入 → 存储 → 渲染」三步；IDOR = 多个缺归属校验的端点串一条
```

两套来源：

- **GitNexus 组装**：拿已判定的单点漏洞跨端点串起来——stored XSS 把「写入」和「渲染」按存储表名连成三步链；IDOR 把多个缺归属校验的端点聚成一条。GitNexus 不可用就空，LLM 轨兜底。
- **框架/前端分析**：从 TS 移植的部分，靠框架推断端点 + 前端路由关联，产 XSS 四步链和 IDOR 链。

**双轨铁律照守**：组装只读两轨各自的产物做合并，不反向喂 LLM 轨 prompt（CLAUDE.md §1）。两步组装 + 渲染都非 fatal——增强报告，不阻塞扫描（attack chains 挂了单点漏洞卡照常出）。

> 文件：`packages/core/src/supernova_core/code_index/attack_chain_assembler.py`（GitNexus 组装）、`services/route_chain_builder.py`（框架/前端组装）、`services/report_assembler.py:48`（`render_attack_chains`）；wiring：`whitebox/pipeline/activities.py:1721`（`run_attack_chain_assembly_v2`）→ `workflows.py:497`。攻击链汇总口径修复见 [叙事版](./refactor-scan-optimization-vs-ts.md)。

---

## 6. PoC 产物化：externally_exploitable → curl + Burp raw

```
   双轨合并后的漏洞队列
   （只挑 externally_exploitable = 公网可达的）
            ▼
   PoC 生成（报告之后的后处理，不改判定）
     · 注入 / XSS / SSRF：模板直接出 curl + Burp 包
     · auth / authz：LLM 补一份结构化 PoC
            ▼
   产物：curl 命令 + Burp raw + 置信度标注
   （真实 token 用占位符，不落盘）
```

TS 的 PoC 散在 exploit 阶段的自由格式 markdown 里，没结构化 Burp 产物，`externally_exploitable` 下游也没人专门消费。PY 针对 ee==True 的漏洞纯后处理产 curl + Burp raw，从「markdown 抠 curl」变成「一键产出可复用包」。

效果验证：✅ 真机对历史 session 实测产出 11 个 curl PoC（XSS×2 / AUTHZ×4 / SSRF×1 + 变体）。

> 文件：`packages/core/src/supernova_core/services/poc_generator.py`（`PoCGenerator`、`to_curl`、`to_burp_raw`、`classify_confidence`）；wiring：`whitebox/pipeline/activities.py:1220`（`generate_poc_report`）→ `workflows.py:604`。叙事见 [叙事版 §5](./refactor-scan-optimization-vs-ts.md)（P1）。

---

## 7. 跨仓微服务关联：TS 单仓 → PY 多仓拓扑

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

## 8. cost 计费：双引擎统一自算，per-profile 定价

> 扫描效果视角：cost 计费不改变「扫出什么」，但它让每次扫描的代价可核算、可比较、可按 profile 定价——是扫描「跑得起、算得清」的支撑设计。

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

## 9. 设计不变量（铁律）汇总

PY 重构里直接影响扫描效果的架构不变量，改动前必读（CLAUDE.md §1-§2）：

1. **双轨独立性**（§2）：GitNexus 轨与 LLM 轨链来源不同、互不喂数据，只在合并器 verdict OR 交汇。不要把确定性层产物喂进 LLM 轨 prompt（`tests/prompts/test_static_dataflow_hints_decoupling.py` 锁定）。
2. **`externally_exploitable` 是可达性标签**（§2）：true=公网 / false=内部或跨服务，不能被 verdict 覆写。
3. **LLM 轨纯 LLM 自给自足**（§2/§3）：补召回只动 GitNexus 轨自己接 LLM，不破坏双轨独立性。
4. **双引擎可互换**（§1）：两引擎流程一致、prompt 不改。不要「切到 glm-anthropic 了事」丢 openai 引擎，也别让 openai 退化成单 agent 使两引擎行为分叉。openai 自维护工具是 SDK 哲学差异，不可消除，别当退化去「修」。
5. **`SUPERNOVA_LLM_TRACK_ENABLED=0` 语义收窄**（§2）：只关 inj/xss/ssrf 的 taint agent；pre-recon/recon/authz/auth 的 LLM 全保留（GitNexus 做不了）。
6. **`scan-config.yaml` 明文边界**（§4）：持久化层加密，但 per-scan `scan-config.yaml` 必须明文（core 合流点，`parse_config` 不解密）；明文债靠 0600 权限 + 用后即删缓解。
7. **多身份判定铁律**（§4）：EXPLOITED 必须有 baseline 对比佐证；无 baseline 一律 POTENTIAL。
8. **组合漏洞 token 边界**（§3.1）：只有字面量 token 能静态 join，动态/拼接 token 留给 LLM 轨，静态解析不了的不硬连——误连比漏报更糟。

---

## 10. 一句话总结

PY 重构的扫描效果核心是给 TS 的单轨纯 LLM 加一条确定性兜底轨并把它真正跑通：双轨 verdict OR（§2）保召回对账和鲁棒性，多层补召回（§3）让确定性轨能落地，组合漏洞二阶 join（§3.1）补 stored XSS / 二阶 SQLi 系统性漏报，双引擎抽象（§1）让 claude/openai 可互换，黑盒多角色账号验证（§4）把单账号越权从「枚举 ID 猜测」变成「baseline 对比硬证据」，攻击链构造（§5）把单点漏洞串成多步攻击路径，PoC 产物化（§6）把「markdown 抠 curl」变成「一键 curl + Burp raw」，跨仓关联（§7）打开 TS 单仓盲区，per-profile cost 计费（§8）消除双引擎不对称。所有设计受一组铁律约束（§9），最核心的是双轨独立性——确定性产物绝不喂 LLM 轨 prompt。

> 各设计点的安全效果与真机验证状态见 [主文档能力矩阵](./scan-effectiveness-gains-vs-ts.md)；踩坑排查过程见 [叙事版](./refactor-scan-optimization-vs-ts.md)；目录索引见 [`README.md`](./README.md)；完整设计架构（含仓库分层、authz 深度 agent 轨）见母文档 [`py-redesign-architecture.md`](./py-redesign-architecture.md)。
