# authz 双轨 ee 误判修复 + Horizontal 端点去重 设计

> 日期：2026-08-04 ｜ 状态：Design ｜ 分支：feat/fork-py
> 触发场景：白盒报告 `delivery-20260804-024910`——authz 漏洞"有的有 PoC、有的没有"，且 `AUTHZ-VULN-xx` 与 `AUTHZ-GN-EXPLORE-xx` 同端点重复出现。

---

## 1. 问题现象

白盒报告页中 authz 漏洞：

1. **PoC 缺失不一致**：部分 authz 漏洞卡片带「复现 PoC」，部分没有。PoC 仅挂在 `AUTHZ-GN-EXPLORE-*`（GitNexus 轨）上，`AUTHZ-VULN-*`（LLM 轨）全部无 PoC。
2. **重复**：同一端点（如 `GET /api/order/detail`）同时出现 `AUTHZ-VULN-03` 与 `AUTHZ-GN-EXPLORE-03` 两张卡片，又重复又乱。

## 2. 根因分析（已用真机数据验证）

### 2.1 PoC 缺失 ← `externally_exploitable`（ee）误判

PoC 生成唯一过滤条件是 `externally_exploitable == True`（`packages/core/src/supernova_core/services/poc_generator.py:906-908`）。ee 是**网络可达性标签**（CLAUDE.md 铁律：true=公网可达 / false=内部或跨服务），与"是否需要认证"无关。

但 LLM 轨 authz agent 把 ee **当成了 `authentication_required` 的反面**。铁证——`authz_llm_queue.json` 20 条漏洞，ee 与 auth_req 完全反相关：

| ee | auth_req | 条数 |
|----|----------|------|
| False | True | 19 |
| True | False | 1（`AUTHZ-VULN-02`，匿名可达） |

即 LLM 逻辑是"需要登录 → 不可外部利用 → ee=False"。

**ee 误判的根因：authz/auth prompt 从未定义 ee 语义。**
- `prompts/vuln-authz.txt:147-164` exploitation_queue_format 只列 `"externally_exploitable": true | false`，零定义。
- `prompts/vuln-auth.txt:107` 同样只有字段、无定义。
- `prompts/authz_gitnexus_judge.txt`/`authz_gitnexus_explore.txt` 也只列字段。
- 对比 `prompts/vuln-injection.txt:185-188` 明确定义了 ee=可达性标签——所以 inj/xss/ssrf 的 ee 判得对、authz/auth 全错。

LLM 在无定义时凭英文字面 "externally exploitable" 猜语义，猜成"要不要认证"。

### 2.2 重复 ← 双轨合并未按端点去重

authz 永远双轨（CLAUDE.md：`SUPERNOVA_LLM_TRACK_ENABLED=0` 也保留 authz LLM，因 GitNexus 只做 IDOR、做不了 Vertical/Context/系统性授权缺陷）：
- LLM 轨（`authz-vuln` agent）跑全方法论（Vertical + Horizontal/IDOR + Context），产 `AUTHZ-VULN-*`
- GitNexus 轨（`run_authz_gitnexus_judge`）专做 IDOR，产 `AUTHZ-GN-EXPLORE-*`

两轨在 **Horizontal/IDOR 子类天然重叠**（LLM 也覆盖 IDOR）。重叠应被 `dual_track_merger.merge_dual_track_queues` 合并成 `merge_source="both"`，但实际未合并——

`_finding_key`（`dual_track_merger.py:27-31`）的 key = `(vulnerability_type, 全部 location 字段组合, 全部 sink 字段组合)`。两轨对同一 IDOR 的 `vulnerable_code_location` 表述不同（LLM 指 service 层 `order.ts:296`，GitNexus 指 controller+service `controller/order.ts:54 + service/order.ts:296`），任一 location 字段差异即令整体 key 不同 → 各落 `llm-only`/`gitnexus-only`，未合并。两轨 `endpoint` 字段反而完全一致（`GET /api/order/detail` 等 6 个）。

注：Vertical/Context（如 `AUTHZ-VULN-01` 系统性 `/check/` 旁路）只 LLM 能产、不重复，是双轨互补价值，不在去重范围。

## 3. 设计目标

- **治本**：让 authz/auth 的 LLM 正确判 ee（语义定义），所有 authz 漏洞（含只 LLM 轨有的 `AUTHZ-VULN-01`）正确产 PoC。
- **治标兜底**：合并层双轨 ee 取 OR，防 LLM 判定波动。
- **去重**：Horizontal/IDOR 按端点合并，消除重复卡片。
- **不丢召回**：Vertical/Context/单轨独有项原样保留；inj/xss/ssrf 合并行为零变化。
- **不动旧报告**：仅改代码，旧报告由用户自行重跑。

## 4. 设计

### A. 治本——authz/auth prompt 补 ee 语义定义

在以下 prompt 的 exploitation_queue_format / 输出字段说明处，补 ee 语义段（措辞见下），统一两轨口径：

- `prompts/vuln-authz.txt`（:153 字段后）
- `prompts/vuln-auth.txt`（:107 字段后）
- `prompts/authz_gitnexus_judge.txt`（:27、:42 区域）
- `prompts/authz_gitnexus_explore.txt`（:32 区域）

