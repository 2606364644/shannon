# 黑盒扫描结构化 verdict 落盘 + 工作区漏洞计数对齐

- 日期：2026-08-12
- 分支：`feat/fork-py`
- 状态：设计稿（待 review → plan）

## 1. 背景与根因

### 1.1 现象
黑盒扫描 `__legacy__/scans/NodeGoat-20260729-194022~3`（`scan_type=blackbox`，`status=completed`，6 个 exploit agent 全 success、实跑 88min、¥44.5）在**报告页** `/p/__legacy__/scans/.../report` 能看到漏洞（evidence.md + 综合报告 md 有内容），但在**工作区扫描列表**的漏洞数显示 0。

### 1.2 根因（已定位，非路径问题）
两条读取路径的分叉：

| 路径 | 读什么 | 计数逻辑 |
|---|---|---|
| 报告页 `/report`（能看到） | `DeliverablesReader.list_reports()` 列 `*.md` 渲染正文 | 不计数 |
| 工作区列表 `ScanStore._summarize`（显示 0） | `get_workspace_vuln_counts(scan_dir)` | `deliverables_dir.rglob("*_exploitation_queue.json")` 数 `vulnerabilities` 数组（`workspace.py:133`） |

黑盒 scan 的 `deliverables/` 实际产物：
```
deliverables/
├── {auth,xss,ssrf,injection,authz}_exploitation_evidence.md   ← exploit 证据(md)
└── blackbox/comprehensive_security_assessment_report.md        ← 综合报告(md)
```
**没有任何 `*_exploitation_queue.json`**（全树 `find` 确认空）→ 计数器返回 `{}` → `vuln_count = 0`。

> 澄清：**不是路径分裂**。`DEFAULT_DELIVERABLES_SUBDIR="deliverables"`，`get_workspace_vuln_counts` 拼出的 `scan_dir/deliverables` 路径正确、目录存在（与 memory `web-deliverables-getpaths-workspace-path-split` 的 worker 写侧 `_get_paths` 问题无关，本例不沾）。问题纯粹是**文件类型不匹配**：计数器只认白盒那套 `*_exploitation_queue.json`，黑盒不产这个文件。

### 1.3 黑盒为何不产 queue.json（链路全貌）
- 白盒 `*-vuln` agent 走 `structured_output_schema` → `result.structured_output` 非空 → `executor.py:185-192` `atomic_write_json` 写 `{vc}_exploitation_queue.json`（**候选漏洞输入队列**）。
- 黑盒 **exploitation-only，不跑 vuln agent**，`*-exploit` agent 走 **collector 通道**（`add_exploit` 工具 mode=append），**不传 `structured_output_schema`** → `result.structured_output is None` → 写 JSON 分支跳过。
- 黑盒 exploit 结构化数据链路：
  ```
  add_exploit 工具 → CollectorBase 内存 → validate_exploit_verdicts (L0-L3)
    → VerdictValidation(accepted, rejected)        ← 结构化数据在这里(内存)
    → render_exploit 展平成 markdown 字符串
    → executor.py:199-202 写 {vc}_exploitation_evidence.md   ← 到此结构化形态消失
  ```
- **结构化 verdict 只活在内存，从未序列化落盘**。

### 1.4 两个"有读者、无写者"的孤儿消费者
主线曾有 `{vc}_exploit_verdicts.json` 写盘（worktree 残留 `exploit_evidence_renderer.write_verdicts_json`），重构改 collector+纯函数 renderer 时删了写盘，但读者留着、均回落正则扫 evidence.md：
- `exploitation_checker.py:222-231`：读 `accepted_ids`，缺失回落 `extract_covered_ids` 正则。注释明言 "verdicts.json 缺失（...pre-T5）"——**这是 T5 规划过但未实现的产物**。
- `poc_generator.py:796-804` `_load_accepted_ids`：同样读 `accepted_ids`，缺失返回空集。

**结论**：治本 = 补全一个本该存在的 `{vc}_exploit_verdicts.json` 写盘 + 让计数器读它 + 让孤儿消费者用上它。语义比"让黑盒硬产 `*_exploitation_queue.json`"干净（queue 是白盒 vuln agent 的**候选输入**格式，黑盒产 queue 会把"验证结果"塞进"候选队列"格式，语义错位）。

## 2. 目标 / 非目标

### 目标
1. 黑盒 scan 产出结构化 `{vc}_exploit_verdicts.json`（落 `deliverables/blackbox/`），恢复主线缺失的写盘。
2. 工作区扫描列表对黑盒 scan 显示**成功 exploit 数**（status=exploited 的 verdict 计数）。
3. 两个孤儿消费者（coverage 检查、PoC 生成）改走 verdicts.json 精确路径。

### 非目标
- **不兼容老 scan**：已有黑盒 scan（如 NodeGoat-20260729-194022~3，无 verdicts.json）工作区列表维持 0，不做读侧 fallback、不做迁移脚本。重扫或新 scan 才显示。
- 不改 exploit agent prompt、不加 `structured_output_schema`、不改 collector 契约。
- 不动白盒 queue 机制。
- 不涉及双轨（CLAUDE.md §1）—— 本改动是黑盒产物落盘层，与 inj/xss/ssrf 双轨、确定性层无关。

