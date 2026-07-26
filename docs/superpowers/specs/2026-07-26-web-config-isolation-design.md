# Web 配置隔离 + 并发解锁 — 设计文档（P3c）

- **日期**：2026-07-26
- **分支**：`feat/fork-py`
- **状态**：基于三份探索报告（配置总线 / 执行链+并发 / multi-configs+凭据）+ 5 个架构决策已与用户确认；细节待用户审阅
- **依赖**：P0（认证）+ P1（成员制）+ P2（repo 隔离）**已实现并 final-review**；本 spec 在此之上
- **范围标记**：P3c。覆盖配置隔离（阶段 0-2）+ 并发解锁（阶段 3）+ clone 凭据 per-ws（阶段 4）。**不含** multi-configs 的 ws 隔离（前端未消费，YAGNI）
- **交付策略**：完整 spec 先行（本文件覆盖 0-4 全局视野 + 所有决策）；实现计划（plan）**分阶段出**，第一个 plan = 阶段 0

---

## 1. 背景

用户诉求（原话）："工作区除了隔离产物，还需要隔离模型配置，最好 `.env` 各种配置项都隔离" + "需要支持"多 workspace 并发扫描。

P0/P1/P2 已完成产物 / repo / 成员关系按 ws 物理隔离，但**配置与并发仍是全局单例**。三份探索报告（2026-07-26）的核心结论：

1. **`os.environ` 就是事实总线**——`packages/` 下约 201 处 `SUPERNOVA_*` 分散读取，无 RuntimeConfig/Settings 总对象。`env_loader.load_env()`（`core/config/env_loader.py:21-66`）在 worker 启动时一次性把 profile 冻结进 `os.environ`，全进程共享。
2. **provider 配置在 activity 调用时现读 env**——一次 scan 的 profile = worker 容器启动时的 `SUPERNOVA_PROFILE`，**要切 profile 必须重启 worker**。`PipelineInput`（`whitebox/pipeline/shared.py:8-21`）无 provider/profile 字段；`ActivityInput.api_key`（:47）web 路径恒 `None`。
3. **`build_provider_config`（`core/agents/providers.py:187-234`）已是「显式参数优先于 env」设计**（`_read` :237-242）——好消息：穿线只需补数据通道 + 调用点传参，签名基本不动。**坏消息**：引擎内部（`providers_anthropic.py`/`providers_openai.py`）绕过 `ProviderConfig` 直接 `os.getenv` 读 `CLAUDE_MAX_TURNS`/`SUPERNOVA_OPENAI_*` 等，这些是阶段 0 必须收编的硬点。
4. **并发 = 1 是硬钉的**——`worker/runner.py:75`（wb）/`:92`（bb）`max_concurrent_workflow_tasks=1`，注释明说是为兜 `AuditSession._current` 进程级模块全局单例（`core/audit/session_registry.py:14`）：两个 scan 并发，后到的 `set_audit_session`（`activities.py:1772`）覆盖前者 → 串台到错误 ws 的 `events.ndjson`/`session.json`。`LogBus`、`heartbeat._current_heartbeat` 同款单例。
5. **multi-configs 跑偏**——它其实是 correlation 多仓 scan 配置（`core/models/multi_repo_config.py:39-58`，repos/relations/out_workspace），**不是 provider 配置**；全局共享无 ws 维度（`MultiRepoConfigStore` 构造只吃单 `configs_dir`），**前端根本没消费**（grep 0 命中）。
6. **clone 凭据全局**——单一 `GITLAB_USER/TOKEN`（`web/config.py:14-15`）+ 单例 `GitFetcher`（`app.py:229`），无 per-ws 凭据概念。
7. **前端配置 UI = 只读**——`SettingsPage.tsx` 仅展示 systemStatus 快照，admin 在前端能改的配置 = 0；multi-configs / clone-creds / profile 切换 / users 管理 UI 全缺。

---

## 2. 目标 / 非目标

### 目标
- **per-ws provider 配置**（字段级）：每个 workspace 独立的 `ai_provider`/`api_key`/`base_url`/`model`/model tiers/运行时调参，未填字段回落全局默认
- **配置穿线**：ws 配置从 `scan_manager.start → PipelineInput → ActivityInput → run_claude_prompt → Provider` 一路显式传递，不再依赖 `os.environ` 全局
- **多 ws 并发扫描**：各自配置、各自 events/session 文件，不串台
- **凭据加密存储**：`api_key`/`gitlab_token` 等密文落盘，master key 受控
- **admin 前端配置 UI**：admin 在 UI 管理 ws 的 provider 配置 + clone 凭据
- **clone 凭据 per-ws**（阶段 4）：不同 ws 用不同 GitLab 账号/实例 clone

