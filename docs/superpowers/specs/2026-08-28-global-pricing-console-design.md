# 全局 + 工作区两级定价管理（web 控制台）— 设计 spec

- 日期：2026-08-28
- 状态：设计中（已与用户确认方案一 + 界面接管语义）
- 上游背景：CLAUDE.md §4 cost 计费（per-profile 定价 + 双引擎统一自算，spec 2026-07-09）

## 1. 背景与痛点

定价链路现状（`packages/core/src/supernova_core/agents/pricing.py`）：

```
BUILTIN_PRICING_CNY（内置 4 模型，CNY）
  ∪ SUPERNOVA_PRICING_OVERRIDE 指向的 JSON 文件
      └─ _load_override() 用 ws_getenv 读：工作区 env 覆盖 > 进程 env（per-profile）
```

- per-profile 层**实际在用**：`.env.profiles/*.env` 显式设 `SUPERNOVA_PRICING_OVERRIDE=.env.profiles/<base>.pricing.json`（本机 glm / deepseek 两份，含 `deepseek-v4-pro` 这类内置表没有的模型）。
- 工作区层：`SUPERNOVA_PRICING_OVERRIDE` 在 `ws_env_codec.SCAN_ENV_KEYS` 白名单里，工作区可经 env 文本框各自覆盖——机制在，但**没有任何像样的 UI**，只能手写文件路径。
- 全局层：**不存在**。想统一调价只能改 `.env.profiles/` 下的 JSON 文件 + 重启（或依赖 worker 现读文件的特性不重启）。

用户需求：**一个 web 界面调整各模型定价，全局生效、不分工作区**；同时保留工作区覆盖能力，且工作区也要有像样的定价配置 UI（复用全局的编辑组件）。

## 2. 目标 / 非目标

**目标**

1. core 定价合并插入「全局层」：`内置 < profile env < 全局表（web 管理） < 工作区覆盖`。
2. web 后端：全局定价 store + API（admin 编辑、全员可看）；工作区定价覆盖 API（复用编辑器，默认继承全局）。
3. 前端：`PricingEditor` 复用组件 + SettingsPage「模型定价」Section + WsSettingsTab「定价」卡片，来源徽章标注每个价的出处。
4. 界面保存即生效（worker 现读文件），无需重启。

**非目标**

- 不做阶梯计费 / 分档定价（pricing 单一档位近似，维持现状约定）。
- 不动 cost 落盘字段语义（`cost_usd`/`cost_currency` 不变量不变）。
- 不删除 profile env override 机制（保留给 CLI 直跑与未建全局表的部署）。
- 不做跨币种聚合 / 汇率换算（`USD_CNY_RATE` 保留现状）。

## 3. 现状锚点（实现时核对）

| 位置 | 现状 |
|---|---|
| `packages/core/src/supernova_core/agents/pricing.py` | `_load_override()` 经 `ws_getenv` 读 override 路径（ws > process 混在一层）；`_pricing()` = BUILTIN ∪ override；每次调用现读文件（无缓存） |
| `packages/core/src/supernova_core/config/scan_env.py` | `_SCAN_ENV` 私有 dict + `ws_getenv`（ws 覆盖 > os.environ）；无 ws-only 公开读取 API |
| `packages/core/src/supernova_core/config/env_loader.py:55-64` | 仅当用户未显式设 `SUPERNOVA_PRICING_OVERRIDE` 时 wire `<profile>.pricing.json` / `<base>.pricing.json` |
| `packages/web/src/supernova_web/components/ws_env_codec.py:52-64` | `SCAN_ENV_KEYS` 白名单含 `SUPERNOVA_PRICING_OVERRIDE` |
| `packages/web/src/supernova_web/app.py` | `create_app` 挂 `app.state.branding_store` 等 store；startup 里 load_env（web 进程 env 有 profile 的 PRICING 键） |
| `packages/web/src/supernova_web/components/scan_manager.py:1895` | `_resolve_env_overrides(ws)` → `ws_config_store.resolve_env_overrides(ws)`，作为 activity env_overrides 注入 scan_env 覆盖层 |
| `packages/web/src/supernova_web/api/branding.py` | 全局 store + API 先例：GET 全员 / PUT admin（`require_admin`） |
| `packages/web/src/supernova_web/api/ws_config.py` | ws 权限先例：GET `workspace_member` / PUT `workspace_manager`（admin 直通） |
| 前端 `pages/SettingsPage.tsx:159` | `isAdmin = user?.role === "admin"` 先例；Section 分区先例 |
| 前端 `routes/WorkspaceDetail/WsSettingsTab.tsx:184-186` | `canEdit = admin | manager`（workspace 级角色）先例；Card 结构先例 |
| 部署 | worker 由 web（scan_manager）spawn，继承 web 进程 env → 注入的 env 键 worker 可见 |

