# shannon-py 重构前期侦察分析（Pre-Recon Weakness Analysis）

> 对比原始 TypeScript Shannon（`/root/shannon`，分支 `feat/fork`）与 Python 重构版（`/root/shannon-py`，分支 `feat/fork-py`）的**当前（2026-06-17）状态**，聚焦重构项目的**弱势项**，为后续优化提供基线。
>
> **术语澄清**：标题中的「pre-recon」指**重构项目开工前的前期侦察分析**，并非 Shannon 流水线里的 `pre-recon` agent。
>
> **数据来源**：5 个并行审计 agent 的代码级核验（均带 `file:line` 证据），以 `docs/refactor-docs/2026-05-29-shannon-py-gap-analysis.md`（下称「05-29 报告」）为**待验证基线**——即不采信其结论，逐一回到代码核验其是否仍成立。
>
> **日期**：2026-06-17
>
> **核心结论**：05-29 报告**已大幅过时**。当时判定的 8 类「致命/高危差距」（SDK 占位、prompt 骨架化、安全机制 5%、审计 25%、扩展性 0% 等）**多数已修复，部分已超越 TS**。当前真实弱势项已收敛为：2 个 P0（测试套件收集硬失败 + 无 CI、resume 断点续扫形同虚设）、一组 P1（工程化基设全线缺失、扩展性架构层缺失、heartbeat 未接、blackbox 凭据不校验）、以及一批 P2（输入校验边角、whitebox 并发/重试/错误状态语义、报告翻译等功能跟进滞后）。重构项目在**测试覆盖与中文文档**上显著领先 TS。

---

## 目录

