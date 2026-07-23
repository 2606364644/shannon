# PoC 生成分层确定性化 + 可靠性加固设计

> 日期：2026-07-22　分支：`feat/fork-py`　类型：**bug 修复 + 性能/可靠性重构**（报告增强层，黑白盒通用）
>
> 上游 spec：`docs/superpowers/specs/2026-07-02-exploitable-poc-generation-design.md`（PoC 功能原始设计）。本 spec 不推翻上游，仅修正其落地实现中暴露的三类问题。

---

## 1. 背景与触发事件

2026-07-22 sentinel_dashboard 白盒扫描，PoC 阶段使整个 workflow 失败（5 小时、$20）：

```
(1/50) INJ-VULN-01  0ms        ← LLM 轨，模板命中
(2/50) INJ-VULN-08  0ms        ← LLM 轨，模板命中
(3/50) INJ-GN-01    1m37s      ← GitNexus 轨，逐条 LLM
...
(8/50) INJ-GN-07    ~3min
→ 20min timeout → retry 从 (1/50) 重来 → 再超时 → 再重来
→ 3 次 retry 耗尽 → ActivityError: Activity task timed out → workflow FAILED
```

PoC activity 配置（黑白盒相同）：`start_to_close_timeout=20min` + `POC_RETRY(maximum_attempts=3)`。三次重启实测耗时 `20.2 / 20.3 / 20.0 min`，与 20min timeout 精确吻合。

## 2. 根因（三个失配 + 一个附加 bug）

### 2.1 失配 ①：timeout 配置 vs 实际工作量（数量级错配）

PoC 对 N 个 externally_exploitable 漏洞**串行**处理，每条 GitNexus 轨漏洞走一次独立 `llm_fill_gap` → `run_claude_prompt`（`max_turns=10`），GLM 对 HTTP schema 结构化输出反复不合规、多轮空转，**每条 1-5min**。`N × ~3min ≈ 150min`，而 timeout 只有 `20min` → 单次结构上跑不完，每次都卡在第 8 个。

retry（`POC_RETRY`）从第 1 条重来（**无 checkpoint**），3 次耗尽 → `ActivityError`。

### 2.2 失配 ②：原设计意图（上游 §5.3）与现实背离

上游 spec 明写：*"injection / xss / ssrf 有 witness_payload → **默认纯模板，不触发 LLM**"*。即 PoC 本应像报告一样尽量确定性模板化。

但 GitNexus 轨漏洞**天生缺两样**（实测 sentinel_dashboard injection queue）：

| 字段 | LLM 轨 INJ-VULN-* | GitNexus 轨 INJ-GN-* |
|---|---|---|
| `path` | `POST /cluster/config/modify_single → …`（含 HTTP 路由）✅ | `payload -> …Controller.java:method`（数据流文本）❌ |
| `witness_payload` | Jackson gadget payload ✅ | **`None`**（即使 `verdict=vulnerable`）❌ |
| `endpoint` / `source_endpoint` | — | `None` ❌ |
| `source` | `@RequestBody String payload at …:71` | `payload (…Controller.java:method:70)`（代码位置） |

→ `derive_method_path` 从 GitNexus 的 source/path 提取不出 HTTP method/path，`build_template_spec` 返回 None → **逐条被迫走 LLM 兜底**。PoC 从「少数走 LLM」退化成「绝大多数走 LLM」，量级失控。

**witness_payload 为何是 None（深挖根因）**：实测 sentinel_dashboard 全部 14 条 GitNexus INJ-GN 的 `mismatch_reason='llm chain-verdict pass returned unparseable output; needs review'`、`confidence='low'`——即**全部命中 `chain_verdict.py:290-293` 的 unparseable 分支**：判定 LLM 经 `output_format=CHAIN_VERDICT_SCHEMA` 调用后输出无法被 `_extract_json_payload` 解析（GLM 结构化输出不合规），保守标 `verdict=vulnerable`（OR-friendly 不漏报）但 `witness_payload=None`。所以 GitNexus 轨的 witness 缺失是 **verdict 层 LLM 不合规的表征**，非数据天生没有。**改 verdict 链属 CLAUDE.md 铁律禁区，本 spec 不碰**；PoC 层把 witness 当「缺口」与 route 一起补（§4.4），是正确的分层——即便 verdict 层修好产出 witness，route 仍缺，gap-fill 仍需。