### 非目标
- **multi-configs 的 ws 隔离** → 不做（前端未消费，YAGNI；spec §9.E 标注未来需要再隔离）
- **全 `.env` 任意覆盖** → 不做（字段级覆盖足够；任意 env 注入危险且 200+ 读取点全覆盖不现实）
- **per-ws worker 进程/容器** → 不做（资源浪费；用「单 worker + 运行时按 ws 加载」替代）
- **users 账号 per-ws** → 不做（账号本身就该全局，P0 模型正确）
- **brand_name / browser_engine 等 web 进程级配置 per-ws** → 不做（这些是部署期 web 进程配置，非 scan 运行配置）

---

## 3. 架构总览 + 阶段路线图

### 3.1 三件正交但关联的改造

| 改造 | 性质 | 阶段 | 用户诉求 |
|---|---|---|---|
| **配置穿线** | 数据通道（让 per-ws 配置一路传到 provider） | 0 → 1 → 2 | 核心 |
| **并发解锁** | 单例解耦（AuditSession/LogBus/heartbeat contextvar） | 3 | 次要（"需要支持"） |
| **前端配置 UI** | 配套（admin 管 ws 配置） | 2（provider）+ 4（凭据） | 必要 |

**依赖关系**：配置穿线 ∥ 并发解锁（正交，可分别做）；前端 UI 依赖穿线的后端 API。
- 仅做并发不做穿线 → 多 ws 并发但都用全局配置（违背诉求）
- 仅做穿线不做并发 → 各 ws 用各自配置但串行跑（可接受，不违背）

### 3.2 阶段路线图（每阶段独立可验证、可 commit、可 merge）

```
阶段 0｜配置抽象地基（纯重构，零行为改变）
  ├─ 扩展 ProviderConfig 字段，收编引擎内部 os.getenv
  ├─ 引擎（anthropic/openai）改读 self.config，不再绕过
  └─ build_provider_config 扩展接受新字段（默认从 env 读，行为不变）
  验证：全量回归绿；scan 仍用全局配置跑通；零行为变化

阶段 1｜配置穿线（通道打通，仍全局配置）
  ├─ PipelineInput / ActivityInput 加 provider_config 字段
  ├─ workflow 把 PipelineInput.provider_config 灌进各 ActivityInput
  ├─ executor.py + 各 run_claude_prompt 直调点传 provider_config
  └─ scan_manager.start 提交时从全局 env 构造 provider_config 塞入
  验证：scan 仍用全局配置跑通；provider_config 字段全程非 None

阶段 2｜per-ws 配置（各自配置，串行跑）
  ├─ workspaces/<ws>/config.yaml（字段级，凭据 Fernet 密文）
  ├─ CredentialVault（Fernet，master key 受控）
  ├─ WsConfigStore（读写 + 校验）+ resolve_ws_config(ws) → provider_config dict
  ├─ scan_manager.start 按 ws 解析配置塞 PipelineInput
  ├─ admin API（GET/PUT /api/workspaces/{ws}/config，require_admin/manager）
  └─ 前端 ws 配置页（字段表单 + 凭据密文脱敏）
  验证：ws-A / ws-B 用不同 profile 各自跑通；未配字段回落全局

阶段 3｜并发解锁（各自配置 + 并发跑）
  ├─ AuditSession._current → contextvar（按 workflow_id 索引）
  ├─ LogBus / heartbeat._current_heartbeat 同步 contextvar 化
  ├─ worker max_concurrent_workflow_tasks 1 → N（env 可配，默认 2）
  └─ scan_manager 软门 max_concurrent 同步放开
  验证：两个 ws 同时 scan，events/session 不串台，各自配置生效

阶段 4｜clone 凭据 per-ws（可选，安全隔离刚需）
  ├─ git 凭据进 ws config.yaml（gitlab_user + gitlab_token_enc）
  ├─ GitFetcher 改 per-ws 查询（构造收 ws，_inject_auth 按 ws 取凭据）
  ├─ RepoManager.clone 调用链传 ws 凭据
  └─ 前端凭据编辑 UI（admin/manager）
  验证：ws-A / ws-B 用不同 GitLab 账号 clone 各自 repo
```

**阶段顺序不可调换的硬约束**：
- 阶段 3 必须先 contextvar 化（A）再放宽 worker（B）——**仅放宽 worker 不做 contextvar，多 scan 立即串台**（runner.py:72-75 注释明示）
- 阶段 2 依赖阶段 1 的穿线通道（要有字段承载 ws 配置）
- 阶段 1 依赖阶段 0 的 ProviderConfig 完整字段（引擎不再绕过 ProviderConfig 读 env，否则穿线传下去的配置被引擎内部 env 读取覆盖）

---

## 4. 关键决策（A-E，已确认）