## 4. 设计

### 4.1 core：定价分层合并（`pricing.py` + `scan_env.py`）

`scan_env.py` 新增公开函数（只读工作区覆盖层、不回落 os.environ）：

```python
def ws_override_get(key: str) -> str | None:
    """仅读当前扫描的工作区覆盖值；无覆盖层 / 键不在层内 → None（不回落 os.environ）。"""
```

`pricing.py` 把 `_load_override()` 拆为按路径读文件的纯函数 + 三层读取：

```python
def _load_pricing_file(path: str | None) -> dict:   # 现读 + 容错（现状 _load_override 逻辑）
    ...  # 返回 {} 或 {"currency": ..., "models": {...}}（旧 flat schema 兼容维持）

def _pricing() -> tuple[dict, str]:
    table = dict(BUILTIN_PRICING_CNY)
    layers = [
        _load_pricing_file(os.environ.get("SUPERNOVA_PRICING_OVERRIDE")),      # process 层（per-profile）
        _load_pricing_file(os.environ.get("SUPERNOVA_GLOBAL_PRICING")),        # 全局层（web 注入，压过 process）
        _load_pricing_file(ws_override_get("SUPERNOVA_PRICING_OVERRIDE")),     # 工作区层（最高）
    ]
    currency = "CNY"
    for layer in layers:            # 低 → 高逐层 update
        if isinstance(layer.get("models"), dict):
            currency = layer.get("currency", "CNY") or "CNY"   # 币种 = 最高优先非空层
            table.update(layer["models"])
        elif layer:                 # 旧 flat schema（仅 process 层现实存在）
            currency = "CNY"
            table.update(layer)
    return table, currency
```

- **`SUPERNOVA_GLOBAL_PRICING`**（新 env 键）：全局价目表 JSON 路径，由 web 进程启动时 `os.environ.setdefault("SUPERNOVA_GLOBAL_PRICING", str(workspaces_dir / "pricing.json"))` 注入；worker 子进程继承。CLI 直跑未设 → 无此层，行为与现状完全一致。
- **兼容不变量（TDD 锚）**：未设 GLOBAL 键且无 ws 覆盖时，`_pricing()` 输出与现状（BUILTIN ∪ ws_getenv 混合层）逐项等价。
- schema：全局层 / 工作区层统一新 schema `{"currency": "CNY"|"USD", "models": {model: {input, output, cache_read, cache_creation}}}`；process 层旧 flat 兼容维持。

### 4.2 web 后端：`PricingStore` + API

**`components/pricing_store.py`**（branding_store 同款：原子写 mkstemp+replace、读容错回落、损坏文件不当机）：