**跨轨复用空间有限**（按 controller 类名实测）：GitNexus externally_exploitable injection 14 条中，仅 4 条能匹配到 LLM 轨同 controller（可继承 route+witness），**10 条是 GitNexus-only**（双轨的意义——GitNexus 找到 LLM 轨漏的，但它是代码级发现，天生不带 HTTP 形状）。

### 2.3 失配 ③：上游 §8 非阻塞契约被 Temporal timeout 击穿（回归）

上游 §8 承诺：*"PoC activity 抛任何异常 → try/except 兜底，只 log，主报告不受影响"*。PoC 本就是「报告增强、非关键路径」。

实现中 `generate_poc_report` activity 内部确有 `except Exception`（`whitebox/.../activities.py:1182`），但 **Temporal 的 `start_to_close_timeout` 是 runtime 层强制 cancel，不抛 Python 异常**，该 try/except 抓不到。后果：本该「绝不阻塞主流程」的 PoC，反而把整个 workflow 拖垮——对设计核心承诺的直接回归。

### 2.4 附加 bug：`_spec_from_llm_guess` 的 str items 崩溃

`poc_generator.py:527-528`：

```python
query={k: str(v) for k, v in (guess.get("query") or {}).items()},
headers={k: str(v) for k, v in (guess.get("headers") or {}).items()},
```

LLM（GLM 无 strict）返回 str 类型 query/headers 时 `.items()` 崩。`_coerce_request_body`（commit `cc4d7603`）只归一化了 body，query/headers 漏网。INJ-GN-08 首跑报 `'str' object has no attribute 'items'`——单条被 per-item try/except 吞，丢一个 PoC，非失败主因。

## 3. 目标 / 非目标

### 目标

- **G1（分层确定性化）**：模板优先组装骨架，LLM 只补真空缺口（HTTP 路由 + witness_payload），且按 controller 文件分组批量调用——把 LLM 调用从「逐条 ~48 次」降到「按文件个位数」。
- **G2（§8 契约硬化）**：PoC 任何故障（含 Temporal timeout）**绝不**让 workflow FAILED——workflow 层兜底 + 增量 checkpoint，retry 能续跑而非从零重来。
- **G3（健壮性）**：修复 str items 崩溃；LLM 不可用/失败 → 骨架降级 + 标注，不阻塞。
- **G4（对齐报告的确定性哲学）**：能从已有数据确定性拼出的，零 LLM（复刻 `findings_renderer` 9ms 渲染 59 条的成功模式）。

### 非目标

- **不改双轨 / verdict / merger**（守 CLAUDE.md 铁律）：本 spec 只动报告增强层（`poc_generator.py` + 两轨 workflow 的 activity 调用包裹 + retry/timeout 配置），不碰确定性 taint 链路、不改 chain_verdict prompt、不修 merger 的跨轨 dedup（§2.2 提到的「两轨 location 格式不同导致不 dedup」是已知但属另一议题，本 spec 不处理）。
- **不做上游确定性层富化**（不在 builder 记录 `@RequestMapping` 路由 / 强制 chain_verdict 输出 witness）——那是更大、更高危的独立 epic，本 spec 用「LLM 只补缺口」替代。
- **不改 PoC 产物格式**（curl + Burp 双格式、置信度三档、md 布局均不变）。
- **不做 PoC 自动重放验证**（同上游 §1 非目标）。

## 4. 核心设计：分层确定性化 + 按 controller 文件分组补缺

### 4.1 设计原则

复刻报告的成功模式：**确定性优先，LLM 最小化，且 LLM 只补真空字段、模板做最终组装**。

**适用范围**：本分层流程仅适用于 **inj / xss / ssrf**（这是出问题的主体，sentinel_dashboard 48 条 LLM 全在此）。**authz**（上游 §4.4 成对请求，`_build_authz_pair` 模板）与 **auth**（量小、上游 §5.3 本就默认走 LLM）**保持既有 per-item 路径不变**——它们不是本次性能问题来源，且 authz 的成对结构、auth 的 hypothesis 推断不适合按 controller 文件分组。

当前实现是「模板 OR LLM」二选一（模板拼不出就整条丢 LLM 重建 spec）。新设计（仅 inj/xss/ssrf）改为**分层组装**：

