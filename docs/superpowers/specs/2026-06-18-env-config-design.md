# Spec:纯 `.env` 的 profile 化配置

- 日期:2026-06-18
- 分支:`feat/fork-py`
- 状态:设计已确认,待写实现计划

## 1. 背景与问题

当前引擎/账号配置全部平铺在单个 `.env`,解析集中在
`packages/core/src/shannon_core/agents/providers.py::build_provider_config`,存在三个结构问题:

1. **扁平 env + 多前缀隐式 fallback 链**。所有变量平铺,靠一串 `or` 串联优先级:
   - key:`SHANNON_OPENAI_API_KEY` → `SHANNON_API_KEY` → `ANTHROPIC_API_KEY` → `OPENAI_API_KEY`
   - base_url:`SHANNON_OPENAI_BASE_URL` → `SHANNON_BASE_URL` → `ANTHROPIC_BASE_URL`
   - tier model:`SHANNON_OPENAI_*_MODEL` → `SHANNON_*_MODEL`
   - 这些优先级只藏在代码里,`.env` 里看不出来。

2. **`load_dotenv(override=True)` 把多套变量全加载**。`SHANNON_AI_PROVIDER` 一行决定走哪个引擎,但用户的 `.env` 里实际并存了三套——智谱(anthropic 接口,当前生效)、智谱(openai 接口)、DeepSeek(整段注释),靠注释/反注释切换。因为 `override=True`,两套模型名(`GLM-5.2[1m]` vs `glm-5.2`)、两套 key 同时进环境,只是代码按引擎取其一。这就是"看起来两个引擎都开着"的根源。

3. **同一概念在不同引擎下变量名不同、归属靠注释**。例如 Bearer token:`ANTHROPIC_AUTH_TOKEN` 才对 `anthropic_api` 透传 Claude CLI,而 `SHANNON_AUTH_TOKEN` 只在 `litellm_router` 生效——"哪个变量对哪个引擎"全靠注释嘴上说,容易配错。

## 2. 目标与非目标

**目标**

- **可读**:打开配置就知道当前用哪个引擎、它需要哪些变量,不用翻代码查 fallback 优先级。
- **防错**:启动时校验变量与引擎是否匹配,不匹配直接报错,而非静默 fallback 到错变量。
- **切换便利**:智谱 Pro/Max、DeepSeek、不同端点之间一键切换,用户明确表示这几套经常来回切,不想每次改文件内容。
- **代码收敛**:把 `providers.py` 里散落的几十个 `os.getenv` 收敛成一个强类型 `Settings`,加载逻辑集中、可测。

**非目标(YAGNI)**

- 不引入 YAML/TOML 等结构化配置文件。项目配置(端点、key、模型名)本质是扁平 kv,没有嵌套/条件逻辑需求;`python-dotenv>=1.0` 已是依赖,`dot-git` 已忽略 `.env`。YAML+env 双文件对一个个人单机工具是过度设计。
- 不做"配置-机密分离"(配置进版本控制、机密单独管)。密钥随 profile 放在 `.env.profiles/`,只要该目录进 `.gitignore` 即可,这是个人单机工具的合理取舍。
- 不重构 bedrock / vertex / litellm_router 三个 provider 的读取逻辑(用户当前未使用),只保证不被破坏。

## 3. 方案决策与取舍

**选纯 `.env` 的 profile 化**(而非 YAML 结构化主配置):配置是扁平 kv,依赖已具备,迁移最直接,无新格式。

**删除现有跨前缀 fallback 链**:profile 化后每个 profile 文件自洽(只放它那套引擎的变量),跨前缀兜底(`SHANNON_API_KEY` → `ANTHROPIC_API_KEY` → `OPENAI_API_KEY`)不再需要。删除后行为可预测、代码更干净,由启动校验层保证每个 profile 自洽,无静默"惊喜"。