```python
class PricingStore:
    def __init__(self, workspaces_dir: Path): ...
    # 全局表 <workspaces_dir>/pricing.json；工作区覆盖 <workspaces_dir>/<ws>/pricing.override.json
    def read_global(self) -> dict | None            # None=未创建（未接管）
    def write_global(self, currency: str, models: dict) -> None
    def clear_global(self) -> None
    def read_ws_override(self, ws: str) -> dict | None
    def write_ws_override(self, ws: str, currency: str, models: dict) -> None
    def clear_ws_override(self, ws: str) -> None    # 删文件（不存在幂等）
    def resolve_effective(self, ws: str | None = None) -> EffectivePricing
    @staticmethod
    def validate(currency: str, models: dict) -> None   # ValueError → 400

# EffectivePricing: {currency, models: [{model, prices{input,output,cache_read,cache_creation}, source}]}
# source ∈ "builtin" | "profile_env" | "global" | "workspace"（供前端来源徽章）
# resolve 按 §4.1 同一优先级合并；profile_env 层路径取 web 进程 os.environ 的 SUPERNOVA_PRICING_OVERRIDE
```

**校验规则**（`validate`）：
- `currency ∈ {"CNY", "USD"}`；`models` 非空 dict（工作区覆盖允许为空 = 等价清除，路由层直接拒，要求清除走 DELETE）。
- 模型 key 经 `normalize_model` 归一后不得为空、不得彼此重复（如 `glm-5.2[1m]` 与 `glm-5.2` 冲突 → 400）。
- 4 档价格为有限数且 ≥ 0；缺失键 → 400（不静默补 0，编辑器总是提交全 4 档）。

**API**（`api/pricing.py`，挂 `app.state.pricing_store`）：

| 端点 | 权限 | 语义 |
|---|---|---|
| `GET /api/pricing` | 全员 | `{currency, models:[{model, prices, source}], has_global_table, builtin_defaults}`；**全局视角**（`resolve_effective(ws=None)`，不含工作区层，source 无 "workspace"）；`builtin_defaults` = BUILTIN 表原文（供「恢复默认」） |
| `PUT /api/pricing` | admin | body `{currency, models:{model:{4档}}}`；校验 → 原子写全局表（**完整生效表快照**，保存即接管 profile env 层） |
| `DELETE /api/pricing` | admin | 删全局表，回落 profile env / 内置 |
| `GET /api/workspaces/{ws}/pricing` | workspace_member | `{override_exists, currency, models:[…source 含 workspace 层…], builtin_defaults}` |
| `PUT /api/workspaces/{ws}/pricing` | workspace_manager | 写 `<ws>/pricing.override.json` |
| `DELETE /api/workspaces/{ws}/pricing` | workspace_manager | 删覆盖文件，恢复继承全局 |

**工作区覆盖不走 env 文本段（关键决策）**：工作区覆盖的 SSOT = `pricing.override.json` 文件存在性，**不写** ws config 的 env 段。理由：env 文本框契约是「文本 = 完整定义」（PUT 未出现的键 = 清空，`ws_config.py` 头注释）——程序把 `SUPERNOVA_PRICING_OVERRIDE` 写进 env 段而 display_text 不同步的话，用户下次保存 env 文本会**静默清除**定价覆盖；同步 display_text 则把程序状态混进用户文本。改由 `scan_manager._resolve_env_overrides(ws)` 扩展：ws config env 段解析后，若 `<ws>/pricing.override.json` 存在则追加 `SUPERNOVA_PRICING_OVERRIDE=<该路径>`（**压过**用户手写该键——UI 覆盖存在即接管；想走手写 env 先清 UI 覆盖）。用户经 env 文本框手写该键的现状能力保留（无 UI 覆盖文件时照常生效）。

### 4.3 前端：`PricingEditor` 复用组件 + 两个挂载点

**`components/pricing/PricingEditor.tsx`**（受控组件，scope 决定保存目标）：

```tsx
interface PricingRow { model: string; prices: {input: number; output: number;
  cache_read: number; cache_creation: number}; source: Source; }
interface Props {
  scope: "global" | "workspace";
  currency: string;               // ¥ / $ 切换（全表单一币种）
  rows: PricingRow[];              // 生效表（含来源）
  builtinDefaults: Record<string, Prices>;   // 内置行「恢复默认」
  canEdit: boolean;                // admin（global）/ canEdit（workspace）
  onSave: (currency: string, models: Record<string, Prices>) => Promise<void>;
  onClear?: () => Promise<void>;   // DELETE（清除覆盖 / 回落）
  hasOverride?: boolean;           // workspace scope：当前是否已覆盖
}
```

