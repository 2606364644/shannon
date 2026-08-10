# 工作区配置:env 文本区直接编辑

> 日期:2026-08-10 ｜ 分支:feat/fork-py ｜ 主题:WEB 工作区设置从结构化表单改为 env 文本区

## 1. 背景与目标

WEB 工作区设置 `WsSettingsTab`(`packages/web/frontend/src/routes/WorkspaceDetail/WsSettingsTab.tsx`)当前是结构化表单(Select + 一组 Input + Switch),字段对应 `workspaces/<ws>/config.yaml` 的 provider / git 字段(凭据 Fernet 加密落盘)。

用户诉求:**「一个地方直接填,不要散落的框」**。

目标:把 `WsSettingsTab` 整体替换为一块 **env 文本区**(`KEY=value`),用户可直接把 `.env.profiles/<profile>.env` 的内容粘进来、保存即生效。凭据(token / api_key)页面掩码、智能保留;落盘仍加密。

## 2. 架构边界(关键约束)

worker 是**共享 temporal 进程**:`scan_manager.start_workflow(...)`(`scan_manager.py:317`)提交 workflow,worker 进程消费,多个工作区的扫描**并发跑在同一个进程里**。env 在该进程内是共享的 `os.environ`;只有 `provider_config` 作为 **workflow 参数**传入(`scan_manager.py:309,315`),所以它才能干净地 per-workspace 覆盖。

由此 env key 天然分两类,决定文本区里该 key 在 ws 级**是否真生效**:

- **扫描参数类**:作为 workflow 参数 / CLI 子进程 env,每次扫描独立 → **ws 覆盖生效**。
- **进程级开关类**:worker 进程内 `os.environ.get` 读取(并发共享)→ **ws 覆盖不生效**(会踩并发扫描),必须在全局 `.env` / `.env.profiles` 配置。

## 3. 设计:env 文本作表现层,config.yaml 仍为 SSOT

env 文本只换「怎么填」,不换「怎么存 / 怎么生效」:

- **GET**:后端把 ws 的 config 字段**反向渲染**成 env 文本;凭据字段渲染成 `KEY=••••`。
- **PUT**:前端发整段 env 文本;后端 parse `KEY=value` → 回 config 字段 → 加密落盘。

复用现有 `ws_config_store`(凭据加密 / 路径穿越校验 / `resolve_provider_config`)与 worker 注入链路,**不改 worker**。

## 4. 白名单分类

文本区接受三类 key,行为不同:

### 4.1 生效类(Accepted & Effective)— 存 config.yaml,真 ws 覆盖
provider 连接字段、`SUPERNOVA_AI_PROVIDER`、model / tier model、`SUPERNOVA_MAX_TURNS` / `SUPERNOVA_ADAPTIVE_THINKING`(约定,见 §5)、`GITLAB_USER` / `GITLAB_TOKEN`。

### 4.2 进程级警告类(Accepted but Ineffective)— 保存时警告,不阻塞
| env key | 读取点 |
|---|---|
| `SUPERNOVA_MAX_CONCURRENT` | `concurrency.py:18` |
| `SUPERNOVA_PRICING_OVERRIDE` | `pricing.py:86` |
| `SUPERNOVA_LLM_TRACK_ENABLED` | workflow dispatch 级 |
| `SUPERNOVA_GITNEXUS_LLM_ENABLED` | workflow dispatch 级 |
| `SUPERNOVA_AGENT_NARRATION_LANG` | `i18n.py:22` / `narration.py:49` |
| `CLAUDE_CODE_MAX_OUTPUT_TOKENS` | `providers_anthropic.py:201` |

保存时若含这些 key → 警告「以下 key 为 worker 进程级配置,ws 级不生效,请在全局 `.env` / `.env.profiles` 设置」;**文本里保留这些行,但运行时不读 ws 值**。

### 4.3 未知类(Unknown)— 保存时警告,不阻塞
不在以上两类的 key → 警告「已忽略未知 key」(可能是拼写错误)。

## 5. env key ↔ config 字段映射

### 5.1 反向映射(parse 用,key 名唯一,不依赖 provider)
| config 字段 | 接受的 env key | 凭据 |
|---|---|:--:|
| `ai_provider` | `SUPERNOVA_AI_PROVIDER` | |
| `base_url` | `ANTHROPIC_BASE_URL` / `SUPERNOVA_OPENAI_BASE_URL` / `SUPERNOVA_BASE_URL` | |
| `api_key` | `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY` / `SUPERNOVA_OPENAI_API_KEY` / `SUPERNOVA_AUTH_TOKEN` | ✓ |
| `model` | `SUPERNOVA_MODEL` | |
| `small_model` | `SUPERNOVA_SMALL_MODEL` / `SUPERNOVA_OPENAI_SMALL_MODEL` | |
| `medium_model` | `SUPERNOVA_MEDIUM_MODEL` / `SUPERNOVA_OPENAI_MEDIUM_MODEL` | |
| `large_model` | `SUPERNOVA_LARGE_MODEL` / `SUPERNOVA_OPENAI_LARGE_MODEL` | |
| `max_turns` | `SUPERNOVA_MAX_TURNS` | |
| `adaptive_thinking` | `SUPERNOVA_ADAPTIVE_THINKING` | |
| `gitlab_user` | `GITLAB_USER` | |
| `gitlab_token` | `GITLAB_TOKEN` | ✓ |

`SUPERNOVA_MAX_TURNS` / `SUPERNOVA_ADAPTIVE_THINKING` 为本次**约定的新 env key**(对应 config.yaml 独有字段;`max_turns` 为 int,`adaptive_thinking` 为 bool,取值 `true`/`false`)。