| 决策 | 选择 | 理由 |
|---|---|---|
| **A. 配置隔离粒度** | **字段级** | 可控（只暴露该暴露的）+ 安全（不任意注入）+ 灵活（per-ws 覆盖关键字段）+ UI 友好（表单）。否决全 `.env` 级（危险 + 200+ 点不现实）/纯 profile 级（不够灵活） |
| **B. 配置存储 + 凭据加密** | **`workspaces/<ws>/config.yaml` + Fernet 对称加密** | ws 目录内聚，worker mount 透明；凭据密文落盘，master key 从 `SUPERNOVA_MASTER_KEY` env 读，首启自动生成。否决 DB（schema 僵化）/明文（不安全） |
| **C. provider 注入 worker 方式** | **单 worker + 运行时按 ws 加载** | 复用现有部署，activity 执行前按 `input.workspace_name` 读 ws config → 构造 ProviderConfig，web 提交端零改动。否决 per-ws worker（浪费）/多 queue（提交端要改路由） |
| **D. 并发解锁路径** | **contextvar 化 + 放宽 worker** | Agent B 论证的最小改造路径；`_current`→`ContextVar`（按 `workflow_id`）+ `max_concurrent_workflow_tasks` 1→N。顺序不能反 |
| **E. 阶段 4 可选项** | **clone 凭据 per-ws 做；multi-configs ws 隔离不做** | 凭据隔离是安全刚需（"隔离下载的仓库"隐含）；multi-configs 前端未消费，YAGNI |

---

## 5. 阶段 0：配置抽象地基（纯重构，零行为改变）

### 5.1 目标
让 `ProviderConfig` 成为**唯一**的 provider 配置载体：引擎内部不再绕过它读 `os.getenv`。所有调用点仍走默认（`build_provider_config` 从 env 构造），**行为零变化**。

### 5.2 改动点

**5.2.1 扩展 `ProviderConfig` 字段**（`core/agents/runner.py:27-44`）

现字段：`type / api_key / base_url / model / region / project_id / auth_token / small_model / medium_model / large_model`。

新增字段（收编引擎内部 `os.getenv`，全部默认 `None` = "未覆盖，读 env 默认"语义由 build 阶段填充）：

```python
@dataclass
class ProviderConfig:
    # ... 现有 10 字段 ...
    # 阶段 0 新增（运行时调参，收编引擎内部 os.getenv）
    max_turns: int | None = None              # CLAUDE_MAX_TURNS / SUPERNOVA_OPENAI_MAX_TURNS
    subagent_max_turns: int | None = None     # SUPERNOVA_OPENAI_SUBAGENT_MAX_TURNS
    max_output_tokens: int | None = None      # CLAUDE_CODE_MAX_OUTPUT_TOKENS
    call_timeout: int | None = None           # SUPERNOVA_OPENAI_CALL_TIMEOUT
    adaptive_thinking: bool | None = None     # CLAUDE_ADAPTIVE_THINKING
    pricing_override: str | None = None       # SUPERNOVA_PRICING_OVERRIDE
    model_context_override: str | None = None # SUPERNOVA_MODEL_CONTEXT_OVERRIDE
```

**5.2.2 引擎改读 `self.config`**（不再 `os.getenv`）

- `providers_anthropic.py`：`_build_sdk_env`（:193-255）的 `CLAUDE_CODE_MAX_OUTPUT_TOKENS`（:198）、`CLAUDE_MAX_TURNS`（:276）、`CLAUDE_ADAPTIVE_THINKING`（:329）改为 `self.config.max_output_tokens / max_turns / adaptive_thinking`，字段为 `None` 时回落原 env 读取（保行为不变）。
- `providers_openai.py`：`_max_turns`（:74）、`_subagent_max_turns`（:80）、`_call_timeout`（:93）的 `SUPERNOVA_OPENAI_*` 改读 `self.config.*`，`None` 回落 env。
- `pricing.py:86`（`SUPERNOVA_PRICING_OVERRIDE`）、`model_caps.py:51`（`SUPERNOVA_MODEL_CONTEXT_OVERRIDE`）：接受 `ProviderConfig` 或显式参数，`None` 回落 env。

**回落而非删除 env 读取**是阶段 0 的关键——保证零行为变化：字段 `None` → 走原 env 路径；阶段 2 填了字段 → 走字段。

**5.2.3 `build_provider_config` 扩展**（`providers.py:187-234`）

新增对应参数，默认从 env 读（行为不变）：

```python
def build_provider_config(
    provider_type: str | None = None,
    api_key: str | None = None,
    # ... 现有参数 ...
    # 阶段 0 新增（默认从 env 读，行为不变）
    max_turns: int | None = None,
    subagent_max_turns: int | None = None,
    max_output_tokens: int | None = None,
    call_timeout: int | None = None,
    adaptive_thinking: bool | None = None,
    pricing_override: str | None = None,
    model_context_override: str | None = None,
) -> ProviderConfig:
```