**保留 provider 类型路由**(这不是 fallback):读取哪个前缀的变量仍由 `SHANNON_AI_PROVIDER` 决定——`openai_compatible` 读 `SHANNON_OPENAI_*`,`anthropic_api` 读 `ANTHROPIC_*` / `SHANNON_*_MODEL`。这是显式路由,不在删除范围内。

**Settings 收敛这次一起做**:用户已将其列为目标,变量量级小,顺带完成,作为加载/校验之后的独立实现步骤。

## 4. 文件布局

```
.env                          # 共享配置 + profile 选择(不进 git)
.env.profiles/                # 各引擎/账号 profile(不进 git,含密钥)
  glm-anthropic.env
  glm-openai.env
  deepseek.env
.env.example                  # 共享模板(进 git,占位无密钥)
.env.profiles.example/        # 各 profile 模板(进 git,占位无密钥)
  glm-anthropic.env.example
  glm-openai.env.example
  deepseek.env.example
```

- 主 `.env` 只放**引擎无关的共享项**(`TEMPORAL_ADDRESS`、`SHANNON_BROWSER_ENGINE`、`SHANNON_DELIVERABLES_SUBDIR`、`SHANNON_WORKER_ROOT` 等)+ 一行 `SHANNON_PROFILE`。
- 每个 profile 文件**自洽**:引擎类型 `SHANNON_AI_PROVIDER` + 它专属的端点 / key / 模型。同一时刻只有"共享 + 一个 profile"被加载,两套并存从根上消失。
- `.env.profiles.example/` 是模板(占位、无真实密钥),进版本控制供首次设置参考;真实 `.env.profiles/` 不进 git。

## 5. 加载顺序

替换现有三个 CLI(`whitebox` / `blackbox` / `combined`)里 `load_dotenv(override=True)` 的直接调用,改为统一的 loader 函数:

1. 加载 `.env`(`override=True`)。
2. 读 `SHANNON_PROFILE`。
3. 加载 `.env.profiles/${SHANNON_PROFILE}.env`(`override=True`,profile 覆盖共享)。
4. `SHANNON_PROFILE` 未设置 → 报错指明需在 `.env` 设置该项。
5. profile 文件不存在 → **启动即报错**,指明应创建 `.env.profiles/${SHANNON_PROFILE}.env`。

加载完成、构造 provider 之前,执行启动校验(见第 6 节)。

## 6. 启动校验(防错核心)

校验当前 profile 的变量与声明的 `SHANNON_AI_PROVIDER` 是否自洽。失败用项目既有的 `PentestError`(`error_code=ErrorCode.CONFIG_VALIDATION_FAILED`),与 `config/parser.py` 现有校验风格一致。

**强制校验的 provider 必填变量集**(仅这两个 provider,用户实际使用):

| `SHANNON_AI_PROVIDER` | 必填端点 | 必填凭证 | 必填模型 |
|---|---|---|---|
| `anthropic_api`(含 glm-anthropic、deepseek 等 profile) | `ANTHROPIC_BASE_URL` | `ANTHROPIC_AUTH_TOKEN` 或 `ANTHROPIC_API_KEY`(二选一) | `SHANNON_LARGE_MODEL` / `SHANNON_MEDIUM_MODEL` / `SHANNON_SMALL_MODEL` |
| `openai_compatible`(如 glm-openai) | `SHANNON_OPENAI_BASE_URL` | `SHANNON_OPENAI_API_KEY` | `SHANNON_OPENAI_LARGE_MODEL` / `SHANNON_OPENAI_MEDIUM_MODEL` / `SHANNON_OPENAI_SMALL_MODEL` |

校验规则:
- `SHANNON_AI_PROVIDER` 必须存在且为已知值(`anthropic_api` / `openai_compatible` / `litellm_router` / `bedrock` / `vertex`)。
- 若为 `anthropic_api` 或 `openai_compatible`,上表必填变量必须齐全;缺失 → 报 "profile `${SHANNON_PROFILE}` 声明 provider `${type}`,但缺少 `${var}`"。
- `bedrock` / `vertex` / `litellm_router` 保留现有读取行为,本次不新增校验(用户未使用)。其内部 model tier 的取值沿用现状,不视为本次删除的"跨前缀 fallback"(后者专指凭证/端点的跨前缀兜底)。