同字段多 env 变体(如 `base_url`)是因为 env key 是 per-provider 的;用户粘的 profile 只含一种 provider 的 key,不冲突,若同时出现则后出现者覆盖。

### 5.2 正向渲染(GET 用,按 provider 选 key 名)
按 `ai_provider`(ws config ?? 全局 `SUPERNOVA_AI_PROVIDER` ?? 默认 `anthropic_api`)选模板,复用 `PROVIDER_SETTINGS`:
- `anthropic_api` → `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `SUPERNOVA_{SMALL,MEDIUM,LARGE}_MODEL`
- `openai_compatible` → `SUPERNOVA_OPENAI_BASE_URL` / `SUPERNOVA_OPENAI_API_KEY` / `SUPERNOVA_OPENAI_{SMALL,MEDIUM,LARGE}_MODEL`
- `litellm_router` → `SUPERNOVA_BASE_URL` / `SUPERNOVA_AUTH_TOKEN` / `SUPERNOVA_OPENAI_*_MODEL`
- `bedrock` / `vertex` → `SUPERNOVA_{SMALL,MEDIUM,LARGE}_MODEL`(region / project_id 本次不存 config,见 §9)

仅渲染非 None 字段;凭据字段有值时渲染 `KEY=••••`,无值时整行省略。

## 6. 凭据掩码智能保留

- **GET 渲染**:`api_key` / `gitlab_token` 有值 → `KEY=••••`(与现有 GET 脱敏 sentinel `MASKED = "••••"` 一致)。
- **PUT parse**:凭据字段值 `== "••••"` → 保留原密文;否则更新(加密落盘)。非凭据字段不参与此比较。
- sentinel `••••` 足够独特,真实 token 不会等于 4 个 bullet。

## 7. PUT 语义:全量覆盖

文本区 = 这份 ws 配置的完整定义:
- 出现的生效字段 → 设为该值。
- **未出现的生效字段 → 清空(None,回落全局)。**
- 删行 = 清空。
- 凭据行被整行删除 → 凭据清空(回落全局);凭据行值仍为 `••••` → 保留原值。

## 8. 前端改动

`WsSettingsTab.tsx`:
- 移除 Select / Input / Switch 表单与 `EMPTY` / `PROVIDERS` / `TEXT_FIELDS` 常量。
- 替换为 `<Textarea>`(monospace 字体),`value` = GET 返回的 env 文本。
- 保存:发 PUT `{env_text: "..."}`;成功后用返回的 env 文本重置编辑器;失败 toast(403 / 422 / 网络)。
- 保存成功返回的**警告**(进程级 / 未知 key)以 toast 或文本区下方行内提示展示。
- `canEdit` 权限不变(workspace 角色 `admin` / `manager`)。

`api/wsConfig.ts`:
- `getWsConfig` → 返回 `{env_text: string}`;`putWsConfig` → 接 `{env_text}`。
- 移除结构化 `WsProviderFields` / `WsGitFields` 入参(仅 `WsSettingsTab` 使用)。

i18n:`wsConfig.*` 翻译键更新(字段标签 → 文本区标签 / 占位提示 / 警告文案)。

## 9. API 契约改动

`GET /api/workspaces/{ws}/config`(`ws_config.py:50`)→ 返回 `{env_text: "<渲染的 env 文本>"}`。

`PUT /api/workspaces/{ws}/config`(`ws_config.py:74`)→ 接 `{env_text: "..."}`;后端:
1. parse env 文本 → `{field: value}` + 收集(进程级 key 集合 / 未知 key 集合 / 格式错误)。
2. 格式错误(无 `=` 行、`SUPERNOVA_MAX_TURNS` 非整数、`SUPERNOVA_ADAPTIVE_THINKING` 非 `true`/`false`)→ 422。
3. 凭据字段值 `== "••••"` → 保留原值;否则更新。
4. 全量覆盖:未出现的生效字段 → None。
5. `validate_ws_config`(ai_provider 合法性)失败 → 422(现有逻辑)。
6. 加密落盘;返回 `{ok: true, warnings: {ineffective: [...], unknown: [...]}}`。

## 10. 不在范围(YAGNI / follow-up)

- `SUPERNOVA_REGION` / `SUPERNOVA_PROJECT_ID`(bedrock / vertex):`WsProviderFields` 未存、用户未用;本次归「未知 / 警告」。🔴 **待 plan 确认**:是否扩展 `WsProviderFields` 加 region / project_id。
- `CLAUDE_CODE_MAX_OUTPUT_TOKENS`:现状进程级读 `os.getenv`;可改造为 ws 生效(加进 provider_config,`providers_anthropic` 起 CLI 时从 config 读)。列为 follow-up,本次归进程级警告类。
- 真·任意 env per-ws(per-scan 子进程隔离):不做(见 §2 架构边界)。

## 11. 测试

后端(`ws_config_store` 及新增 env 序列化逻辑):
- parse ↔ render 双向(各 provider 模板)。
- 凭据掩码智能保留(`••••` 保留 / 新值更新 / 删行清空)。
- 进程级 key → `warnings.ineffective`;未知 key → `warnings.unknown`;注释 `#` / 空行忽略。
- 格式错误 / 非法 `ai_provider` / `SUPERNOVA_MAX_TURNS` 非整数 → 422。
- 凭据 Fernet 加密落盘不变;路径穿越校验(`_validate_ws_segment` + `is_relative_to`)不变。

前端(`WsSettingsTab`):
- textarea 渲染 env 文本;保存发 `env_text`;错误 / 警告展示;`canEdit=false` 禁用态。