```
每个 externally_exploitable 漏洞
  ① 确定性提取能拿到的字段（参数名 / 漏洞类 / 文件+方法 / 置信度 / 认证态）
  ② 路由 + witness 都齐？ → 模板直接组装 curl/Burp，结束 [0ms]
  ③ 缺路由/witness → 归入「待补」桶，按 controller 文件分组
按文件批量补缺:
  ④ 每个文件组 1 次 LLM 调用（cap ≤ 8），只返回 {http_method, route_path, witness_payload}×N
  ⑤ 模板用补回字段做最终组装 → curl/Burp
  ⑥ LLM 失败 → 该条退骨架 + 标注（不阻塞）
```

### 4.2 确定性字段提取规则（0ms）

GitNexus 轨 `source` 格式实测固定（由 `_source_text(chain) = f"{source_param} ({entry_point_id})"` 产生）：

```
payload (src/main/java/.../ClusterConfigController.java:apiModifyClusterConfig:70)
└─┬─┘ └────────── file ──────────┘└──── method ────┘ └line┘
param                                                            (source/sink_call 均带 file:method)
```

新增确定性提取函数（纯函数，可单测）：

```python
_GN_SOURCE_RE = re.compile(r"^(\S+)\s*\((.+?):([^:/]+):(\d+)\)\s*$")

def extract_gn_location(source: str | None) -> tuple[str | None, str | None, str | None]:
    """从 GitNexus source 提取 (param_name, file_path, method)。

    'payload (…/Controller.java:method:70)' → ('payload', '…/Controller.java', 'method')
    非 GitNexus 格式（如 LLM 轨）→ (None, None, None)
    """
```

由确定性字段拼出的**部分 spec**（`PartialSpec`，新中间结构）：

| 字段 | 来源 | 是否需 LLM |
|---|---|---|
| `vuln_class` | 文件名前缀 | 否 |
| `param_name` | `extract_gn_location` 首段 / `extract_param_name(source)` | 否 |
| `placement`（inj→query、xss→query、ssrf→body） | `vuln_class` 映射 | 否 |
| `controller_file` / `method` | `extract_gn_location` | 否（用于分组） |
| `confidence_band` | verdict（+ 黑盒 accepted_ids） | 否 |
| `auth_state` | recon endpoints（按 route 查） | 否，但**需 route 作输入**：route 已知时直接查；route 缺时在 gap-fill 补回 route 后再查（见 §4.5） |
| **`http_method`** | —— | **缺时需 LLM** |
| **`route_path`** | —— | **缺时需 LLM** |
| **`witness_payload`** | —— | **缺时需 LLM** |

**判定缺口**：`http_method`+`route_path` 经 `derive_method_path`（既有，复用）从 `path`/`endpoint` 提取不到，或 `witness_payload` 为空 → 进待补桶。

**LLM 轨多半无缺口**（`path` 含 `METHOD /route` + witness 非空）→ 直接模板，0ms（现状已对，保留）。

### 4.3 按 controller 文件分组（回应「一类漏洞太多会分析错」）

不按漏洞类分批（一类可能几十条，单次 LLM 易错乱），而**按 controller 文件聚合**：

- 分组 key = `controller_file`（从 `extract_gn_location` 取；提取不到的用 fallback key `"__unknown__"`）
- 每组发**一次** LLM 调用，prompt 形如：「读 `<file>`，对以下 handler 方法，返回各自的 HTTP method/route/witness」
- **每组 cap ≤ 8 条**（`SUPERNOVA_POC_GROUP_CAP`，默认 8）：超出则按 cap 拆成多次调用
- LLM 拿到 repo 访问（`repo_path`，既有），读单文件聚焦、不易错（Spring `@PostMapping` / Express `router.get` / Flask `@app.route` 都一眼可见）

**收益**：sentinel_dashboard 10 条 GitNexus-only injection 若分布在 3 个 controller 文件 → **3 次轻量调用**（而非 10 次逐条、也非 1 次巨批）；同文件 N 条只读 1 次（现状逐条每条重读同文件，浪费）。

### 4.4 LLM gap-fill schema（最小输出）

LLM 每组只返回补缺字段，不返回整个 spec（GLM 结构化输出压力最小）：

```python
GAPFILL_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ID": {"type": "string"},
                    "http_method": {"enum": ["GET","POST","PUT","DELETE","PATCH"]},
                    "route_path": {"type": ["string","null"]},
                    "witness_payload": {"type": ["string","null"]}
                },
                "required": ["ID"]
            }
        }
    },
    "required": ["items"]
}
```