- 表格列：模型 | 输入 | 缓存读取 | 缓存写入 | 输出（单位标注「本币 / 百万 token」）| 来源徽章 | 行操作。
- 来源徽章（信息即结构）：`内置`（muted）/ `环境`（outline）/ `全局`（default）/ `本工作区`（accent 变体）——一眼看清每个价来自哪层、被谁覆盖；非 canEdit 时只读展示。
- 行操作：内置行「恢复默认」（回填 builtin 值，仅编辑态）；非内置行「删除」；「新增模型」行（model id + 4 档价，id 提交前经与现有行重复校验，后端 400 兜底）。
- 数字输入：`inputMode="decimal"`，≥0 校验，非法值禁用保存并行内提示；脏状态提示 + 保存 / 重置。
- i18n：zh / en locales 补 `settings.pricing.*`、`wsSettings.pricing.*`（key 用 camelCase——kebab→camel 转换陷阱已知）。

**挂载点一：SettingsPage「模型定价」Section**（`Section` 先例 + `isAdmin`）：
- admin：可编辑（PUT）；「清除全局定价」按钮（DELETE，确认对话框）。
- 非 admin：只读生效表（同组件 canEdit=false）。
- 说明文案：生效范围（全部工作区的默认定价）、生效时机（**下一次计费起用新价，无需重启；已落盘的历史扫描成本不变**；进行中扫描的后续计费即用新价）。

**挂载点二：WsSettingsTab「定价」Card**（`canEdit = admin | manager` 先例）：
- 默认态：「继承全局定价」+ 生效表只读 + 来源徽章；`canEdit` 时显示「覆盖本工作区定价」按钮。
- 覆盖态：`PricingEditor` workspace scope（从当前生效表起步编辑）+「清除覆盖，恢复继承全局」（DELETE，确认）。
- 覆盖存在时卡片标题带「已覆盖」徽标；env 文本框手写 `SUPERNOVA_PRICING_OVERRIDE` 且无 UI 覆盖文件时，来源徽章以「环境」如实呈现（不隐藏现状能力）。

### 4.4 数据流：保存 → 生效链路

```
admin 改价保存 → PUT /api/pricing → 原子写 workspaces/pricing.json
worker 下一次 compute_cost → _pricing() 现读 pricing.json（SUPERNOVA_GLOBAL_PRICING 已在 worker env）→ 新价生效
```

- 无进程重启、无缓存失效问题（`_pricing()` 本就每次现读）。
- worker 与 web 共享 `workspaces_dir` 文件系统（同机部署），路径经 env 键解耦——core 不感知 web 的 workspaces_dir。
- 边界记录：worker 未经 web 启动（独立直跑 worker / CLI）时无 `SUPERNOVA_GLOBAL_PRICING` → 无全局层，回落 profile env 语义——部署上 worker 由 web spawn，此边界仅影响非标准部署。

## 5. 错误处理

- `pricing.json` / `pricing.override.json` 损坏：读容错回落（该层视为空 + warning 日志，`pricing.py` 现状容错语义一致）；前端 GET 附 `table_corrupt: true` → 显示「价目表文件损坏，请重新保存」横幅。
- 校验失败 → 400 带 detail（branding 先例：`ValueError` → `HTTPException(400)`）。
- 归一后模型 id 冲突 → 400（`normalize_model("glm-5.2[1m]") == normalize_model("glm-5.2")`）。
- 非权限写 → 403（`require_admin` / `workspace_manager` 依赖）。
- 删除不存在的表 / 覆盖 → 幂等成功（200）。

## 6. 测试策略（TDD 先红后绿）