`_build_from_settings` / `_build_legacy` 同步填充新字段（`_read(param, env_name)` 模式）。

**5.2.4 `PASSTHROUGH_VARS` 处理**（`providers_anthropic.py:223-244`）

CLI 子进程 env 的 passthrough 白名单保持从 env 读（这些是**透传给 CLI 子进程**的 env，非 provider 配置语义，不属于 ProviderConfig 字段）。本阶段不动 passthrough，仅在 spec 标注：passthrough 是阶段 2 per-ws 隔离的次要点（CLI 子进程 env 注入需另设通道，可能用 `ProviderConfig.extra_env: dict`，留阶段 2 决策）。

### 5.3 行为不变量（阶段 0 验收）
- 全量相关回归测试绿（core agents / providers / pricing / model_caps）
- 任何调用点未传新字段 → 行为与改造前**逐字节一致**
- `ProviderConfig` 是引擎读取 provider 配置的**唯一**入口（grep 引擎内 `os.getenv(SUPERNOVA_|CLAUDE_)` 应仅剩 passthrough 与回落兜底）

---

## 6. 阶段 1：配置穿线（通道打通，仍全局配置）

### 6.1 目标
provider 配置经数据通道显式传递，不再隐式靠 `os.environ`。**此阶段配置仍是全局的**（从 env 构造），但通道打通，为阶段 2 per-ws 填充铺路。

### 6.2 改动点

**6.2.1 `PipelineInput` 加字段**（`whitebox/pipeline/shared.py:8-21`）

```python
@dataclass
class PipelineInput(BasePipelineInput):
    # ... 现有字段 ...
    provider_config: dict | None = None   # 阶段 1：None=未穿线（CLI 兜底走 env）；dict=显式配置
```

同步 `blackbox/pipeline/shared.py` 的 BlackboxPipelineInput。

**6.2.2 `ActivityInput` 加字段**（`shared.py:40-56`）

```python
@dataclass
class ActivityInput:
    # ... 现有字段（已有 api_key, workspace_name ...）...
    provider_config: dict | None = None   # 阶段 1：workflow 从 PipelineInput 灌入
```

**6.2.3 workflow 灌入**（`whitebox/pipeline/workflows.py` 起 activity 处，约 :129-140）

每个 `execute_activity` 调用把 `input.provider_config` 灌进 `ActivityInput.provider_config`。黑盒 workflow 同步。

**6.2.4 调用点传参**

- `executor.py:115`（`AgentExecutor.execute`，所有 vuln agent/recon/exploit 统一入口）：把 `input.provider_config` 传给 `run_claude_prompt(provider_config=...)`。
- 白盒直调点：`activities.py:296/688/1238/1281`（recon summary / chain verdict / gitnexus verdict / authz judge）传 `provider_config=input.provider_config`。
- 黑盒：经 `AgentExecutor`（`blackbox/.../activities.py:165/232/366`）自动覆盖。
- `services/poc_generator.py:494/541`：从 activity context 取 provider_config 传下去。

**6.2.5 `scan_manager.start` 提交时构造**（`web/components/scan_manager.py:121-143`）

```python
def _submit_whitebox(self, target, ws, event_file, req):
    from supernova_core.agents.providers import build_provider_config
    # 阶段 1：从全局 env 构造（行为不变）；阶段 2 改为按 ws 解析
    pc = build_provider_config()
    inp = PipelineInput(
        repo_path=..., web_url=..., workspace_name=ws, event_file=event_file,
        provider_config=asdict(pc),   # 阶段 1：全局 env 构造
    )
    client.start_workflow(WhiteboxScanWorkflow.run, inp, ...)
```

### 6.3 行为不变量（阶段 1 验收）
- scan 仍用全局配置跑通（与阶段 0 后行为一致）
- `provider_config` 字段在 `PipelineInput → ActivityInput → run_claude_prompt` 全程非 `None`
- `run_claude_prompt` 的 `provider_config is None` 分支（`runner.py:144-150`）在 web 路径不再命中（仍保留作 CLI 兜底）

---

## 7. 阶段 2：per-ws 配置（各自配置，串行跑）

### 7.1 目标
每个 workspace 独立 provider 配置（字段级），scan 按各自配置跑。**此阶段并发仍 = 1**（阶段 3 才解锁），但各 ws 配置已隔离。

### 7.2 数据与物理路径

**7.2.1 ws 配置文件**：`workspaces/<ws>/config.yaml`

