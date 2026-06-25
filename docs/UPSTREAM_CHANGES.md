# 上游官方更新同步报告

> 本文档对比 fork 分支 `feat/fork` 与官方上游 `upstream/main`（KeygraphHQ/shannon）的差异，
> 记录 fork 之后官方引入的所有变更，用于评估同步/合并策略。

## 一、同步基线

| 项目 | 值 |
| --- | --- |
| 本地分支 | `feat/fork` |
| 官方上游 | `upstream/main` (https://github.com/KeygraphHQ/shannon) |
| 分叉点 (merge-base) | `ab2c400d` — Merge PR #202 |
| 分叉日期 | 2026-03-04 |
| 上游最新提交 | `5a2f78c5` — 2026-06-23 |
| 本地领先提交数 | 227 |
| 上游新增提交数 | 75（其中非合并 60） |
| 新增版本标签 | v1.6.0、v1.7.0、v1.8.0、v1.8.1 |

## 二、官方变更分类总览

按主题归类，标注 PR 编号与影响范围。

### 1. AI / 模型升级

| PR | 内容 | 说明 |
| --- | --- | --- |
| #325 | 升级到 Opus 4.7 | 默认启用 adaptive thinking |
| #353 | 升级到 Opus 4.8 | Claude Agent SDK 升至 0.3.163 |
| #354 | 支持 Claude Fable 5 | Claude Agent SDK 升至 0.3.173 |
| #329 | 分析模式 notes 字段引导 | analysis-only 模式下的提示词 |

模型升级集中在 `apps/worker/src/ai/models.ts` 及 SDK 依赖版本。本地若改过模型配置需重点比对。

### 2. 认证与预检（auth / preflight）

| PR | 内容 | 说明 |
| --- | --- | --- |
| #335 | auth-validation 预检 + email_login 凭证类型 | 新增 `validate-authentication` 活动与 prompt |
| #345 | preflight 认证 session 跨 agent 共享 | 新增 `shared/_shared-session.txt`，会话复用 |
| #274 | pre-recon deliverable 文件名不匹配修复 | — |
| #337 | URL 校验拦截云元数据 IP 段 | 安全加固 |
| #371 | 支持多 repo 目标 | 移除 `.git` 检查 |

注：#335 / #345 与本地 `feat/auth-validation-preflight` 分支高度重叠，是合并冲突的高风险区。
#335 影响 17 文件 (+567/-35)，#345 影响 19 文件 (+148/-8)，核心改动落在
`apps/worker/src/services/validate-authentication.ts`、`prompt-manager.ts`、
`temporal/activities.ts` 与各 agent prompt。

### 3. 架构与 SDK 集成（重点）

| PR | 内容 | 说明 |
| --- | --- | --- |
| #256 | npx CLI + monorepo 重构 | 与本地 `feat/npx-integration` 重叠最大 |
| #350 | MCP collectors 结构化中间 deliverables | 新增 `mcp-server/` 与多个 renderer 服务 |
| #267 | vuln agent exploitation queue 改用 structured outputs | — |
| #282 | 抽取 pipeline 核心为可复用库 | 库化拆分 |
| #295 | provider 扩展，移除 claude-code-router 模式 | — |
| #246 | 自定义 Anthropic 兼容 endpoint base URL | — |

其中 **#350 是体量最大的功能性改动**：26 文件，+4151/-1312。新增目录
`apps/worker/src/mcp-server/`（pre-recon / recon / vuln / exploit 四个 collector），
以及对应的 `services/*-renderer.ts`，并重写了全部 agent prompt 以适配 MCP collector 模式。
这意味着几乎所有 `apps/worker/prompts/*.txt` 与 `session-manager.ts`、`temporal/activities.ts`
都被改写——任何在本地改过 prompt 的分支都会与之冲突。

### 4. Docker / 运行时

| PR | 内容 | 说明 |
| --- | --- | --- |
| #273 | 用户 repo 只读挂载 + 可写 shannon overlay | — |
| #346 | 转发 /etc/hosts 到 worker 容器 | 与本地 forwardEtcHostsFlags 重叠 |
| #338 | 全局 npm install 固定 `--ignore-scripts` | 安全加固 |
| 023cc953 | 加固 Docker 隔离和子进程环境 | 安全加固 |

### 5. CLI / 用户体验

| PR | 内容 |
| --- | --- |
| #323 | 阻止以 sudo/root 运行 shannon |
| #299 | docker 错误透传 + `--debug` worker 日志标志 |
| #328 | save-deliverable / generate-totp 加 `--help` |

### 6. 配置体系（run scoping）

| PR | 内容 | 说明 |
| --- | --- | --- |
| #326 | config 驱动的 run scoping + report 过滤 | 支持 `vuln_classes`、`exploit`、`min_severity` 等 |

#326 影响 32 文件 (+1162/-181)，触及 `config-parser.ts`、`preflight.ts`、
`prompt-manager.ts`、`reporting.ts`、`temporal/*`、`ai/settings-writer.ts`（写入
code_path deny 规则）等。本地若调整过配置体系需重点比对。

### 7. 安全 / 依赖升级

| PR | 内容 |
| --- | --- |
| #314 | protobufjs 升至 7.5.5（修 CVE-2026-41242） |
| #344 | fast-uri 升至 3.1.2（修 CVE-2026-6321） |
| #266 | pnpm 最低发布年龄策略，升级至 v10.33.0 |

### 8. CI/CD

| PR | 内容 |
| --- | --- |
| #247 | beta 发布与回滚工作流 + cosign 签名 |
| #356 | beta 发布线升至 2.0.0 |

### 9. 文档与品牌

大批 README 更新与 Keygraph/Shannon 命名统一（#302、#359、#375、#371 相关 docs 等），
另有若干纯资源文件（图片）增删，不涉及代码逻辑。

## 三、与本地 fork 的重叠分析

下表列出官方改动方向与本地分支（origin）的对应关系，并标注冲突风险等级。

| 主题 | 官方 PR | 本地对应分支 | 冲突风险 |
| --- | --- | --- | --- |
| auth-validation 预检 | #335, #345 | `feat/auth-validation-preflight` | 高 |
| npx 集成 / monorepo | #256, #295 | `feat/npx-integration` | 高 |
| MCP collectors 重写 prompt | #350 | （本地大量 prompt 定制） | 极高 |
| config run scoping | #326 | （本地配置体系改动） | 中-高 |
| /etc/hosts 转发 | #346 | 本地 forwardEtcHostsFlags | 中 |
| telemetry | （upstream 分支） | `feat/telemetry` | 视实现而定 |

**最高风险点是 #350（MCP collectors）。** 它重写了全部 `apps/worker/prompts/*.txt`
并新增了 `mcp-server/` 与 `services/*-renderer.ts`，而本地 227 个提交中包含大量
prompt 定制与渲染逻辑改动。两者在 prompt 目录上几乎必然全量冲突。

## 四、同步建议

1. **优先级排序**：先吸收低冲突的纯增量（模型升级 #325/#353/#354、依赖安全修复
   #314/#344/#338、CI #247/#356），这些多为独立文件，cherry-pick 成本低。

2. **关键模块逐个评估**：#350、#256、#326、#335/#345 四组是结构性改动，建议逐组
   `git diff` 本地对应文件，判断是“采用官方实现”还是“保留本地实现”。

3. **prompt 目录策略**：鉴于 #350 重写了全部 prompt，若本地 prompt 定制价值高，
   建议以本地 prompt 为基线，手工把 #350 的 MCP collector 调用方式移植进来，
   而非直接合并官方 prompt。

4. **合并前回归测试**：同步后务必用 `--pipeline-testing` 跑一遍最小流水线，确认
   preflight / vuln / exploit / report 各阶段 deliverable 正常生成。

5. **保留可回滚点**：大改动同步前打 tag（如 `pre-upstream-sync-2026-06`），
   便于出问题时快速回退。

## 五、复核命令

```bash
# 查看分叉点
git merge-base feat/fork upstream/main

# 列出上游新增提交
git log --oneline $(git merge-base feat/fork upstream/main)..upstream/main

# 对比某个 PR 的文件改动（以 #350 为例）
git show --stat 0a1a2eb1

# 本地与上游某目录的差异
git diff feat/fork upstream/main -- apps/worker/prompts/
```
## 六、重点 PR 深度解读

本节针对关注度最高的几个 PR，结合实际代码 diff 做逐项说明。

### A. #325 默认启用 Adaptive Thinking（adaptive thinking）

**核心改动在 `apps/worker/src/ai/models.ts`**，分两块：

1. 默认模型从 `claude-opus-4-6` 升级到 `claude-opus-4-7`：
   ```ts
   const DEFAULT_MODELS = {
     small: 'claude-haiku-4-5-20251001',
     medium: 'claude-sonnet-4-6',
     large: 'claude-opus-4-7',   // 原来是 claude-opus-4-6
   };
   ```

2. 新增能力探测函数 `supportsAdaptiveThinking`：
   ```ts
   /** Whether a model supports adaptive thinking. Opus 4.6 and 4.7 only. */
   export function supportsAdaptiveThinking(model: string): boolean {
     return /opus-4-[67]/.test(model);
   }
   ```

**"Adaptive Thinking" 是什么**：Claude Opus 4.6/4.7 支持的扩展思考（extended thinking）
能力。Shannon 在 `claude-executor.ts` 和 `message-handlers.ts` 里依据这个函数决定是否给
SDK 传入 thinking 相关参数，让模型在复杂推理（漏洞判定、攻击路径推导）前先"想一想"再产出结论。
之所以限定正则 `/opus-4-[67]/`，是因为只有这两个模型支持该特性；Haiku/Sonnet 不支持。

**配套改动**：`setup.ts`（交互向导）、`resolver.ts`/`writer.ts`（TOML 配置）、`env.ts`、
`claude-executor.ts`、`message-handlers.ts`、`types.ts` 都跟着调整，并新增 `CLAUDE_ADAPTIVE_THINKING=false`
环境变量开关和 `core.adaptive_thinking` TOML 选项来逐次扫描关闭该特性。后续 #353（Opus 4.8）
和 #354（Fable 5）持续在此基础上升级 SDK 版本。

**本地影响**：如果你的 fork 改过 `models.ts` 的默认模型或 executor 的 thinking 参数，
需要手动合并这个探测函数；否则只是默认模型 ID 字符串变化，低冲突。

---

### B. #256 npx CLI + monorepo 重构（巨型 PR）

这是 fork 后上游最大的结构性重构，影响 4058 文件（大部分是删除旧的示例 deliverable）。
它把原本单仓库的 Shannon 改造成了可 `npx` 分发的 monorepo。提交分多个子任务，核心如下：

**1. 引入 npx 分发体系**
- 新增 `cli/`（后 `apps/cli/`）独立包，发布到 npm 为 `@keygraph/shannon`
- 零安装：用户 `npx @keygraph/shannon start` 直接拉取 Docker Hub 镜像运行
- TOML 配置（`~/.shannon/config.toml`）+ 交互式 `setup` 向导（`@clack/prompts`）
- 状态目录从项目内 `./` 迁移到 `~/.shannon/`（npx 模式）

**2. 临时 worker 容器架构**
- 基础设施（Temporal）走 `docker-compose.yml`，worker 改为每次扫描一个 `docker run --rm` 临时容器
- 每个扫描有独立的 task queue，保证活动不会路由到挂载错误 repo 的 worker
- `apps/cli/src/docker.ts` 负责镜像拉取/构建、ephemeral worker 生成、`--add-host` 转发

**3. 迁移到 Turborepo + pnpm + Biome monorepo**
- 目录重构：`src/` → `apps/worker/src/`，`cli/` → `apps/cli/`，`mcp-server/` → `packages/mcp-server/`
- `prompts/` 和 `configs/` 移入 `apps/worker/`
- `npm` → `pnpm`（`package-lock.json` → `pnpm-lock.yaml`），Dockerfile 同步改造
- 路径解析集中到 `apps/worker/src/paths.ts`

**4. CI/CD + 安全加固**
- semantic-release 语义化发布、beta 发布与回滚工作流 + cosign 签名
- GitHub Actions 固定到 commit SHA 防供应链攻击
- Docker 多平台构建改用原生 ARM64 runner（替代 QEMU 模拟）
- Linux bind mount 权限修复（entrypoint UID 重映射）、Windows 兼容（POSIX sleep/权限检查）

**5. MCP → CLI 工具迁移**（子任务 #252）
- 浏览器自动化从 MCP 工具改回 `playwright-cli`，`formatBrowserAction` 适配 CLI 命令名
- Vertex AI 凭证挂载到固定容器路径

**与你的重叠**：这是与你 `feat/npx-integration` 分支方向最重合的 PR。若要同步，几乎是
对整个 monorepo 骨架做三方合并，建议评估后整体采用官方结构。

---

### C. #350 MCP Collectors 结构化中间交付物（重点功能 PR）

**要解决的问题**：此前每个 agent（pre-recon/recon/vuln/exploit）直接把分析结果写成一个
自由格式的 Markdown 文件。格式不稳定、难以被下游 agent 解析、resume 时无法校验完整性。
#350 用 **MCP tool + Zod schema + 确定性渲染** 取代了自由写作。

**核心机制（以 pre-recon 为例）**：

1. **Collector MCP Server**（`apps/worker/src/mcp-server/pre-recon-collector.ts`）
   为 deliverable 的每个章节定义一个 MCP tool，每个 tool 用 Zod schema 严格约束入参。
   schema 的 `describe()` 字段携带写作指引，SDK 会把它注入到 agent 的工具目录里，
   于是 agent 调用 tool 的过程就是在"填结构化表单"。
   - 一次性（write-once）：重复调用返回 `DuplicateError`
   - 跳过的 tool 在渲染阶段输出占位符，而非导致活动失败

2. **Activities 层接线**（`temporal/activities.ts`）
   每个 agent 活动从一行 `runAgentActivity('pre-recon', input)` 扩展为：
   ```ts
   const collector = createPreReconCollectorServer();
   const metrics = await runAgentActivity('pre-recon', input, {
     'pre-recon-collector': collector.server,   // 把 collector 作为 MCP server 注入
   });
   if (metrics.skipped) return metrics;          // resume 时跳过
   const markdown = renderPreRecon(collector.getAll());  // 确定性渲染
   await atomicWrite(mdPath, markdown);
   ```
   vuln 和 exploit 阶段抽出了 `runVulnAgentWithCollector` / `runExploitAgentWithCollector`
   复用函数。exploit collector 还会先读 `*_exploitation_queue.json` 校验合法漏洞 ID。

3. **确定性渲染器**（`services/*-renderer.ts`）
   `pre-recon-renderer.ts`、`recon-renderer.ts`、`vuln-renderer.ts`、`exploit-renderer.ts`
   把 collector 收集的结构化 payload 转成 Markdown，无 LLM 参与。

**新增目录/文件**：`apps/worker/src/mcp-server/`（4 个 collector）、
`apps/worker/src/services/*-renderer.ts`（4 个渲染器）、`types/deliverables.ts` 瘦身、
`session-manager.ts`（collector 注册）、全部 `prompts/*.txt` 改写以引导 agent 调用 tool。

**带来的好处**：deliverable 格式可预测、下游 agent 可靠解析、resume 可校验完整性、
渲染与模型解耦（换模型不影响输出格式）。

**与你的重叠**：这是与你本地 prompt 定制冲突最严重的 PR。官方重写了全部 prompt，
你的 227 个提交里若有 prompt 调优，几乎必然逐文件冲突。建议保留本地 prompt，
把官方的 collector 调用约定（schema + tool 名）手工移植到你的 prompt 中。

---

### D. #282 抽取 pipeline 核心为可复用库（library consumption）

**目标**：让 Shannon 的渗透流水线可以作为库函数被外部程序嵌入调用，而不必只能通过 CLI+Docker 跑。
为此把核心逻辑与"路径约定、凭证来源、状态持久化"解耦，引入依赖注入。

**1. 库入口 `apps/worker/src/temporal/pipeline.ts`**
   ```ts
   export { pentestPipeline } from './workflows.js';
   export type { PipelineInput, PipelineState, PipelineSummary, ... } from './shared.js';
   ```
   消费者 `import { pentestPipeline } from '@shannon/worker/pipeline'` 即可调用。

**2. 可注入接口 `apps/worker/src/interfaces/`**
- `CheckpointProvider` — 每个 agent 完成后回调，用于外部进度追踪（默认 no-op）
- `FindingsProvider` — 把外部安全数据（SAST/SCA/密钥）合并进 exploitation queue
- 每个接口都带 `NoOp*` 默认实现，OSS 用法零改动

**3. 多 provider LLM 支持 `ProviderConfig`**（`types/config.ts`）
   抽象 Bedrock/Vertex/自定义 base URL 等，executor 据此映射 SDK 环境变量，
   不再硬编码走 `process.env.ANTHROPIC_API_KEY`。

**4. `ContainerConfig` 运行时配置**
   消费者可覆盖 deliverables 子目录、audit 目录、prompt 目录、API key 来源、provider 配置，
   而不改源码。DI 容器（`services/container.ts`）据此装配。

**配套修复**：工作区目录 chmod、playwright 输出目录解析、大 UID/GID（AD/LDAP 用户）容器兼容、
model override 经由 `options.model` 解析。

**与你的重叠**：中等。如果你的 fork 主要改 prompt 和流水线行为而非核心装配，冲突集中在
`container.ts`、`claude-executor.ts`、`activities.ts`、`workflows.ts`。建议吸收这套 DI 设计，
它让你后续的 fork 改动也能走注入而非硬改。

---

### E. #295 Provider 扩展 + 移除 claude-code-router 模式

建立在 #282 的 DI 基础上，进一步扩展注入点，并清掉了 router 模式。

**1. 新增 `ReportOutputProvider`**（`interfaces/report-output-provider.ts`）
   在 report agent 生成 Markdown 报告后回调，消费者可产出额外的衍生产物（如结构化 JSON）。
   默认 no-op。修复了 resume 路径上也会产出结构化 report JSON，且 provider 出错时 fail loud。

**2. 扩展 `CheckpointProvider`**
   从单一 `onAgentComplete` 扩展为两个方法：
   - `shouldSkipAgent(...)` — agent 执行前调用，返回 `{ skip: true, metrics }` 可跳过（resume 复用）
   - `onAgentComplete(...)` — 增加可选 `CheckpointContext`（repoPath/sessionId/outputPath）便于产物持久化
   配套 `SkipDecision`、`CheckpointContext` 类型。

**3. 移除 claude-code-router 模式**
   - 删除 `apps/worker/src/ai/router-utils.ts`
   - 从凭证解析、setup 向导、env 构建、billing 检测里清除 `ROUTER_DEFAULT` / `OPENROUTER_API_KEY` / `OPENAI_API_KEY`
   - 删除 `apps/cli/infra/router-config.json` 和 docker-compose 里的 router 配置
   - provider 枚举移除 `router`、`.env.example` 精简、issue 模板更新
   这表明官方把多模型路由收敛到了 `ProviderConfig`（#282）体系，不再支持独立 router。

**4. spending-cap 文案**：从消费上限的文本匹配模式里去掉 `resets` 关键词（误匹配修复）。

**与你的重叠**：若你的 fork 依赖 router 模式或自建了凭证流，这里删除会破坏你。否则是纯增量扩展。

---

### F. #371 支持多 repo 目标（移除 .git 检查）

这是一个小而精准的 PR，**改动集中在 `apps/worker/src/services/preflight.ts`**：

**要解决的问题**：原 preflight 校验要求目标目录必须含 `.git`，否则报
`Not a git repository`。但多 repo 目标（一个包含多个子 repo 的父目录）顶层没有 `.git`，
会被误拦。

**核心改动**：
1. `validateRepo` 去掉整个 `.git` 目录存在性检查（约 30 行），只校验路径存在且是目录
2. 删除 `skipGitCheck` 参数及它在 `validateRepo`、`runPreflightChecks`、
   `ActivityInput`、`PipelineInput`、workflows、shared 各处的传递链路
3. 注释说明：git checkpoint/rollback 在 `git-manager` 里已对非 git 目录做 no-op，
   因此 preflight 不再需要这层守卫

**影响面**：`preflight.ts`、`temporal/activities.ts`、`temporal/shared.ts`、`temporal/workflows.ts`，
净删除约 45 行，逻辑变简单。

**与你的重叠**：低。若你的 fork 给 preflight 加过别校验，注意 `validateRepo` 签名去掉了
`skipGitCheck` 参数，调用处要同步。

---

### 小结：这几个 PR 的演进关系

#256（monorepo+npx）打底 → #282（抽取核心库+DI）让流水线可嵌入 → #295（扩展 provider、砍 router）
进一步松耦合 → #350（MCP collector）把 agent 输出从自由文本升级为结构化 → #371（多 repo）
是小幅 preflight 放宽。#325（adaptive thinking）则是并行的模型能力升级。
其中 **#350 是功能质变**，**#256 是结构质变**，其余是围绕这两者的配套与松耦合。