**ee 语义定义措辞**（参照 `vuln-injection.txt:185-188`，适配 authz 语境）：

> `externally_exploitable` 是**网络可达性标签**（reachability tag），不是认证要求、不是准入门槛：
> - `true` = 该端点对公网暴露（经 nginx/网关对外可达），**哪怕业务上需要登录凭证**——持有任意有效凭证（或借助认证缺陷取得凭证）的外部攻击者可触达。
> - `false` = 仅内部网络/跨服务可达，外部攻击者无法从公网触达该端点。
> - **需要登录 ≠ `false`**。`authentication_required` 与网络可达性是两个独立维度：一个需要登录的公网 API 仍是 `true`。授权类漏洞常依赖有效会话，若端点本身对外暴露，应判 `true`。

`pipeline-testing/` 下的测试副本 prompt 同步（保持与生产 prompt 一致，避免测试漂移）。

### B. 治标兜底——合并层 both 分支 ee 取 OR

`packages/core/src/supernova_core/code_index/dual_track_merger.py`：

- `_clone_with_merge_fields`（:41-62）增加可选参数 `externally_exploitable_override: bool | None = None`；非 None 时 `data["externally_exploitable"] = externally_exploitable_override`。
- `merge_dual_track_queues` both 分支（:99-112）：传 `externally_exploitable_override = bool(llm_ee) or bool(gn_ee)`。
- `llm-only`/`gitnexus-only` 分支不传（保持各自 base 的 ee，行为不变）。

### C. 去重——Horizontal 用端点 key

同文件 `_finding_key`（:27-31）：

- 当 `vulnerability_type == "Horizontal"` 且 `endpoint` 非空时，key = `("Horizontal", 规范化端点)`；规范化 = HTTP method 大写 + path 去 trailing slash / query string。
- 其余情况（Vertical / Context_Workflow / inj / xss / ssrf，或 Horizontal 但 endpoint 缺失）保持现有严格 key（type+location+sink），避免误合并不同问题。

## 5. 铁律与不变量论证

1. **ee OR 不违反"ee 不被 verdict 覆写"铁律**（`dual_track_merger.py:52-57` / CLAUDE.md）：OR 的两个来源是两轨各自的 `externally_exploitable` 字段（可达性），不是 `verdict`（漏洞成立性）。语义是"同一漏洞不应有两套矛盾的可达性结论，偏安全取 True"，仍是可达性层面，不引入 verdict→ee 推导。
2. **prompt 加 ee 定义不违反"LLM 轨源只从 recon+grep 派生"铁律**（CLAUDE.md）：本次仅澄清结构化输出字段语义，不引入确定性层产物、不 `@include` 确定性 hints。`test_static_dataflow_hints_decoupling.py` 不变量不受影响。
3. **合并器"do NOT overwrite externally_exploitable"语义细化**：单轨分支仍不覆写；仅 both 分支（双轨印证同一漏洞）取 OR。在代码注释中更新该铁律的表述，明确"双轨同漏洞 ee 取 OR 是允许的一致化"。

## 6. 测试策略

### 6.1 prompt 不变量测试（新增）
- 断言 `vuln-authz.txt`、`vuln-auth.txt` 含 ee 可达性定义关键词（如"网络可达性"/"reachability"与"需要登录"共现），防止回归到"零定义"。仿 `tests/prompts/test_static_dataflow_hints_decoupling.py` 模式。

### 6.2 dual_track_merger 单测（新增/扩充）
- Horizontal 同端点两轨（location 不同）→ 合并为 1 条 `merge_source="both"`、`confidence="high"`。
- both 分支 ee OR：llm.ee=False + gn.ee=True → 合并 ee=True。
- both 分支 ee OR：两轨均 False → 合并 ee=False。
- 回归：Vertical / Context_Workflow 两轨 location 不同 → 不按端点合并（保持各自条目）。
- 回归：Horizontal 但 endpoint 缺失 → fallback 严格 key。
- 回归：inj/xss/ssrf 现有合并行为不变。

### 6.3 真机验证（用户重跑时）
- 重扫后 authz：6 个重叠端点合并成 6 条 `both`（带 PoC）；`AUTHZ-VULN-01` 等 LLM 独有项 ee 修正为 True 且带 PoC；Vertical/pickup 互补项保留。

## 7. 生效条件与范围

- A/B/C 均改扫描流程（prompt + merger activity `run_merge_dual_track_queues`），**只对未来扫描生效**。
- 旧报告（`delivery-20260804-024910`）由用户自行重跑，本 spec 不涉及重跑入口。
- 影响范围：白盒 authz（主要）、auth（ee 定义）；inj/xss/ssrf 仅 merger 回归保护、行为不变。

## 8. 不做（Out of Scope）

- 不改 authz agent 召回逻辑 / 双轨分工（LLM 仍跑全方法论含 IDOR，GitNexus 仍专 IDOR——重叠靠本去重吸收）。
- 不加"重跑后处理"入口。
- 不改 PoC 生成器本身（去重 + ee 修正后 PoC 自然正确）。
- 不动黑盒（黑盒无 PoCGenerator，见现有设计）。