- 调用仍走 `run_claude_prompt`（`structured_output_schema=GAPFILL_OUTPUT_SCHEMA`），`max_turns` 复用既有 `SUPERNOVA_POC_MAX_TURNS`（默认 10）
- prompt 含：文件路径、每条 `{ID, param_name, method, vuln_class, slot_type, evidence_chain 片段}`、recon 端点表（补 method 旁证）
- 冲突仲裁（同上游 §5.3）：条目既有 route/witness 永远优先，LLM 只填真空

### 4.5 最终组装（模板）

`_assemble(partial, gapfill)` 把确定性部分 + 补缺字段合成 `HttpRequestSpec`，再走既有 `to_curl` / `to_burp_raw`：

- method/path：优先既有字段，缺则用 gapfill
- query/body：按 `vuln_class` placement 把 `witness_payload` 填入 `param_name`（复用既有 `build_template_spec` 的 injection/xss/ssrf 分支逻辑）
- headers：auth 头由 recon 中间件决定（既有 `auth_header`）

> **重构落点**：当前 `_build_entry` 对 inj/xss/ssrf 的 `build_template_spec`（返回 None 触发整条 LLM）+ `llm_fill_gap`（返回整 spec）+ `_spec_from_llm_guess`，重构为 `extract_deterministic` →（缺则）按文件分组 `llm_fill_gaps` → `assemble`（含补回 route 后重查 recon 得 `auth_state`）。**authz 的 `_build_authz_pair`、auth 的 per-item `llm_fill_gap` 保持既有路径不动**。

## 5. 可靠性加固（用户选 C = A + B 叠加）

### 5.1 Fix A：workflow 层兜底（§8 契约硬化）

在两轨 workflow 给 PoC activity 调用包一层 `try/except`，捕获 `temporalio.exceptions.ActivityError`（含 timeout）：

```python
# packages/whitebox/src/supernova_whitebox/pipeline/workflows.py:602 附近
self._state.current_agent = "generate-poc-report"
try:
    await workflow.execute_activity(
        activities.generate_poc_report, act_input,
        start_to_close_timeout=timedelta(minutes=20),
        retry_policy=retry_for("poc"),
    )
except Exception:  # noqa: BLE001 — PoC 是非关键报告增强，任何失败（含 ActivityError/timeout）绝不阻塞主流程
    pass  # §8 契约：主报告已落盘，PoC 缺失只降级不 fail workflow
finally:
    self._state.current_agent = None
```

- 黑盒 `packages/blackbox/src/supernova_blackbox/pipeline/workflows.py:411` 同样包裹
- **语义**：PoC timeout/失败从此**不可能**让 workflow FAILED——这是对上游 §8 最直接的兑现（不依赖 activity 内那层抓不到 timeout 的 `except Exception`）
- activity 内部 per-item `try/except`（既有）保留：单条失败仍只降级该条

### 5.2 Fix B：增量 checkpoint / 断点续传

新增 sidecar `deliverables_dir / ".poc_checkpoint.json"`，让 retry 从断点续跑而非从零：

```jsonc
{
  "version": 1,
  "track": "whitebox",
  "completed": {
    "INJ-VULN-01": { "vuln_class": "injection", "spec": { /* 序列化 HttpRequestSpec */ } },
    "INJ-GN-03":   { "vuln_class": "injection", "spec": { ... } }
  }
}
```

`generate()` 流程改造：

1. **启动**：读 checkpoint，`done_ids = set(completed.keys())`
2. **主循环**：跳过 `ID ∈ done_ids` 的项（直接复用存储的 spec 进 `entries`）；每完成一项，`completed[ID] = {...}` 并写盘 checkpoint（原子写：写临时文件 + `os.replace`）
3. **分组补缺**：只对未完成的项分组调 LLM；补完逐条写盘
4. **收尾**：从全量 entries（checkpoint 复用 + 本轮新增）渲染最终 md → `exploitable_poc_collection.md`；**保留** checkpoint 文件（幂等：下次完整重跑会覆盖；`scripts/generate_poc.py` 独立入口可加 `--fresh` 清 checkpoint）