**core（`packages/core/tests/agents/test_pricing_layered_merge.py`）**：
1. 未设 GLOBAL 键 + 无 ws 覆盖 → 与现状等价（BUILTIN ∪ process override，含旧 flat schema）。
2. GLOBAL 层压过 process 层（接管语义：同模型取 GLOBAL 价）。
3. ws 覆盖层压过 GLOBAL 层。
4. 币种 = 最高优先非空层的 currency（全部层空 → CNY）。
5. `ws_override_get`：覆盖层有键 / 无键 / 无覆盖层三态。
6. GLOBAL 路径文件损坏 → 该层空 + 不抛。

**web（`packages/web/tests/`）**：
7. `test_pricing_store.py`：原子写、校验规则全集（币种枚举 / 归一冲突 / 负价 / 缺档）、读容错、`resolve_effective` 来源标注四态、清除幂等。
8. `test_api_pricing.py`：GET 全员可看、PUT/DELETE 非 admin 403、校验失败 400、GET 视图字段（source / builtin_defaults / has_global_table）。
9. `test_api_ws_pricing.py`：member GET / manager PUT+DELETE / 覆盖文件落盘与删除、`_resolve_env_overrides` 注入（覆盖文件存在 → env_overrides 带该路径并压过 config env 手写键；不存在 → 不注入）。

**前端（`packages/web/frontend/src/`）**：
10. `PricingEditor.test.tsx`：渲染来源徽章、编辑脏状态、非法值禁保存、恢复默认、新增模型重复 id 拒绝、只读态。
11. SettingsPage / WsSettingsTab 集成：admin 编辑 vs 非 admin 只读、覆盖态切换、清除覆盖确认流。

## 7. 兼容与迁移

- **CLI 直跑**：无任何新 env 键 → 零行为变化（锚点 1 守护）。
- **现有部署**（本机 profile env 显式 override）：首次在界面保存全局表即快照收编（GET 展示的就是合并生效表，admin 看到什么存什么，无信息丢失）；不保存则现状不变。
- **`SCAN_ENV_KEYS` 白名单不动**：工作区 env 文本框手写 `SUPERNOVA_PRICING_OVERRIDE` 能力保留（UI 覆盖文件存在时被压过，来源徽章可见）。
- **CLAUDE.md §4 收尾同步**：落地后补一段「全局价目表 web 管理」（优先级链 + 接管语义）。

## 8. 否决的备选（记录理由）

- **profile env 优先于界面**：本机 env 层已覆盖几乎所有在用模型，界面将形同虚设；与「一个界面调整定价」的核心诉求直接冲突。
- **工作区 delta 覆盖存储**（只存与全局差异）：存储 / 合并 / 展示三处复杂度，收益仅语义精细；全表快照 + 来源徽章已满足可理解性。
- **工作区定价写 ws config env 段**：与「env 文本 = 完整定义」回显契约打架（程序写键不同步 display_text → 用户下次保存 env 文本静默清除定价覆盖；同步则污染用户文本）。改走独立覆盖文件 + scan_manager 注入。
- **pricing.py 探测固定默认路径**：core 不应感知 web 的 workspaces_dir；经 env 键解耦，CLI / core 可独立测试。

## 9. Task 切分建议（供 writing-plans）

1. **core 分层合并**：`scan_env.ws_override_get` + `pricing.py` 拆层（锚点 1-6，TDD）。
2. **PricingStore**：原子写 / 校验 / resolve_effective（锚点 7）。
3. **全局 API + 挂载**：`api/pricing.py` 全局三端点、`create_app` 挂 store + startup 注入 `SUPERNOVA_GLOBAL_PRICING`（锚点 8）。
4. **工作区 API + 注入**：ws 三端点 + `_resolve_env_overrides` 扩展（锚点 9）。
5. **PricingEditor 组件**：受控编辑器 + 来源徽章（锚点 10）。
6. **挂载点 + i18n**：SettingsPage Section、WsSettingsTab Card、zh/en 文案（锚点 11）+ CLAUDE.md §4 同步。