## 7. Settings 收敛(删除 fallback 的具体影响)

新增强类型 `Settings`,把"provider → 它读哪些变量"的映射固化为显式字段,取代 `build_provider_config` 里散落的 `os.getenv` + `or` fallback 链。

- 删除 `api_key`、`base_url`、`model`、`region`、`project_id`、`auth_token`、tier model 的跨前缀 fallback `or` 链。
- 保留按 `SHANNON_AI_PROVIDER` 的显式路由(读 `SHANNON_OPENAI_*` 还是 `ANTHROPIC_*` / `SHANNON_*_MODEL`)。
- `Settings` 在加载 + 校验通过后构造,作为唯一配置入口传给 provider 构造逻辑。
- 现有依赖 fallback 链的单测需相应改写为"profile 文件直接给出对应变量"。

## 8. 模块结构(建议位置)

集中在 `packages/core/src/shannon_core/config/`(现有 `config/parser.py` 是扫描规则的 YAML 业务配置,与本设计运行时 provider 配置职责不同,不混):

- `config/env_loader.py`:加载 `.env` + profile 文件(第 5 节)。
- `config/profile_validator.py`:启动校验(第 6 节)。
- `config/provider_settings.py`:`Settings` dataclass + 按 provider 的字段映射(第 7 节)。

`providers.py::build_provider_config` 重构为从 `Settings` 取值;三个 CLI 的 `load_dotenv` 调用改为先 `env_loader.load()` 再 `profile_validator.validate()`。

最终模块归属由实现计划阶段细化,本 spec 仅给出建议。

## 9. 迁移步骤

1. 从现有 `.env` 拆出三个 profile 文件:
   - `glm-anthropic.env`:`SHANNON_AI_PROVIDER=anthropic_api` + 现有 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `SHANNON_*_MODEL`(智谱 Pro)。
   - `glm-openai.env`:`SHANNON_AI_PROVIDER=openai_compatible` + 现有 `SHANNON_OPENAI_*` 全套。
   - `deepseek.env`:`SHANNON_AI_PROVIDER=anthropic_api` + DeepSeek 的 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / 模型。
2. 主 `.env` 只保留共享项 + `SHANNON_PROFILE=glm-anthropic`(或当前首选)。
3. 同步拆 `.env.example` → `.env.example`(共享) + `.env.profiles.example/*.env.example`(各 profile 占位)。
4. `.gitignore` 增补 `.env.profiles/`(当前只忽略 `.env`)。
5. 改三个 CLI main 的加载调用为统一 loader + 校验。

## 10. 测试

- **profile 加载**:共享项 + profile 项叠加、profile 覆盖共享同名项。
- **profile 缺失**:`SHANNON_PROFILE` 未设置 / 文件不存在 → 报对应错误。
- **校验**:`anthropic_api` / `openai_compatible` 各自缺端点 / 缺凭证 / 缺模型 → 启动报错;齐全 → 通过。
- **Settings**:删除 fallback 后,给定 provider 类型正确取对应前缀变量;不再跨前缀兜底。
- 测试遵循项目惯例:只跑改动相关子集,不跑全套(全量会卡 Temporal / 网络慢测试)。

## 11. 范围与分阶段

本设计聚焦单一目标,可由一个实现计划覆盖。建议实现顺序(供 writing-plans 参考):

1. 新增 `env_loader` + `profile_validator`,接通三个 CLI(加载与校验先上,行为正确)。
2. 拆分 `.env` / `.env.profiles/`,补 `.gitignore`,建模板。
3. `Settings` 收敛 + 删除 `build_provider_config` 的 fallback 链 + 改写相关单测。

第 3 步独立,可在前两步验证通过后再做,降低风险。