## 3. 决策（已与用户确认）

| 决策点 | 选择 | 理由 |
|---|---|---|
| 计数语义 | 成功 exploit 数（status=exploited） | 符合"黑盒扫出漏洞"心智；与 evidence.md "Successfully Exploited" 段一致 |
| 老 scan（计数器） | 只管新 scan（老 scan 显示 0） | 用户接受；范围最小 |
| 孤儿消费者 | 顺带修 | 治本彻底；读代码已就绪（读 `accepted_ids`），工作量极小 |
| 落盘格式 | `{vc}_exploit_verdicts.json` | 对齐已有读者字段名 + worktree 被删产物 + T5 规划 |
| 实现挂点 | renderer 链路（VerdictValidation 已在手） | 零额外计算；不踩 structured_output 双引擎坑 |

### 关键交互点（易误解，明确之）
"只管新 scan"是对**计数器显示**而言；"顺带修孤儿消费者"**必须保留 evidence.md 正则回落**——否则老 scan 的 coverage/PoC 功能会回归（之前靠回落还能工作）。两者不冲突：计数器老 scan 显示 0 是展示层取舍；孤儿消费者保留回落是功能层防回归。

## 4. 设计

### 4.1 写盘：补 `{vc}_exploit_verdicts.json`

**挂点**：`renderers/__init__.py:42-78` `_render_exploit_deliverable` 此刻已持有 `VerdictValidation`（accepted: `list[ExploitVerdict]` + rejected: `list[tuple[dict,str]]`，见 `collectors/exploit.py:133-136`）。让 renderer 增返一份 verdicts payload（保持纯函数——算数据不写盘），由 `executor.py:199-202` 的 render 分支在写 evidence.md 的同处一并写 verdicts.json。

```
render_deliverable(...) -> (md: str, verdicts_payload: dict | None)
executor.py:199-202:
    md, verdicts_payload = render_deliverable(...)
    (deliverables / defn.deliverable_filename).write_text(md)            # 已有
    if verdicts_payload is not None:
        atomic_write_json(blackbox_dir(deliverables) / f"{vc}_exploit_verdicts.json", verdicts_payload)  # 新增
```

**返回签名统一为 `(md, verdicts_payload | None)`**：`render_deliverable` 是 `__init__.py` 的统一 dispatch 入口，改其返回从 `str` → `tuple`。`_render_exploit_deliverable` 返回 `(md, payload)`；其他所有 renderer（vuln/report 等）返回 `(md, None)`，executor 据此仅 exploit 写第二个文件。`blackbox_dir(deliverables)` 是 `paths.py:121` 的函数（返回 `deliverables/"blackbox"`），非属性。

**路径**：`deliverables/blackbox/{vc}_exploit_verdicts.json`（对齐 endpoint_verify.json、evidence.md 的 blackbox/ 约定，`paths.py:121-126 blackbox_dir`）。evidence.md 当前在老 scan 是 legacy flat 顶层，新 scan 应落 blackbox/ —— verdicts.json 一律落 blackbox/。

### 4.2 verdicts.json schema

```json
{
  "accepted_ids": ["INJ-VULN-01", "XSS-VULN-02"],
  "verdicts": [
    {"vulnerability_id": "INJ-VULN-01", "status": "exploited", "severity": "critical", "...": "..."},
    {"vulnerability_id": "XSS-VULN-02", "status": "blocked_by_security", "...": "..."}
  ],
  "rejected": [
    {"id": "INJ-VULN-99", "reason": "L2 id不在queue: INJ-VULN-99"}
  ]
}
```

字段来源：
- `accepted_ids` = `[v.vulnerability_id for v in validation.accepted]`（**所有 accepted 的 id，含 exploited/blocked/potential/other**，非只 exploited）—— 满足 coverage/PoC 两消费者（它们读这个字段，凡 accepted 即算覆盖）。
- `verdicts` = `[v.model_dump() for v in validation.accepted]`（完整 verdict，含 `status`）—— 计数器据此数 exploited。
- `rejected` = `[{"id": raw.get("vulnerability_id","<unknown>"), "reason": reason} for raw, reason in validation.rejected]`（id+原因，调试可见性；raw 可能无 vulnerability_id 则 `<unknown>`）。

> 直接复用 `ExploitVerdict.model_dump()`，不新建 model。`accepted_ids` 字段名刻意对齐两个孤儿消费者已读的字段（`exploitation_checker.py:227`、`poc_generator.py:802`），使它们读代码零改动即可走精确路径。

### 4.3 计数器：`get_workspace_vuln_counts` 加 verdicts 支

`workspace.py:120-145` 当前只扫 `*_exploitation_queue.json`。新增第二支扫 `*_exploit_verdicts.json`：