```yaml
# workspaces/<ws>/config.yaml（字段级，未填 = None = 回落全局 env）
provider:
  ai_provider: openai_compatible      # 覆盖全局 anthropic_api
  api_key_enc: "gAAAAABj..."          # Fernet 密文（CredentialVault 加密）
  base_url: https://openai.example.com/v1
  model: glm-4.6
  small_model: glm-4.5-air
  medium_model: glm-4.6
  large_model: glm-4.6
  max_turns: 500
  adaptive_thinking: true
runtime:
  enable_llm_track: true              # 也可 per-ws 覆盖 LLM 轨开关
git:                                   # 阶段 4 填，阶段 2 留空
  gitlab_user: null
  gitlab_token_enc: null
```

**7.2.2 master key**：env `SUPERNOVA_MASTER_KEY`（base64 url-safe Fernet key）。
- 优先级：env > `workspaces/.master_key` 文件（gitignored，权限 0600）
- 首启：env 未设 + 无文件 → 用 `Fernet.generate_key()` 生成，写 `workspaces/.master_key`
- 生产部署：建议经 env 注入（docker-compose / k8s secret），不落盘

### 7.3 新增模块（`packages/web/src/supernova_web/auth/` 或新建 `config/` 子包）

**7.3.1 `CredentialVault`**（`web/components/credential_vault.py`）

```python
class CredentialVault:
    """Fernet 对称加密封装。master key 从 env/文件读，首启生成。"""
    def __init__(self, master_key_source: Path | str): ...
    def encrypt(self, plaintext: str | None) -> str | None: ...   # None → None
    def decrypt(self, token: str | None) -> str | None: ...        # None → None；无效 → None + warning
```

**7.3.2 `WsConfigStore`**（`web/components/ws_config_store.py`）

```python
@dataclass
class WsConfig:
    provider: ProviderFields | None    # ai_provider/api_key/base_url/model/tiers/max_turns/...
    runtime: RuntimeFields | None      # enable_llm_track
    git: GitFields | None              # 阶段 4

class WsConfigStore:
    """读写 workspaces/<ws>/config.yaml，凭据字段经 CredentialVault 加解密。"""
    def __init__(self, workspaces_dir: Path, vault: CredentialVault): ...
    def read(self, ws: str) -> WsConfig: ...            # 不存在 → 空 WsConfig（全 None）
    def write(self, ws: str, cfg: WsConfig) -> None: ... # api_key 明文输入 → 密文存储
    def resolve_provider_config(self, ws: str) -> dict: ...  # 拼 ws 覆盖 + 全局默认 → provider_config dict
```

`resolve_provider_config(ws)` 语义：读 ws config，**仅 ws 显式填的字段**覆盖全局 env 构造的 ProviderConfig（`build_provider_config()` 先给全局默认，再用 ws 字段覆盖）。返回 dict 供 `PipelineInput.provider_config`。

**7.3.3 凭据字段白名单**：`api_key` / `auth_token` / `gitlab_token` 走 `CredentialVault`；其余字段明文。白名单常量化防漏。

### 7.4 scan_manager 接入（`scan_manager.py:121-143`）

阶段 1 的 `build_provider_config()`（全局）替换为：

```python
ws_cfg = request.app.state.ws_config_store
pc_dict = ws_cfg.resolve_provider_config(ws)   # ws 覆盖 + 全局默认
inp = PipelineInput(..., provider_config=pc_dict)
```

### 7.5 admin API（`web/api/ws_config.py`，新文件）

```
GET  /api/workspaces/{ws}/config        → WsConfig（凭据字段脱敏：api_key → "••••" 是否已设）
PUT  /api/workspaces/{ws}/config        → 写 WsConfig（require_admin | workspace_manager）
                                         api_key 字段：空串=不清，"••••"=不改，新值=更新
POST /api/workspaces/{ws}/config/test   → 用该 ws 配置发探测请求验证连通（可选，YAGNI 可砍）
```

鉴权：`require_admin` 或 `workspace_manager`（P1 已有依赖）。读脱敏（不回传明文 key），写支持"保留原值"语义。

### 7.6 启动校验 per-ws

`profile_validator.validate_active_profile`（`core/config/profile_validator.py:20`）原只校验全局 active profile。阶段 2 加 `validate_ws_config(ws_cfg)`：校验该 ws 配置自洽（如 `ai_provider=openai_compatible` 时 `api_key` + `base_url` 必填）。校验时机：PUT `/api/workspaces/{ws}/config` 写入前 + scan_manager.start 提交前（fail-fast）。

### 7.7 前端设计（阶段 2）

**7.7.1 ws 配置页**（`/p/:workspace/settings`，admin/manager 可见）

- 字段表单（ai_provider 下拉 / api_key password 输入 / base_url / model / 3 个 tier / max_turns / adaptive_thinking switch）
- api_key 字段：占位 "已配置（••••）" 或 "未配置"；用户不聚焦不清空，留空提交 = 不改
- 未填字段提示"回落全局默认"
- 保存 → `PUT /api/workspaces/{ws}/config` + toast

