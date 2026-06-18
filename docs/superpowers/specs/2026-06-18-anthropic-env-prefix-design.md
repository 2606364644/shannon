# `ANTHROPIC_*` 环境变量前缀化 + 彻底解耦 设计

> **状态:** 设计确认(2026-06-18),待写实现计划。
> **关联:** 上游为 [纯 .env profile 化配置](../plans/2026-06-18-env-config-profiles.md) / [设计](./2026-06-18-env-config-design.md);本次收口其遗留的命名不对称。

## 1. 背景 / 问题

env-config profile 化完成后,`anthropic_api` 引擎仍用标准名 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY`,而:

- 这三个名字**与 Claude Code(Claude Agent SDK 驱动的 CLI 子进程)自身用的变量名完全相同**。
- 在 GLM / DeepSeek 的 profile 里,`ANTHROPIC_BASE_URL` 却指向智谱 / DeepSeek 端点 —— 名实不符,误导。
- 同一 shell 里若 Claude Code 与 shannon 共存,`ANTHROPIC_*` 会互相覆盖(串味)。
- 与 OpenAI 引擎不对称:OpenAI 侧已用前缀名 `SHANNON_OPENAI_*`,Anthropic 侧没有。

**机制差异(为什么不能像 OpenAI 那样直接改名):**

| 引擎 | 底层 | 谁读变量 |
|---|---|---|
| OpenAI | 进程内 `AsyncOpenAI(api_key=, base_url=)` 显式注入(`providers_openai.py:58-68`) | 项目代码读,值作为构造参数喂给 SDK,SDK 不碰 env → 变量名随便取 → 用了 `SHANNON_OPENAI_*` |
| Anthropic | 驱动 **Claude Code CLI 子进程**(`claude_agent_sdk` → `query()`) | **Claude Code CLI 写死读 `ANTHROPIC_*`**;`_build_sdk_env()` 把 `ANTHROPIC_*` 塞进子进程 env |

因此 Anthropic 侧的变量名必须最终落到标准 `ANTHROPIC_*`,但**来源**可以(也应该)是带前缀、不撞名的名字。

## 2. 目标与核心原则

把 `anthropic_api` 的 3 个撞名变量改为 `SHANNON_ANTHROPIC_*`,并贯彻:

> **`ANTHROPIC_*` 标准名在整个项目里只作为 `_build_sdk_env()` 的输出**(发给 Claude Code CLI 子进程)。项目自身的 config 层(profiles、`PROVIDER_SETTINGS`、`_build_legacy`、`profile_validator`、`credential_validator`)**一律不再读、不再透传** bare `ANTHROPIC_*` 的凭据/端点变量。

`_build_sdk_env()` 是 `SHANNON_ANTHROPIC_*` → `ANTHROPIC_*` 的**唯一翻译点**。

## 3. 作用域

**本次改(撞 Claude Code 凭据/端点的 bare `ANTHROPIC_*`):**

- `ANTHROPIC_API_KEY` → `SHANNON_ANTHROPIC_API_KEY`
- `ANTHROPIC_BASE_URL` → `SHANNON_ANTHROPIC_BASE_URL`
- `ANTHROPIC_AUTH_TOKEN` → `SHANNON_ANTHROPIC_AUTH_TOKEN`
- `ANTHROPIC_MODEL`(`_build_legacy:260` 的 fallback 项)—— 同属 bare `ANTHROPIC_*`,Claude Code 也用它覆盖模型,一并收口(model 回退到 `SHANNON_MODEL` / `DEFAULT_MODELS`)。

**保留不动(详见 §6 裁决):**

- `ANTHROPIC_VERTEX_PROJECT_ID`(`_build_legacy:264`)—— Vertex 标准基建变量,语义同 `AWS_REGION`/`CLOUD_ML_REGION`,不是 Claude Code 凭据撞名。
- 模型 tier `SHANNON_SMALL_MODEL` / `SHANNON_MEDIUM_MODEL` / `SHANNON_LARGE_MODEL`(不撞名)。
- `providers_openai.py:61` 的 `or os.getenv("OPENAI_API_KEY")`(同类撞名,但本次只做 ANTHROPIC_*)。

## 4. 架构:翻译边界与数据流

```
.env.profiles/<p>.env  写 SHANNON_ANTHROPIC_BASE_URL / _API_KEY / _AUTH_TOKEN
   │ load_env()
   ▼