**收益**：即使 Fix A 兜底让 workflow 不 fail，PoC 本身也能在 retry 中逐步推进、最终产出完整集合（而非每次重跑前 8 条）。checkpoint 是 sidecar（点开头），不污染 deliverables 正式产物列表。

### 5.3 timeout 配置

分层设计后 LLM 调用降至个位数，**20min 已充裕，维持不变**（不再需要调大）。POC_RETRY（max 3）配合 checkpoint，最坏 `3 × 20min` 也能把个位数 LLM 调用跑完。若实测仍紧张，env `SUPERNOVA_POC_ACTIVITY_TIMEOUT_MIN` 可调（默认 20）。

## 6. str items bug 修复（§2.4）

新增 `_coerce_str_dict`（仿 `_coerce_request_body`），在 `_spec_from_llm_guess` 归一化 query/headers：

```python
def _coerce_str_dict(raw: Any) -> dict[str, str]:
    """LLM 结构化输出不可靠：query/headers 可能返回 str 而非 dict。
    归一化为 dict 以守 spec 类型不变量，避免 'str' object has no attribute 'items'。"""
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        # 尝试 JSON 对象
        if s[:1] in ("{", "["):
            try:
                p = json.loads(s)
                return {str(k): str(v) for k, v in p.items()} if isinstance(p, dict) else {}
            except Exception:
                return {}
        # 尝试 query string（k=v&k2=v2）
        out: dict[str, str] = {}
        for pair in s.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                out[k.strip()] = v.strip()
        return out
    return {}
```

`_spec_from_llm_guess` 改用 `_coerce_str_dict(guess.get("query"))` / `_coerce_str_dict(guess.get("headers"))`。

## 7. 降级路径（错误处理矩阵）

| 故障 | 行为 |
|---|---|
| workflow：PoC activity timeout / ActivityError / 任意异常（Fix A） | 兜底吞掉，workflow 照常完成；主报告已落盘 |
| retry 触发（Fix B） | 读 checkpoint，跳过已完成项，续跑未完成项 |
| 单条 LLM gap-fill 返回缺字段 / 整组失败 | 该条退骨架（method=GET/path=已知或`/`+note「需手工补全 body/参数」），不阻塞其余 |
| LLM 整体不可用（stub/超时） | 全部 GitNexus-only 退骨架 + 标注；LLM 轨 + 有缺口兄弟仍模板产出 |
| checkpoint 损坏 / 不可写 | 当作无 checkpoint 从头跑（降级，不报错） |
| queue 缺失 / JSON 损坏 | 复用既有 `parse_lenient` warnings，不报错 |
| 过滤后 0 条 | 既有空表兜底 md |

## 8. 测试策略

对齐既有 `test_poc_generator.py` 模式，只跑改动相关文件（CLAUDE.md §3 测试陷阱）。

### 8.1 单元测试（`packages/core/tests/test_poc_generator.py` 扩充）

1. **`extract_gn_location`**：GitNexus source 各形态（Java/TS/Python handler、缺行号、非 GN 格式）→ 正确提取 param/file/method 或全 None
2. **分组逻辑**：N 条 GN 漏洞按 controller_file 分组；cap 拆分（>cap 拆多次）；`__unknown__` fallback
3. **`_assemble`**：确定性 partial + gapfill dict → 正确 HttpRequestSpec（inj→query、ssrf→body、auth 头）
4. **`_coerce_str_dict`**：dict / str(JSON) / str(query) / str(乱) / None → dict；不抛 `'str' object has no attribute 'items'`
5. **模板优先路径**：LLM 轨 fixture（path 含路由 + witness）→ **不触发** LLM

### 8.2 集成测试

- 构造 fixture deliverables（LLM 轨 queue + GitNexus 轨 queue + recon 端点片段），跑 `generate`：
  - LLM 轨条目 0ms 模板命中
  - GitNexus-only 条目经分组 → mock LLM 返回 route/witness → 组装正确
  - 最终 md 含模板产 + gapfill 产 + 骨架降级 三类

### 8.3 LLM 边界测试（mock `run_claude_prompt`）

- GitNexus-only 按文件分组，同文件多条只 1 次 LLM 调用（断言调用次数 = 文件组数，非条目数）
- cap 拆分：单文件 >cap → 多次调用
- LLM 抛错 / 返回缺字段 → 对应条目降级骨架，不阻塞
- LLM 返回 str 类型 query/headers → 不崩（回归 §2.4）

### 8.4 checkpoint 测试