```python
for f in sorted(deliverables_dir.rglob("*_exploit_verdicts.json")):
    if not f.is_file():
        continue
    vuln_class = f.name.replace("_exploit_verdicts.json", "")
    data = json.loads(f.read_text("utf-8"))
    verdicts = data.get("verdicts", [])
    exploited = sum(1 for v in verdicts if isinstance(v, dict) and v.get("status") == "exploited")
    counts[vuln_class] = counts.get(vuln_class, 0) + exploited
```

**key 碰撞分析**：白盒用 `{class}_exploitation_queue.json`（key=class），黑盒用 `{class}_exploit_verdicts.json`（不同 stem）。由于**一个 scan_dir 只有一个 track**（白盒 scan 或黑盒 scan，不共存），同目录不会同时出现两类文件 → 无碰撞。用 `+=`（而非覆盖）保险：即便未来共存也累加而非互吞。黑盒 scan 的 `vuln_count = sum(counts.values())` = 各 class exploited 之和 = "成功 exploit 数"。

### 4.4 孤儿消费者：改读 verdicts.json（保留回落防回归）

两消费者**读代码已就绪**（都读 `accepted_ids`），verdicts.json 一旦存在即自动走精确路径。本次改动主要是**验证 + 文档化**，无需大改：
- `exploitation_checker.py:219-231`：现状已是 "verdicts.json 优先、缺失回落正则"。保留不动（注释已准确）。新增测试覆盖"verdicts.json 存在"路径。
- `poc_generator.py:796-804` `_load_accepted_ids`：现状"存在则读、缺失返回空集"。保留不动。新增测试。

> 不删除正则回落分支（`extract_covered_ids`）——老 scan（无 verdicts.json）的 coverage/PoC 仍依赖它，删了即回归。

### 4.5 老 scan
不迁移、计数器不 fallback → 老 scan 工作区列表维持 0。evidence.md 回落仅在孤儿消费者侧保留（功能层防回归，非计数器侧）。

## 5. 涉及文件

| 文件 | 改动 |
|---|---|
| `packages/core/src/supernova_core/renderers/__init__.py` | `_render_exploit_deliverable` 增返 verdicts payload |
| `packages/core/src/supernova_core/agents/executor.py` | render 分支（~199-202）写 verdicts.json |
| `packages/core/src/supernova_core/workspace.py` | `get_workspace_vuln_counts` 加 verdicts 支（数 exploited） |
| `packages/blackbox/src/supernova_blackbox/services/exploitation_checker.py` | 仅新增测试（读代码已就绪） |
| `packages/core/src/supernova_core/services/poc_generator.py` | 仅新增测试（读代码已就绪） |

跨 `core` + `blackbox` 两 package。复用：`VerdictValidation`、`ExploitVerdict.model_dump()`、`blackbox_dir()`、`atomic_write_json`。

## 6. 测试策略（TDD）

1. **renderer**：给定 `VerdictValidation`（含 exploited/blocked/rejected 各若干），断言 verdicts.json payload 的 `accepted_ids`/`verdicts`/`rejected` 三字段正确、exploited 计数对。
2. **executor 写盘**：exploit agent 跑完后 `deliverables/blackbox/{vc}_exploit_verdicts.json` 存在且 schema 对；非 exploit agent 不产该文件。
3. **计数器**：`get_workspace_vuln_counts` 对含 verdicts.json 的目录数出 exploited 数；与白盒 queue 共存场景（同 class）累加不互吞。
4. **孤儿消费者**：verdicts.json 存在 → 走 `accepted_ids` 精确路径；缺失 → 回落正则（双路径各一测，锁防回归）。
5. **端到端**：黑盒 scan 跑完，`ScanStore._summarize` 的 `vuln_count` = exploited 数（非 0）。

> 测试陷阱（memory）：只跑改动相关测试文件，勿跑全套 pytest（会 hang）；`packages/web` 前端测试须 `cd packages/web/frontend`。

## 7. 风险与不变量

- **生效门槛**：改 core/blackbox src **须 rebuild supernova-worker** 才生效（memory 多处：`blackbox-auth-validation-two-root-causes`、`web-llm-track-switch-not-wired` 等）。plan 须含 rebuild 步骤。
- **纯函数边界**：renderer 保持"算数据"、executor 保持"写盘"——不让 renderer 产生写盘副作用（破坏纯度 + 难测）。
- **双轨铁律（CLAUDE.md §1）**：本改动不触碰双轨、不喂确定性产物给 LLM 轨。verdicts.json 是黑盒 exploit agent 自身 collector 产物的落盘，无确定性层介入。
- **exploitation-only 对齐 TS**：不改黑盒"只 exploit、不 vuln"的定位；verdicts.json 是 exploit 产物落盘，非新阶段。
- **schema 兼容**：`accepted_ids` 字段名锁死（两消费者已读），改动不得重命名。

## 8. 后续（非本 spec 范围）

- 老 scan 迁移脚本（从 evidence.md 反解生成 verdicts.json）——用户已选不做，留待日后若需要再议。
- 前端工作区列表对黑盒 scan 的"漏洞数"标签语义提示（如 tooltip 说明"成功 exploit 数"）——可选增强，不在本 spec。