os.environ  (SHANNON_ANTHROPIC_*)
   │ build_provider_config()   ← PROVIDER_SETTINGS["anthropic_api"] 读前缀名
   ▼
ProviderConfig  (.api_key / .base_url / .auth_token)
   │ AnthropicProvider._build_sdk_env()   ← ★唯一翻译点★
   │   显式 emit: ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN
   ▼
Claude Code CLI 子进程  (读标准 ANTHROPIC_*,它只认这些名)
```

要点:CLI 拿到的是已翻译的标准名(来自 config/profile),profile 写的是不撞名的前缀名;`ANTHROPIC_*` 不再从 shell 透传进 CLI(去掉 PASSTHROUGH)。

## 5. 详细改动(file → 现状 → 目标)

### 5.1 `config/provider_settings.py`(`anthropic_api` 映射,当前 `:38-40`)

```
base_url="ANTHROPIC_BASE_URL"   →   "SHANNON_ANTHROPIC_BASE_URL"
api_key="ANTHROPIC_API_KEY"     →   "SHANNON_ANTHROPIC_API_KEY"
auth_token="ANTHROPIC_AUTH_TOKEN" → "SHANNON_ANTHROPIC_AUTH_TOKEN"
```

模型 tier(`SHANNON_*_MODEL`)、`required`、其余 provider 不变。`profile_validator` 自动跟随(它读 `PROVIDER_SETTINGS`)。

### 5.2 `agents/providers_anthropic.py` `_build_sdk_env()`(当前 `:167-220`)

- **`anthropic_api` 分支(当前 `:177-179` 只 emit `ANTHROPIC_API_KEY`):** 补显式 emit 全部三个标准名,值取自 `self.config`:
  - `sdk_env["ANTHROPIC_API_KEY"] = self.config.api_key`(已有)
  - 新增 `sdk_env["ANTHROPIC_BASE_URL"] = self.config.base_url`
  - 新增 `sdk_env["ANTHROPIC_AUTH_TOKEN"] = self.config.auth_token`
- **`litellm_router` 分支(`:191-194`)emit `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`:** 保留(这是翻译边界的输出侧,值来自 `SHANNON_BASE_URL`/`SHANNON_AUTH_TOKEN` 的 config)。
- **PASSTHROUGH_VARS(`:197-212`):** 删除 `ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL`、`ANTHROPIC_AUTH_TOKEN` 三项。其余(`CLAUDE_CODE_OAUTH_TOKEN`、Bedrock/Vertex 基建项、`HOME`/`PATH` 等)保留。

> 后果:CLI 子进程不再从 shell 继承 stray `ANTHROPIC_*`;只从 config(profile)拿。彻底消除串味。

### 5.3 `agents/providers.py` `_build_legacy()`(当前 `:234-291`)

删每条 fallback 链里的 bare `ANTHROPIC_*` 凭据/模型项,其余(`SHANNON_*`/`OPENAI_API_KEY`/`AWS_REGION` 等)保留:

- `api_key`(`:249-253`):`SHANNON_API_KEY or ANTHROPIC_API_KEY or OPENAI_API_KEY` → `SHANNON_API_KEY or OPENAI_API_KEY`
- `base_url`(`:258`):`SHANNON_BASE_URL or ANTHROPIC_BASE_URL` → `SHANNON_BASE_URL`
- `model`(`:260`):`SHANNON_MODEL or ANTHROPIC_MODEL` → `SHANNON_MODEL`
- `auth_token`(`:266`):`SHANNON_AUTH_TOKEN or ANTHROPIC_AUTH_TOKEN` → `SHANNON_AUTH_TOKEN`
- **保留** `project_id`(`:264`):`SHANNON_PROJECT_ID or ANTHROPIC_VERTEX_PROJECT_ID`(Vertex 标准基建名,见 §6)

> `openai_compatible` 不走 `_build_legacy`(上游 `_build_from_settings` 拦截),故只 `litellm_router` 受 base_url/auth_token 的 `is_litellm` 分支影响,其 `SHANNON_OPENAI_*` 读取不变。

### 5.4 `utils/credential_validator.py`(openai_compatible 错误信息)

当前 openai_compatible 的报错文案引用了**过时**变量名(openai_compatible 实际读 `SHANNON_OPENAI_*`,见 env-config 重构):

- `:140`:`"OpenAI-compatible provider requires api_key (SHANNON_API_KEY or ANTHROPIC_API_KEY)"` → 改为引用 `SHANNON_OPENAI_API_KEY`(去掉 `ANTHROPIC_API_KEY`)。
- `:147`:`"OpenAI-compatible provider requires base_url (SHANNON_BASE_URL)"` → 改为 `SHANNON_OPENAI_BASE_URL`。

### 5.5 profile 模板与本地 profile(变量名改名)

- 入库模板 `.env.profiles.example/glm-anthropic.env.example`、`.env.profiles.example/deepseek.env.example`:`ANTHROPIC_*` → `SHANNON_ANTHROPIC_*`。
- 本地 `.env.profiles/glm-anthropic.env`(携真实值)、`.env.profiles/deepseek.env`(占位):同步改名。
- `.env.profiles/glm-openai.env` / `.example` 不受影响(走 `SHANNON_OPENAI_*`)。

## 6. 作用域裁决:为什么 `ANTHROPIC_VERTEX_PROJECT_ID` 保留

`_build_legacy:264` 的 `ANTHROPIC_VERTEX_PROJECT_ID` 是 Vertex AI 的**标准 GCP 项目变量**,语义与 Bedrock 的 `AWS_REGION` / `CLOUD_ML_REGION` 一致 —— 是云厂商基建配置,不是 Claude Code 凭据撞名,删了会破坏 Vertex 从标准 env 发现项目的能力。故保留。`ANTHROPIC_MODEL` 则相反(Claude Code 用它覆盖模型,属撞名面),删之,model 回退到 `SHANNON_MODEL` / `DEFAULT_MODELS`。

## 7. 迁移(破坏性)

**Breaking:** 任何 anthropic 类 profile(`anthropic_api` provider)里写的 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY` 必须改名为 `SHANNON_ANTHROPIC_*`,否则 `profile_validator` 启动报缺必填变量。