**7.7.2 SettingsPage 扩展**（`/settings`）

- 现有只读 systemStatus 保留（展示**全局默认**配置）
- 加 admin 入口提示"per-workspace 配置在各 ws 的 Settings 页"

### 7.8 行为不变量 + 验收（阶段 2）
- 未配 ws（无 config.yaml 或全 None）→ 行为与阶段 1 一致（回落全局）
- ws-A 配 openai / ws-B 配 anthropic，分别 scan，各自用各自 provider 跑通
- 凭据密文落盘（`cat config.yaml` 不见明文 key）；master key 缺失时凭据字段降级为 None + warning（不崩）
- PUT API 非管理员 403；脱敏读不回传明文

---

## 8. 阶段 3：并发解锁（各自配置 + 并发跑）

### 8.1 目标
多 ws 同时 scan，各自 AuditSession/events/session 不串台。与阶段 2 叠加 = 完整诉求（各自配置 + 并发）。

### 8.2 改动点

**8.2.1 `AuditSession` contextvar 化**（`core/audit/session_registry.py`）

现 `_current: Any = None`（:14）是模块全局单值。改为按 `workflow_id` 索引 + contextvar 传当前 workflow：

```python
import contextvars
from temporalio import activity as _activity

_sessions: dict[str, "AuditSession"] = {}
_current_wf_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("wf_id", default=None)

def set_audit_session_for_workflow(wf_id: str, session) -> None:
    _sessions[wf_id] = session

def bind_current_workflow(wf_id: str) -> None:   # activity 入口调
    _current_wf_id.set(wf_id)

def get_audit_session():
    wf_id = _current_wf_id.get()
    if wf_id is not None and wf_id in _sessions:
        return _sessions[wf_id]
    # CLI 兜底：无 activity context 时走旧单值（_current_legacy）
    return _current_legacy if _current_legacy is not None else NullAuditSession()
```

- **web 路径**：`setup_display` activity（`activities.py:1731-1814`）改为 `set_audit_session_for_workflow(activity.info().workflow_id, session)` + `bind_current_workflow(wf_id)`；`finalize_summary` 按 wf_id 清。每个 activity 入口（workflow dispatch 处）`bind_current_workflow(input.workflow_id)`。
- **CLI 路径**：保留旧 `set_audit_session` / `_current_legacy` 单值（CLI 单进程单 scan，无需 contextvar），作 `get_audit_session` 的兜底分支。

**8.2.2 `LogBus` / `heartbeat` 同步 contextvar 化**

- `LogBus.attach`（`activities.py:1771`）：LogBus dispatcher 同样按 wf_id 索引（或 AuditSession 已含 dispatcher，经 `get_audit_session().dispatcher` 取，自动随 contextvar 隔离）。
- `heartbeat._current_heartbeat`（`core/runtime/heartbeat.py:95`）：同款 contextvar 化（按 wf_id），`start_heartbeat` / cancel 按 wf_id。

**8.2.3 worker 并发放宽**（`worker/runner.py:75/92`）

```python
# 现：max_concurrent_workflow_tasks=1（硬钉兜 AuditSession 单例）
# 阶段 3：contextvar 化后放宽
max_concurrent_workflow_tasks=int(os.getenv("SUPERNOVA_WORKER_MAX_CONCURRENT", "2"))
```

注释更新：contextvar 化后并发安全，N 可配。默认 2（保守，可按机器调）。

**8.2.4 scan_manager 软门同步**（`web/config.py:12 SUPERNOVA_WEB_MAX_CONCURRENT`）

web 提交端 `max_concurrent` 默认调到与 worker 匹配（或保留 1 由 admin 调，文档说明）。`scan_manager.start` 的 `TooManyScans`（:84）限流保留作 web 层背压。

**8.2.5 残留 env 回落清理**（`workflow_logger.py:91`）

`SUPERNOVA_WEB_EVENT_FILE` 的 `os.environ.get` 回落分支（C1 后已主要走 `PipelineInput.event_file`）在并发下是隐患（多 scan 共享 env）：阶段 3 移除该回落，强制 `event_file` 必须经 PipelineInput 透传（已是默认，清理兜底）。

### 8.3 验收（阶段 3）
- 两个 ws 同时 scan（ws-A 配 openai / ws-B 配 anthropic），各自 events.ndjson / session.json **不串台**（事件归属正确）
- `max_concurrent_workflow_tasks=2` 下两个白盒 scan 真并发（不再 FIFO 串行）
- CLI 单 scan 路径不受影响（contextvar 兜底分支覆盖）

---

## 9. 阶段 4：clone 凭据 per-ws（可选）