1. [分析范围与方法](#1-分析范围与方法)
2. [当前真实弱势项总览矩阵](#2-当前真实弱势项总览矩阵)
3. [P0 — 可信度归零与虚假承诺](#3-p0--可信度归零与虚假承诺)
4. [P1 — 工程化基设与扩展性架构](#4-p1--工程化基设与扩展性架构)
5. [P2 — 可靠性深度与输入校验](#5-p2--可靠性深度与输入校验)
6. [演进跟进滞后（TS 05-29 后的新增功能）](#6-演进跟进滞后ts-05-29-后的新增功能)
7. [shannon-py 的优势（平衡对比）](#7-shannon-py-的优势平衡对比)
8. [后续优化优先级建议（工作清单）](#8-后续优化优先级建议工作清单)
9. [附录：05-29 基线「已修复/已消除」项一览](#9-附录05-29-基线已修复已消除项一览)

---

## 1. 分析范围与方法

### 1.1 对比对象

| 项 | 原始 Shannon (TS) | 重构 Shannon-py |
|---|---|---|
| 语言/栈 | TypeScript / pnpm / turbo monorepo | Python 3.12 / uv workspace |
| 核心代码位置 | `apps/cli/src`、`apps/worker/src`（packages 实为空） | `packages/{core,whitebox,blackbox,combined}/src` + `apps/{cli,worker}` |
| 源码规模 | 16,862 行 / 99 文件（`.ts`） | 18,308 行 / 155 文件（`.py`） |
| 工作流引擎 | Temporal.io + `@temporal/sdk` | temporalio Python SDK（真实接入，非 mock） |
| 测试 | **0 个测试文件** | ~130 测试文件 / 22K+ 行 / **1,745 用例**（但见 P0-1） |
| 共享 git origin | `github.com:2606364644/shannon.git` | 同左 |

### 1.2 验证方法

- 5 个独立领域并行审计：① 引擎核心 ② 安全机制 ③ 工作流可靠性 ④ 配置验证与扩展性 ⑤ 工程化基设与演进跟进
- 每个弱势项均要求 `file:line` 证据（TS 侧 + PY 侧），不存在项以「全仓 grep 零命中」佐证
- 关键事实（pytest 收集行为）由主控亲自复核：`uv run pytest --collect-only` 实跑确认
- 凡 05-29 报告判定为差距、但当前代码已实现者，一律标「已修复」并移出弱势项清单（见附录）

### 1.3 基线可信度结论

05-29 报告的核心数字（SDK 集成 0%、安全机制 5%、Workflow 编排 40%、审计 25%、扩展性 0%）**全部已失效**。直接复用该报告做后续优化会严重误导优先级。本报告即为其替代。

---

## 2. 当前真实弱势项总览矩阵

| # | 领域 | 弱势项 | 严重度 | TS 是否有 | PY 当前状态 |
|---|---|---|---|---|---|
| W-01 | 工程化 | 测试套件**收集阶段硬失败** + **无任何 CI** | **P0** | TS 无测试但无此问题 | 1 个坏 import 中断整套（1745 用例无法跑） |
| W-02 | 工作流 | Resume 断点续扫**形同虚设**（守卫代码假性存在） | **P0** | ✅ 完整 | 守卫恒不触发，`-w` 从头重跑 |
| W-03 | 工程化 | 无 CI/CD、无 Dockerfile、无发布机制、无类型检查、ruff 未装 | **P1** | ✅ 全套 | 全线缺失 |
| W-04 | 扩展性 | **DI Container + Findings/Checkpoint Provider + ContainerConfig** 缺失 | **P1** | ✅ | 3 个 Provider 只移植 1 个，DI 完全缺 |
| W-05 | 工作流 | **heartbeat 心跳完全未接** | **P1** | ✅ 5 套 | workflow/activity 双侧零 `heartbeat` |
| W-06 | 安全 | **blackbox 从不校验凭据**（孤儿导入）+ code_path glob 验证缺失 | **P1** | ✅ | blackbox 有 import 无调用 |
| W-07 | 工作流 | whitebox vuln **并发无限制**（打满 rate limit） | P2 | ✅ 双层限流 | 仅 blackbox 有 Semaphore |
| W-08 | 工作流 | whitebox **未接 retry profile**（testing/subscription 失效） | P2 | ✅ | 硬编码 `maximum_attempts=3` |
| W-09 | 工作流 | 错误状态语义弱（failed **不上抛 Temporal**） | P2 | ✅ throw error | 仅置 `status="failed"` 后正常 return |
| W-10 | 引擎 | **recon-static 静态分析路径未接线 + 内容残缺**（164 vs 443 行） | P2 | ✅ 443 行 + 强制 override | prompt 存在但零引用，白盒走动态 recon |
| W-11 | 配置 | 配置校验边角缺失（rule-type 7 选 1、危险模式、重复/冲突、1MB、TOTP 格式） | P2 | ✅ 全套 | 仅 url_path + 部分 login_flow 长度 |
| W-12 | 安全 | spending cap **缺流式实时检测**（run 结束才判定） | P2 | ✅ 4 集成点 | 3 点但无流式拦截 |
| W-13 | 工作流 | combined 非单 workflow **原子编排**（进程级串联） | P2 | ✅ 单 workflow | 两个独立 workflow 串行 |
| W-14 | 工作流 | workflow completion 日志在 host 侧（replay 不一致风险） | P2 | ✅ 在 workflow 内 | worker.py 调用 |
| W-15 | 演进 | **报告翻译**（3 个 TS plan）完全未跟进 → 无中文报告 | P2 | ✅ | 零跟进 |
| W-16 | 演进 | authz 多账号未跟进 | P2 | ✅ 43KB plan | 零跟进 |
| W-17 | 演进 | 可配置并发 `SHANNON_CONCURRENCY` 缺 env 入口 | P2 | ✅ | 原语在，无 env 解析 |
| W-18 | 演进 | SDK 连通性/冒烟测试 + 工作流级 session 注册/resume-skip 未跟进 | P2 | ✅ | 零跟进 |

> 严重度图例：**P0** = 可信度归零/虚假功能承诺；**P1** = 生产可靠性与扩展性硬伤；**P2** = 可靠性深度/输入校验/功能跟进。

---

## 3. P0 — 可信度归零与虚假承诺

### W-01 测试套件收集阶段硬失败 + 无任何 CI（工程可信度为零）

这是当前**最危险的运营风险**，因为它直接侵蚀重构项目相对 TS 的最大优势（测试覆盖）。

- **PY 证据（实跑核实）**：`uv run pytest --collect-only` 输出
  ```
  ERROR collecting packages/whitebox/tests/test_worker_progress.py
  ImportError: cannot import name 'poll_workflow_progress' from 'shannon_whitebox.worker'
  !!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!
  1745 tests collected, 1 error
  ```
  根因：`packages/whitebox/tests/test_worker_progress.py:4` `from shannon_whitebox.worker import poll_workflow_progress`，但 `worker.py` 中**无此符号**（全仓 src grep 零命中）。单个坏 import 导致**整个套件在收集阶段中断**，1745 个用例一个都跑不起来。
- **CI 证据**：shannon-py **无 `.github/`、无 `.pre-commit-config.yaml`、无 Makefile/tox.ini**。没有任何自动化闸门能在合入前拦截此类回归。
- **TS 对比**：TS 端 `.github/workflows/` 有完整 release/beta/rollback 流水线（Docker 多架构 + cosign 签名 + npm 发布）。
- **影响**：6 月 17 日的代码「绿色可信度」为零——任何 PR 都无法证明未引入回归。配合 W-03，等于无门禁。
- **修复方向**：① 在 `worker.py` 补回 `poll_workflow_progress`（或修正测试导入）；② 用 `--continue-on-collection-errors` 作为临时兜底；③ 立刻建立最小 CI（见 W-03）。

### W-02 Resume 断点续扫形同虚设

- **PY 证据**：
  - 模型有 stub 字段 `models/base.py:14` `resume_from_workspace: str | None = None`
  - workflow 内有短路守卫 `if AgentName.X not in self._state.completed_agents:`（`whitebox/workflows.py:131,213,279`；`blackbox/workflows.py:155,205`）
  - **但 `completed_agents` 是 workflow 实例字段，每次新 workflow 从空开始**——全仓 grep `loadResumeState`/`load_resume`/`restore_git`/`restoreGitCheckpoint` 在 `pipeline/workflows.py` 中**零命中**，没有任何代码从磁盘加载已完成 agent。
  - `git_manager.create_checkpoint`（`core/git_manager.py:67`）只在 agent executor 内调用，**无对应 restore**；`audit/metrics_tracker.py:138` 的 `add_resume_attempt` 写了 session.json，workflow 也不读。
  - CLI 标注 `-w/--workspace`「supports resume」（`whitebox/cli/main.py:31`），与实现不符。
- **TS 证据**：`temporal/workflows.ts:278-322` 完整链路（`loadResumeState` → `restoreGitCheckpoint` → `computeExpectedAgents` 短路 → `recordResumeAttempt`）+ `runSequentialPhase`/`runVulnExploitPipeline` 的 `shouldSkip()` 闭包 + `saveCheckpoint`。
- **影响**：中断后无法续扫，昂贵的 pre-recon/recon 会从头重跑。守卫代码的「假性存在」尤其危险——它让审查者误以为 resume 已工作。
- **严重度说明**：归 P0 是因为这是**对外承诺的功能（CLI 标注）+ 假性守卫代码双重误导**，而非单纯的可靠性缺失。
- **修复方向**：实现 `load_resume_state` activity（读 workspace deliverables + session.json 重建 `completed_agents`）+ `restore_git_checkpoint` + 把 stub 字段接到 workflow 入口。

---

## 4. P1 — 工程化基设与扩展性架构

### W-03 工程化基设全线缺失

逐项对比（TS 有 / PY 无）：

| 维度 | shannon (TS) | shannon-py | 差距 |
|---|---|---|---|
| CI/CD | `.github/workflows/{release,release-beta,rollback,rollback-beta}.yml`（Docker 多架构 + cosign + npm + GitHub release，固定 action SHA） | **无 `.github/`** | 严重 |
| 容器化 | `Dockerfile`(5966B，多阶段、Chainguard Wolfi、非 root、Playwright/Chromium 预置) + `.dockerignore` + `entrypoint.sh` | **无 Dockerfile**；`docker-compose.yml` 仅起 Temporal | 严重 |
| 发布/版本 | `.releaserc.json`(semantic-release) + npm 发布；所有包 `version` 真实管理 | 无；所有包硬编码 `version="0.1.0"`，无 changelog | 严重 |
| 类型检查 | `tsc`（`turbo run check`） | **无 mypy/pyright/basedpyright** | 严重 |
| Lint | biome（formatter + linter，noExplicitAny/noUnusedImports） | `[tool.ruff]` 仅设 `line-length`/`target-version`，**未选规则**；**`ruff` 甚至不在 dev 依赖**（`uv run ruff check` → `Failed to spawn`） | 严重 |

- **影响**：重构版无法构建容器镜像、无法做静态分析/类型检查、无发布管线。TS 流水线强制执行的类型安全闸门在 PY 完全缺失。叠加 W-01，工程层面处于「裸奔」状态。
- **修复方向**：① 最小 CI（`uv sync && uv run pytest && uv run ruff check`，先装 ruff）；② 补 mypy/basedpyright；③ Dockerfile（基于 Playwright 官方 Python 镜像）；④ semantic-release 或 hatch 做版本。

### W-04 扩展性架构层缺失（DI Container / Provider 扩展点）

这是 05-29 报告「扩展性 0%」中**唯一仍基本成立**的部分：

- **DI Container**：全仓 grep `class Container`/`getOrCreateContainer`/`container_factory` **零命中**。TS `services/container.ts:57-168` 有完整 Container 类 + 工厂 + 生命周期。PY 用模块级函数 + 直接实例化替代。→ **无法在不改源码前提下替换服务实现**。
- **Provider 扩展点（3 缺 2）**：
  - ✅ `interfaces/report_output_provider.py:5-13` `ReportOutputProvider` 存在
  - ❌ `FindingsProvider`（注入外部 SAST/SARIF/Snyk 结果到 exploitation queue）——零命中
  - ❌ `CheckpointProvider`（企业级 resume：pre-agent skip guard + post-agent artifact 持久化，TS `interfaces/checkpoint-provider.ts:27-51` 含 `SkipDecision`/`CheckpointContext`）——零命中
- **ContainerConfig**：TS `types/config.ts:122-133`（deliverablesSubdir/auditDir/apiKey/promptDir/providerConfig）PY 无对应模型——**缺失直接导致 DI Container 无配置根**。
- **PipelineInput 字段**：PY `models/base.py:9-19` + `whitebox/pipeline/shared.py:7-17` ≈ 11 字段，TS `temporal/shared.ts:9-36` 约 27 字段。PY 缺 `configYAML`/`configData`（inline config）、`auditDir`/`promptDir` override、`sastSarifPath`（外部 findings 输入）、`checkpointsEnabled`、`providerConfig`、`whiteboxOnly`/`blackboxOnly` 等。缺的恰好是容器/provider/checkpoint 相关字段，与 DI/Provider 缺失互为因果。
- **影响**：决定「能否被企业消费者扩展」。当前 PY 是封闭实现，外部 SAST 结果无法注入、断点续跑策略无法定制、报告格式无法扩展。
- **修复方向**：先补 `ContainerConfig` + `FindingsProvider`/`CheckpointProvider` 接口（纯抽象，低成本），再按需引入轻量 DI。

### W-05 heartbeat 心跳完全未接

- **PY 证据**：全仓 `grep "heartbeat"`（排除 .venv/test）**零命中**。两个 workflow 的所有 `workflow.execute_activity(...)` 只传 `start_to_close_timeout` + `retry_policy`，从不传 `heartbeat_timeout`；activities 内也无 `activity.heartbeat()` 调用。
- **TS 证据**：`workflows.ts:95,102,118,134,150` 5 套 proxy 各设 heartbeatTimeout（60min/30min/2h/2min/10min）；`activities.ts` 用 `setInterval` 周期 `heartbeat()`，注释明确是为「SDK blocks event loop during Task tool calls」。
- **影响**：长跑 agent（2h start_to_close）若 event loop 被 SDK Task 调用阻塞，Temporal 无法感知存活，可能误判超时。是当前最实在的运行时可靠性差距。
- **修复方向**：长跑 activity 内加 `activity.heartbeat()` + workflow 侧设 `heartbeat_timeout`。

### W-06 blackbox 凭据不校验 + code_path glob 验证缺失

（注：05-29 报告称 preflight 安全「~5%」已**完全不成立**——SSRF 169.254/16 黑名单、DNS rebinding pinning、loopback 阻断、stealth 反检测、deny 规则、认证预校验均已在 PY 落地并接入双 pipeline，见附录。此处仅列残余两个具体缺口。）

- **blackbox 凭据不校验**：`packages/blackbox/.../activities.py:11` 导入了 `validate_credentials`，但 `run_blackbox_preflight` 内**从未调用**（孤儿导入）；blackbox workflow 也无 credential-check 步骤。而 whitebox 有 `run_credential_check`（`whitebox/.../activities.py:156-175`）。→ blackbox 可能以无效凭据运行数小时。
- **code_path glob 存在性验证缺失**：TS `preflight.ts:199-239` `validateCodePathsExist` 校验 avoid/focus glob 至少匹配 1 个文件；PY 全仓无等价物。→ 无效 focus/avoid 规则静默通过，scoping 行为歧义。
- **次要**：loopback 命中时 TS 给 `host.docker.internal` 友好提示（`preflight.ts:564-576`），PY 只抛裸错误（`security.py:77-83`），UX 略弱。

---

## 5. P2 — 可靠性深度与输入校验

### 5.1 工作流可靠性深度（W-07 ~ W-09, W-13, W-14）

| # | 项 | PY 现状（证据） | TS 对照 |
|---|---|---|---|
| W-07 | whitebox vuln 并发无限制 | `whitebox/workflows.py:297` 直接 `asyncio.gather(*vuln_tasks, ...)` 全并发；`config.py:49` 有 `max_concurrent_pipelines` 字段但 workflow 不读。blackbox 已有 `asyncio.Semaphore`（`blackbox/workflows.py:235`） | 双层限流（`runWithConcurrencyLimit` + worker `maxConcurrentActivityTaskExecutions:25`） |
| W-08 | whitebox 未接 retry profile | `models/retry.py:37-64` 定义了 `TESTING_RETRY`/`SUBSCRIPTION_RETRY`/`get_retry_policy(mode)`，blackbox 已接（`blackbox/workflows.py:64-66`），但 whitebox 仅硬编码 `RetryPolicy(maximum_attempts=3)`（`:286-292`） | 按 mode 切 proxy |
| W-09 | 错误状态不上抛 Temporal | 失败时 `raise ApplicationFailure(...)`（activity 层 OK），但 workflow 用 `gather(..., return_exceptions=True)` 收集后仅置 `self._state.status="failed"` 并**正常 return**（`whitebox/workflows.py:297-366`；`blackbox/workflows.py:317-322`）。Temporal 端 workflow 终态是 **completed**，监控无法区分真完成/部分失败 | failed 分支 `throw error` 让 Temporal 标 failed |
| W-13 | combined 非单 workflow | `packages/combined/orchestrator.py:19-77` 顺序跑两个独立 Temporal workflow（各自 start_workflow + worker），非单 workflow 原子编排 | `pentestPipeline` 单 workflow 内编排 5 对 pipelined |
| W-14 | workflow completion 日志位置 | `log_workflow_complete` 在 host worker 端调用（`blackbox/worker.py:101,110`；`whitebox/worker.py:141`），workflow 主体内不调用 → Temporal replay 时不重放，audit 可能漏 | `logWorkflowComplete` 在 workflow 三分支内调用 |

> 注：W-07/W-08 的 blackbox 侧已修，问题仅集中在 whitebox；W-09 是「分类做到了，但终态语义偏弱」。这几项都是**深度差距**而非功能缺失，故 P2。

### W-10 recon-static 静态分析路径未接线 + 内容残缺

引擎核心领域当前**唯一的真实功能缺口**（SDK 集成、exploit/report prompt、spending cap 等均已修复，见附录）。

- **未接线**：`promptOverride` 机制已移植（`agents/executor.py:36,56`、`pipeline/shared.py:17,44`），但白盒 recon 实际走 `AgentName.RECON → prompt_template="recon"`（`agents.py:48`），**未做** TS 的强制 `promptOverride: 'recon-static'`（TS `local/runner.ts:189`、`workflows.ts:753`）。`prompts/recon-static.txt` 在 `packages/` 全代码中**零引用**（仅 docs/plan 提及），是孤儿 prompt。
- **内容残缺**：PY `prompts/recon-static.txt` = 164 行，TS `recon-static.txt` = 443 行（PY 仅 37%）。即便接线也不达标。
- **影响**：白盒场景（无目标 URL）本应仅做静态分析，PY 却用 500 行动态 `recon.txt`（可能触发浏览器探测）。docs plan `2026-06-04-plan-c-tiered-per-chain-audit.md` 标注「Expand to 9-chapter structure」为已完成，与实际 164 行矛盾——又一个「标完成未落地」。

### W-11 配置校验边角缺失

05-29 报告称 Config「~70%」；当前约 80%，残余集中在输入校验深度（TS 用 241 行 JSON Schema + 运行期检查，PY 用 pydantic 但约束偏松）：

| 校验类别 | TS | PY 现状 |
|---|---|---|
| 危险模式检查 | 覆盖 `rules[*].value`/`rules[*].description`/`report.guidance`（`config-parser.ts:488-525`） | 仅 `description`/`rules_of_engagement`/`login_url`/`credentials.username`/`login_flow[*]`（`config/parser.py:39-59`），**漏 `rules.value`/`rules.description`/`report.guidance`** 三个用户输入面 |
| rule-type 细分 | 7 种全覆盖（`config-parser.ts:532-622`） | **仅 url_path**（`parser.py:61-68`）；code_path/subdomain/domain/method/header/parameter 全不校验 |
| 重复/冲突/废弃 | `checkForDuplicates`/`checkForConflicts`/`checkDeprecatedFields` | 全缺 |
| 长度/大小/格式 | 1MB 文件上限 + 10×maxLength + 4×pattern + URI/email format + TOTP `^[A-Za-z2-7]+=*$` | 仅 login_flow step 限 500 字符；无 1MB 上限（`parse_config:186` 直接 read_text）；无 `Field(max_length/pattern=)`；TOTP 仅运行期校验 |

- **影响**：错误/恶意 rule value（如 `code_path: http://evil`、`method: TRACE`、超大 YAML）不会被拒绝；废弃字段静默失效。
- **修复方向**：补齐 6 种 rule-type 校验 + 重复/冲突检测 + pydantic `Field` 约束 + 1MB 上限。

### W-12 spending cap 缺流式实时检测

05-29 报告称 Spending Cap「~25%」；当前已实现双层防御（`utils/billing.py` 19 pattern + `message_dispatcher.py` message-level + `providers_anthropic.py` L1/L2），**已追平甚至反超 TS**（TS 16 pattern）。残余缺口：

- PY 缺 TS 的**流式 message-handler 级实时检测**（TS `message-handlers.ts:76` 在 streaming content 里就 sniff billing text）。PY 只在 agent run 结束后整块判定 → 多浪费一个完整 agent run 的 token。
- 黑盒同样不校验凭据（W-06），与 spending cap 叠加放大浪费。

---

## 6. 演进跟进滞后（TS 05-29 后的新增功能）

TS 在 `docs/superpowers/plans/` 有多个 2026-06 plan，代表原始项目 05-29 后的演进。重构版跟进情况：

| TS 功能（plan 日期） | shannon 状态 | shannon-py 跟进 | 证据 | 严重度 |
|---|---|---|---|---|
| **报告翻译**（06-09）+ 本地 runner 翻译（06-11）+ 黑盒翻译输出路径（06-16） | ✅ `report-translation-provider`、`translate-deliverables`、`deliverables-cn` overlay | ❌ **未跟进** | `packages/*/src` grep `translat\|翻译\|deliverables-cn` 零命中；无 `ReportTranslationProvider` | **P2**（面向用户的功能，无中文报告） |
| **authz 多账号**（06-14） | ✅ 43KB plan | ❌ **未跟进** | grep `multi[_-]?account` 零命中；authz 命中全是漏洞类别字面量 | P2 |
| **可配置并发** `SHANNON_CONCURRENCY`（06-16） | ✅ `resolveConcurrencyFromEnv()`（CLI>env>5） | ❌ **未跟进**（原语在，缺 env 入口） | grep `SHANNON_CONCURRENCY` 零命中；whitebox 硬编码、blackbox 用 `max_concurrent` 信号 | P2 |
| SDK 连通性/冒烟测试（06-11/12） | ✅ `test-sdk-connectivity.ts` | ❌ 未跟进 | 无连通性/冒烟脚本 | P2 |
| 黑盒 session 注册（06-02）/ resume-skip（06-04） | ✅ workflow 重排 + `originalWorkflowId` 持久化 + `shouldSkip` | ❌ 未跟进（架构不同） | PY `session_registry.py` 仅进程内单例（live-display 用），非工作流级注册 | P2 |
| 跨路由枚举（06-04） | ✅ prompt 片段 | ✅ **已跟进** | `prompts/shared/_cross-route-enumeration.txt` + `route_chain_builder.py` | — |
| 漏报修复 / 路由 auth 精度（06-05） | ✅ framework/frontend/route-chain/attack-chain 分析器 | ✅ **已跟进**（基础设施） | 4 个 service 全迁移到 `core/services/` + 测试 | — |
| recon prompt 修复（06-02）/ clean-blackbox（06-08） | ✅ | ✅（清理）/ ⚠️ 部分（recon-static，见 W-10） | — |

**小结**：重构版跟进的是**早期结构性工作**（06-04/05 的分析器基础设施，已完整迁移到 `core/services/`），但 **06-09 之后面向用户的功能（报告翻译、多账号、可配置并发、SDK 连通性）几乎零跟进**。其中报告翻译影响最大（TS 已能产出中文报告，PY 不能）。

---

## 7. shannon-py 的优势（平衡对比）

为避免「只看弱势」的偏颇，如实记录重构版的超越之处——这些是后续优化时**应当保留**的资产：

1. **测试体系**：~130 测试文件 / 22K+ 行 / 1,745 用例，覆盖 whitebox/blackbox/combined/core 服务/code_index/agent providers/display/集成。TS 为 **0**。这是最大优势——**前提是修复 W-01**。
2. **中文文档体系**：`docs/` 约 123K 行结构化文档（架构、API、prompt 工程、gap 分析、重构评估、superpowers plan/spec），含设计原理与差距分析。TS 约 1.4K 行英文文档。
3. **Provider 抽象更完善**：`BaseProvider`(220 行 ABC + 6 类错误分层) + `providers_openai.py`(262 行) + `providers_anthropic.py`(489 行)，原生支持 5 种 provider（anthropic_api/bedrock/vertex/openai_compatible/litellm_router）+ 三档模型。TS 仅单一 Claude executor。
4. **spending cap 多层检测**：L1 ResultMessage 文本 + L2 行为启发（低 turns + 零 cost + 无输出）+ 错误串匹配，并触发 git rollback（`executor.py:79-86`）。TS 无等价的多层机制。
5. **Playwright 会话物理隔离**：每个 session 独立 `storageState` 文件路径 + 独立 config 文件（`playwright_engine.py:135-179`）；stealth 双保险（`delete` + `Object.defineProperty`）。TS 只切 session id 共享存储。
6. **settings.json 合并语义**：写入前读后合并，不覆盖用户已有 deny 规则（`settings_writer.py:47-53`）；TS 直接覆盖。
7. **白盒 Code Index 静态分析层**：deterministic code index + PRE_RECON 并行（`whitebox/workflows.py:126-159`）+ entry-point 置信度裁定 + framework/frontend mapper，是 PY 重构新增的、TS 原版没有的静态数据流分析能力。
8. **更规范的类型化**：pydantic `Literal` 枚举编译进类型系统（违例在 `model_validate` 即报错）+ 前后双层 sanitize（防绕过）+ 结构化 audit 模型（`AgentEndResult`/`WorkflowSummary`/`PhaseMetrics`）。
9. **统一的 graceful-shutdown runtime 抽象**（`core/runtime/scan_runner.py`）：SIGINT 双击 + SIGTERM 协作取消 + `handle.cancel()` + grace period 封成可复用组件，whitebox/blackbox 共享。TS 分散在各 workflow 闭包。

---

## 8. 后续优化优先级建议（工作清单）

### P0 — 立即（恢复可信度）

| # | 任务 | 工作量 | 说明 |
|---|---|---|---|
| 1 | 修复 `test_worker_progress.py` 收集错误 | XS | 补回 `worker.py::poll_workflow_progress` 或修正测试导入；解锁 1745 用例 |
| 2 | 建立最小 CI（GitHub Actions） | S | `uv sync && uv run ruff check && uv run pytest`；装 ruff 进 dev 依赖；先把 W-03 的 lint/CI 闸门立起来 |
| 3 | 实现 resume 真正加载（`load_resume_state` + `restore_git_checkpoint` + 接 stub 字段） | M | 消除 W-02 的虚假承诺；移除或兑现 CLI `-w` 标注 |

### P1 — 短期（生产可靠性与扩展性）

| # | 任务 | 工作量 |
|---|---|---|
| 4 | Dockerfile + Docker Compose 应用服务 | M |
| 5 | 补 mypy/basedpyright 类型检查 + ruff 规则集 | S |
| 6 | long-running activity 加 `heartbeat()` + `heartbeat_timeout`（W-05） | S |
| 7 | blackbox 接 `run_credential_check` + code_path glob 验证（W-06） | S |
| 8 | 补 `ContainerConfig` + `FindingsProvider`/`CheckpointProvider` 接口（W-04，先抽象后实现） | M |

### P2 — 中期（深度与功能跟进）

| # | 任务 | 工作量 |
|---|---|---|
| 9 | whitebox vuln 并发限流（接 `max_concurrent_pipelines`，W-07） | S |
| 10 | whitebox 接 retry profile（W-08） | S |
| 11 | 错误状态上抛 Temporal（W-09） + workflow completion 日志入 workflow（W-14） | S |
| 12 | 补齐 config 校验（rule-type/危险模式/重复冲突/1MB/TOTP，W-11） | M |
| 13 | recon-static 接线 + 补内容至 TS 水平（W-10） | M |
| 14 | 报告翻译移植（W-15，3 个 TS plan 合并实现） | M |
| 15 | spending cap 流式检测（W-12） | S |
| 16 | `SHANNON_CONCURRENCY` env 入口（W-17）/ authz 多账号（W-16）按需 | M |

---

## 9. 附录：05-29 基线「已修复/已消除」项一览

以下 05-29 报告判定为「致命/高危差距」的项，经代码核验**当前已不成立**（列此以免后续优化误触，并记录重构进度）：

| 05-29 判定 | 当时状态 | 当前（06-17）状态 | 证据 |
|---|---|---|---|
| Claude Agent SDK 仅占位符（P0） | 28 行 `NotImplementedError` | **已修复（超越）** | `providers_anthropic.py`(489 行) 真实 `claude_agent_sdk.query` 流式 + `message_dispatcher.py` 分发 + 成本/usage/结构化输出/模型分级/错误处理 |
| 5 个 exploit prompt 19 行骨架（P0） | 96% 缩减 | **已修复** | injection 450 / xss 456 / ssrf 516 / authz 427（持平或超 TS），仅 auth 351 vs 423 略短 |
| report prompt 22 行（P0） | 81% 缩减 | **已修复** | `report-executive.txt` = 112 行（≈ TS 113），语义更明确（增量清理） |
| misconfig 漏洞类缺失（P1） | 完全缺失 | **情况变化（有意移除）** | `docs/superpowers/specs/2026-06-06-remove-misconfig-design.md`(Approved) 记录为业务决策，附完整移除清单；非重构欠账 |
| 认证预校验缺失（P1） | 无 | **已修复（更解耦）** | `services/validate_authentication.py`(155 行) + 两条 pipeline 均接入 |
| Playwright 反检测/会话隔离缺失（P1） | 无 | **已修复（更强）** | `playwright_engine.py` stealth 双保险 + per-session storageState 物理隔离 |
| code_path deny 规则缺失（P1） | 无 | **已修复（语义更优）** | `settings_writer.py` 合并而非覆盖 |
| Preflight 安全检查 5%（P1） | 仅查 repo 路径 | **部分修复** | SSRF/loopback/DNS-rebinding 已落地（`utils/security.py` 131 行）；残余见 W-06 |
| 审计系统 25%（P3） | 仅 success bool | **已修复（追平）** | `models/audit.py` `AgentEndResult` 全字段 + `metrics_tracker` 持久化 + 阶段聚合 |
| FindingsRenderer 缺失（P3） | 无 | **已修复** | `services/findings_renderer.py` 接入 pipeline |
| Spending Cap 25%（P3） | 仅 executor 1 点 | **已修复（反超）** | 19 pattern + 双层检测，仅余流式缺口（W-12） |
| non-retryable errors 缺失（P2） | 无 | **已修复** | `models/retry.py` + `NON_RETRYABLE_TYPES`(9 类，含 GitError) |
| 实时进度 query 缺失（P2） | 无 | **已修复** | `@workflow.query(name="PipelineProgress")` + `scan_runner.poll_progress` |
| exploit 依赖链错（P2） | exploit→recon | **已修复** | blackbox 每 vuln 类型独立 pipeline（exploit→对应 vuln） |
| exploit queue 门控缺失（P2） | 无 | **已修复（更细）** | `ExploitationChecker.validate_queue` 区分 expected/anomalous |
| EmailLogin/ProviderConfig 缺失（P3） | 无 | **已修复** | `models/config.py:29` / `agents/runner.py:25` |

> **持续有效（未修复）的 05-29 项**：Resume（→W-02）、heartbeat（→W-05）、DI Container / Findings·Checkpoint Provider（→W-04）、pentestPipeline 单 workflow（→W-13）、部分 config 校验（→W-11）。

---

*本报告基于 2026-06-17 两代码库（`/root/shannon` @ `feat/fork`、`/root/shannon-py` @ `feat/fork-py`）的代码级核验生成，由 5 个并行审计 agent + 主控复核（pytest 收集、worker.py 导入）共同产出。建议后续每次大改动后按本结构做「v2 复核更新」，与 `docs/gap/entry-point-gap-analysis.md` 的维护惯例一致。*