需同步改名:本地 `.env.profiles/glm-anthropic.env`、`deepseek.env`;入库模板对应两个。`glm-openai` 不受影响。

## 8. 测试

- **`provider_settings`:** anthropic 映射断言改读 `SHANNON_ANTHROPIC_*`(改既有用例)。
- **`profile_validator`:** `ANTHROPIC_OK` 等基准字典换前缀名;完整/缺变量/credential 二选一用例仍成立。
- **`_build_sdk_env`(新增,当前无覆盖):**
  - `anthropic_api` 时,sdk_env 含标准 `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_API_KEY`,值来自 config(即使 shell 里没有这些标准名)—— 证明不靠 PASSTHROUGH。
  - PASSTHROUGH 不再泄漏:shell 里设了 `ANTHROPIC_BASE_URL` 也不会进 sdk_env(仅当 config 提供时才进)。
- **`test_providers`(`_build_legacy`):** 删 `ANTHROPIC_*` 后,bedrock/vertex/litellm 既有用例仍绿(它们测试里不依赖 `ANTHROPIC_*`)。
- **测试范围:** 只跑相关子集;沿用 test-gotchas(`--ignore` `test_worker_progress`/`test_cli follow`/`test_audit_injection`/integration 挂起项),不跑全量。

## 9. 风险 / 权衡

- **得:** 不再和 Claude Code 撞名;名实相符;`ANTHROPIC_*` 归属清晰(仅 CLI 子进程);消除 OpenAI/Anthropic 命名不对称。
- **失:** 不能再和裸 Claude Code CLI 共用一份 `.env`(shannon 走 profile,本就不需要);breaking 改名(影响存量 anthropic profile,需迁移)。
- **风险点:** `_build_sdk_env` 必须把三个标准名都显式 emit,否则 anthropic_api 的 base_url/auth_token 断供(当前靠 PASSTHROUGH 透传)。单测覆盖此路径。

## 10. 不在范围 / 后续

- `providers_openai.py:61` 的 `or os.getenv("OPENAI_API_KEY")`(OpenAI SDK 同类撞名)——结构相同,后续可对称处理(本次只 ANTHROPIC_*)。
- 模型 tier 命名统一(anthropic 用 `SHANNON_*_MODEL` vs openai 用 `SHANNON_OPENAI_*_MODEL` 的不对称)——独立议题,不在此。