### 9.1 目标
不同 ws 用不同 GitLab 账号/实例 clone repo（安全隔离刚需）。

### 9.2 改动点

**9.2.1 凭据进 ws config**（§7.2.1 的 `git:` 段）

```yaml
git:
  gitlab_user: ci-bot-wsA
  gitlab_token_enc: "gAAAAA..."
```

未填 → 回落全局 `WebConfig.gitlab_user/gitlab_token`（env，行为不变）。

**9.2.2 `GitFetcher` per-ws**（`web/components/git_fetcher.py`）

- 现 `GitFetcher(repos_dir, gitlab_user, gitlab_token)` 单例（`app.py:229`）持一对凭据
- 改：`GitFetcher` 收 `WsConfigStore`（或凭据解析函数），`_inject_auth(url, ws)`（`git_fetcher.py:52-53`）按 ws 解析凭据：`ws_config_store.read(ws).git or 全局默认`
- `available(ws)` 按 ws 判断凭据是否配置

**9.2.3 `RepoManager` 调用链**（`repo_manager.py`）

`_clone_task`（:241-292）调 `self._git._inject_auth(url)`（:271）改为 `_inject_auth(url, ws)`；`RepoManager` 全方法已有 ws 维度（P2），凭据查询复用 ws 参数。`available()` 检查（:243）改 per-ws。

**9.2.4 前端凭据编辑 UI**（ws 配置页 §7.7.1 扩展 git 段）

- `gitlab_user` 文本 + `gitlab_token` password（脱敏同 api_key）
- 测试连接按钮（可选）

### 9.3 验收（阶段 4）
- ws-A 配 GitLab 账号 A / ws-B 配账号 B，各自 clone 各自可访问的 repo
- 未配 git 凭据的 ws → 回落全局凭据（行为不变）
- 凭据密文落盘，前端脱敏

### 9.4 非目标（阶段 4 不做）
- multi-configs（correlation 多仓图）的 ws 隔离——前端未消费，YAGNI。若未来铺前端再隔离：`MultiRepoConfigStore` 加 ws 维度 + 路由迁 `/api/workspaces/{ws}/multi-configs` + 文件迁 `workspaces/<ws>/configs/`。本 spec 仅标注，不实现。

---

## 10. 数据与物理路径（汇总）

```
workspaces/
├── .master_key                 # Fernet master key（gitignored, 0600）；或经 SUPERNOVA_MASTER_KEY env 注入
├── auth.db                     # P0：用户 + workspace_members（全局，已存在）
├── __legacy__/                 # P2：legacy repo 迁移
├── <ws-A>/
│   ├── config.yaml             # 【P3c 新增】ws provider + git 配置（凭据密文）
│   ├── repos/                  # P2：ws 隔离 repo
│   ├── session.json            # 运行态（contextvar 化后多 ws 不串台）
│   ├── events.ndjson           # 运行态
│   └── ...
└── <ws-B>/
    └── config.yaml             # 各自独立配置
```

- **不新增 DB 表**：ws 配置是文件（字段级 yaml + 密文），内聚于 ws 目录，worker mount 透明。
- **不动 docker-compose mount**：worker 已 mount `workspaces/`，config.yaml 路径透明。

---

## 11. 后端设计（跨阶段新增/改动清单）

| 阶段 | 新增文件 | 改动文件 |
|---|---|---|
| 0 | — | `core/agents/runner.py`（ProviderConfig 扩字段）/ `providers.py`（build 扩参）/ `providers_anthropic.py` + `providers_openai.py`（改读 self.config）/ `pricing.py` + `model_caps.py` |
| 1 | — | `whitebox/pipeline/shared.py` + `blackbox/pipeline/shared.py`（PipelineInput/ActivityInput 加字段）/ `workflows.py`（灌入）/ `executor.py` + `activities.py`（传参）/ `scan_manager.py`（构造塞入） |
| 2 | `web/components/credential_vault.py` / `ws_config_store.py` / `api/ws_config.py` | `web/components/scan_manager.py`（resolve_ws_config）/ `web/app.py`（装配 store + 注册路由）/ `core/config/profile_validator.py`（per-ws 校验） |
| 3 | — | `core/audit/session_registry.py`（contextvar）/ `core/runtime/heartbeat.py` + LogBus / `worker/runner.py`（max_concurrent 放宽）/ `workflow_logger.py`（清 env 回落） |
| 4 | — | `web/components/git_fetcher.py`（per-ws 凭据）/ `repo_manager.py`（传 ws 凭据）/ 前端 ws 配置页 git 段 |

---

## 12. 测试策略