- 首轮跑完 → `.poc_checkpoint.json` 含全部 completed
- 模拟「首轮跑到一半」的 checkpoint → 二轮跳过已完成、只补未完成
- checkpoint 损坏 → 当作无 checkpoint，从头跑不报错

### 8.5 可靠性测试（Fix A）

- workflow 层：mock `execute_activity(generate_poc_report)` 抛 `ActivityError` → workflow 仍 `COMPLETED`（不 FAILED）

## 9. 关键不变量（守 CLAUDE.md 铁律）

- **不改双轨独立性 / verdict / merger**：本 spec 的 LLM 调用属**报告渲染层**（对成型漏洞补 HTTP 形态），用 `run_claude_prompt` 单次轻量调用，与 `chain_verdict` 同档；不喂确定性 hints 给 vuln 判定轨；不碰 merger dedup。
- **不覆写 `externally_exploitable`**：只读过滤。
- **§8 失败隔离硬化**：PoC 任何故障（含 timeout）不阻塞主报告（Fix A 保证）。
- **真实凭证不持久化**：上游 §4.3 不变（checkpoint 只存 HttpRequestSpec 形态，凭证仍占位符）。
- **产物格式不变**：curl + Burp 双格式、置信度三档、md 布局、文件名 `exploitable_poc_collection.md` 均不变。

## 10. 风险与开放问题

- **R1（参数名提取覆盖率）**：`extract_gn_location` 依赖 GitNexus source 格式稳定（`param (file:method:line)`）。格式漂移 → 提取 None → 该条仍走 LLM 兜底（降级，不阻塞）。非 Java（TS/Python）handler 文件路径同样可提取（路径 + 方法 token 通用）。
- **R2（LLM 读文件能力）**：gap-fill 依赖 LLM 能读 controller 文件推断路由（`@PostMapping` / `router.get` / `@app.route`）。GLM 经 `run_claude_prompt` 有 repo 访问，已验证可读码追链（`validate_openai_task_probe.py`）。框架无注解路由（纯约定）时 → LLM 给不出 route → 该条降级骨架。
- **R3（checkpoint 与并发）**：本设计 PoC 内部仍串行（分组后顺序调 LLM），checkpoint 无并发写竞争。未来若上并发需加锁。
- **R4（cap 取值）**：默认 cap=8 经验值。过小 → 调用次数多；过大 → 单次易错。env 可调，留 plan 阶段据实测微调。
- **O1（GitNexus 轨 dedup）**：§2.2 提到 merger 因两轨 location 格式不同不 dedup（INJ-GN 与 INJ-VULN 并存）。本 spec 不处理；未来若修 merger 跨轨 dedup，PoC 的 GitNexus-only 数量会下降，gap-fill 调用更少——但属独立议题。
- **R5（GLM 结构化输出可靠性，重要）**：本设计的 gap-fill 与 chain_verdict 用**同一套** `run_claude_prompt` + `output_format`（structured_output 优先、文本兜底抽 JSON）infra、同一 GLM 模型。实测 chain_verdict 对 sentinel_dashboard 14 条 GitNexus 候选**全部 unparseable**（见 §2.2 根因）——即 GLM 对该 schema 的合规率极低。PoC gap-fill 的 prompt 更简单聚焦（读单文件、返回 route+witness，非判定整条 taint 链），合规率应优于 verdict 层，但**不保证**。缓解：①新 runner 文本兜底抽 JSON；②失败降级骨架（§7）不崩、不阻塞；③Fix A/B 兑现「即使大量降级也不拖垮 workflow、retry 能续跑」。**预期**：若 GLM 持续不合规，gap-fill 会大量降级骨架（低价值但安全）。是否值得对 GAPFILL_OUTPUT_SCHEMA 做更激进的简化（如分两次单字段调用）留实测后定。
- **O2（上游确定性层富化）**：§3 非目标。若未来在 GitNexus builder 记录 `@RequestMapping` 路由 + 修好 chain_verdict 的 unparseable（让判定 LLM 可靠产出 witness），则 GitNexus 轨全可模板化、gap-fill 趋零——独立 epic，本 spec 用 LLM 补缺口替代。**chain_verdict unparseable 是更优先的独立议题**：它不止致 witness 缺失，更使 GitNexus 轨 verdict 整体不可信（全 conservative vulnerable+low），值得单独立项。