### 12.1 阶段 0（纯重构）
- `test_provider_config_fields.py`：ProviderConfig 新字段默认 None；build_provider_config 不传新字段 → 行为逐字节等于改造前（快照对比）
- `test_engine_reads_config_not_env.py`：引擎构造时 `self.config.max_turns` 覆盖 env（字段非 None 走字段，None 回落 env）
- 回归：现有 providers/pricing/model_caps 测试全绿

### 12.2 阶段 1（穿线）
- `test_pipeline_input_carries_provider_config.py`：PipelineInput.provider_config 经 workflow → ActivityInput → run_claude_prompt 全程非 None
- `test_scan_manager_injects_global_config.py`：scan_manager 提交时 provider_config = 全局 env 构造（行为不变）

### 12.3 阶段 2（per-ws）
- `test_credential_vault.py`：encrypt/decrypt 往返；None 透传；密文不含明文；master key 缺失降级
- `test_ws_config_store.py`：read/write/resolve；ws 字段覆盖全局默认；未填回落；凭据密文落盘
- `test_api_ws_config.py`：GET 脱敏 / PUT 非管理员 403 / 写入校验 422
- `test_scan_uses_ws_config.py`：ws-A / ws-B 不同 profile 各自跑通
- 前端：ws 配置页表单 + 保存；api_key 脱敏占位

### 12.4 阶段 3（并发）
- `test_audit_session_concurrent.py`：两个 workflow_id 并发 set/get 不串台（模拟多 activity context）
- `test_concurrent_scans_no_cross_talk.py`：两个 ws 并发 scan，events/session 归属正确（集成）
- 回归：单 scan 路径不退化

### 12.5 阶段 4（凭据）
- `test_git_fetcher_per_ws.py`：不同 ws 不同凭据；未配回落全局
- 前端：凭据编辑表单

---

## 13. 范围边界

- **multi-configs ws 隔离** → 不做（前端未消费）
- **全 `.env` 任意覆盖** → 不做（字段级足够）
- **per-ws worker 进程** → 不做
- **users per-ws** → 不做
- **brand/browser_engine 等 web 进程级配置 per-ws** → 不做
- **CLI 路径的 per-ws** → 不做（CLI 是单进程单 scan，profile 经 `.env.profiles` 切换，保持现状；仅 web 路径 per-ws）

---

## 14. 决策记录

1. **字段级配置**（非全 .env / 非纯 profile）——决策 A，可控 + 安全 + 灵活 + UI 友好。
2. **ws 目录文件存储 + Fernet 加密**（非 DB / 非明文）——决策 B，ws 内聚 + 凭据安全。
3. **单 worker + 运行时按 ws 加载**（非 per-ws worker / 非多 queue）——决策 C，复用部署。
4. **contextvar 化 + 放宽 worker**（顺序不可反）——决策 D，Agent B 最小路径。
5. **clone 凭据 per-ws 做；multi-configs 不做**——决策 E，安全刚需 vs YAGNI。
6. **ProviderConfig 为唯一配置载体**（阶段 0 收编引擎 os.getenv）——穿线的前提，否则传下去的配置被引擎内部 env 覆盖。
7. **回落而非删除 env 读取**（阶段 0）——保零行为变化；字段 None → 走 env，字段非 None → 走字段。
8. **CLI 路径保留单值兜底**（阶段 3 contextvar）——CLI 单 scan 无需 contextvar，兼容现有 `_current`。
9. **凭据字段白名单**（api_key/auth_token/gitlab_token）——常量化，防新增凭据字段漏加密。
10. **master key 优先 env，首启生成文件兜底**——生产经 secret 注入，开发自生成。

---

## 15. 阶段化交付与风险

| 阶段 | 风险 | 回滚成本 | 可独立 merge |
|---|---|---|---|
| 0 | 低（纯重构，回落保兼容） | 低（git revert） | ✅ |
| 1 | 低-中（穿线，行为不变） | 低 | ✅ |
| 2 | 中（凭据加密 + admin API + 前端） | 中（ws config 文件可删） | ✅ |
| 3 | **高**（contextvar 化触及并发核心，回归面广） | 中-高 | ✅（但需充分回归） |
| 4 | 中（凭据查询改造） | 低 | ✅ |

**最大风险**：阶段 3 contextvar 化。`AuditSession`/`LogBus`/`heartbeat` 三处单例任一漏改 → 并发串台。缓解：`test_concurrent_scans_no_cross_talk.py` 集成测试 + 保守默认 `max_concurrent=2`。

**建议执行顺序**：0 → 1 → 2 →（充分冒烟）→ 3 →（充分冒烟）→ 4。每阶段独立 plan + 独立 merge + 冒烟后再进下一阶段。

---

**下一步**：本文档审阅通过后，invoke writing-plans 出**阶段 0** 的实现计划（第一个 plan，纯重构低风险）。阶段 1-4 各自后续出 plan。
