# 越权分析优化路线图（AZ-1 ~ AZ-6）

> 本文档是 `authz-effect-gap-analysis.md` 的**执行计划**：把其中识别的 6 个弱势项（AZ-1 ~ AZ-6）拆成独立 Spec 的**排期、依赖、验收标准**，是一份 Spec portfolio plan，而非单点实现 Spec。
>
> **目的**：让你能据此决定"先开哪份 Spec、各项之间如何排序/并行"，并直接拿每项的"Spec 草案大纲"作为下一份正式 Spec 的起点。
>
> **数据来源**：`authz-effect-gap-analysis.md`（差距判定）、`route-analysis-binding-gap-analysis.md`（RA-1/RA-3/RA-4/RA-9 代码级论证）、`shannon/docs/superpowers/specs/2026-06-14-authz-multi-account-design.md`（AZ-1 蓝图）、2026-06-17 代码核验（deliverable 命名 / SharedKnowledge 缺失 / activity 时序）。
>
> **日期**：2026-06-17

---

## 0. 定位与使用方式

- **本文不是**：某一处代码的实现 Spec。
- **本文是**：6 个优化项的**组合规划**——优先级、依赖、阶段、每项的 Spec 范围与验收。
- **使用方式**：① 用 §3 的阶段排期决定推进顺序；② 用 §4 的"Spec 草案大纲"逐个开正式 Spec（每份 Spec 再走 brainstorming → 设计 → 计划 → 实现）；③ 用 §5 的矩阵做"做不做/何时做"的取舍。

---

## 1. 优化项总览（Spec 视角）

| # | 优化项 | 当前状态 | 目标状态 | 建议 Spec 标题 | 工作量 | 优先级 |
|---|---|---|---|---|---|---|
| **AZ-2** | 攻击链 confidence 升级 | IDOR 链永远 `probable`；`attack_chain_builder` 不接收漏洞数据；`attack_chains.json` 与 `route_chains.json` 内容相同（RA-9 冗余） | 已 EXPLOITED 的漏洞能把对应攻击链升 `confirmed`；两个 activity 产出区分 | `authz-attack-chain-confidence-design` | M（3-5 天） | **P0** |
| **AZ-4** | DELETE/PUT 方法显式枚举 | recon 用 "ALL" 符号掩盖具体方法 | recon 端点表逐方法列出，标记 ownership | `recon-http-method-enumeration-design` | S（1-2 天） | **P0** |
| **AZ-1** | 多账户身份对照 | 单账户共享会话；无设计（仅 prompt 残留 "≤5 identities"） | operator 供给 `accounts[]`；多 session；victim/baseline 对照协议 | `authz-multi-account-design`（移植原始蓝图） | XL（2-3 周） | P1（场景相关） |
| **AZ-3** | recon 框架来源标注 | recon §4.2 缺 `Framework Origin`/`Ownership Check` 列 | recon 标注 finale-rest/epilogue 来源 + ownership | `recon-framework-origin-annotation-design` | S（2-3 天） | P2 |
| **AZ-5** | 前端路由越权分析 | `frontend_mapper` 的 `apiCalls`/`userInputs` 提取是**空壳**（RA-4）；前端 guard 不被评估 | 提取前端→后端 API 调用；评估客户端 guard 可绕过性 | `frontend-route-authz-analysis-design` | L（1-2 周，含 RA-4 前置） | P2 |
| **AZ-6** | 跨 agent 攻击链 | 各 vuln agent 独立；**无 SharedKnowledge 等价机制**（RA-3 零基础）；无重组引擎 | 跨 agent 漏洞共享；多步组合攻击可重组 | `cross-agent-attack-chain-design` | XL（3-4 周） | P3 |

> 工作量为粗估（T-shirt + 天数），待各 Spec 细化。优先级依据见 `authz-effect-gap-analysis.md` §7。

---

## 2. 依赖关系图

```
              ┌─────────────────────────────────────────────┐
              │  独立项（无前置依赖，可立即开工）            │
              │                                             │
              │   AZ-4 (DELETE 枚举)      S   ──┐           │
              │   AZ-2 (攻击链 confidence) M   ──┤ 阶段一   │
              │   AZ-3 (recon 框架标注)   S   ──┘           │
              │   AZ-1 (多账户)           XL  ── 阶段二     │
              └─────────────────────────────────────────────┘
                                │
              ┌─────────────────┴──────────────────┐
              ▼                                    ▼
   AZ-5 (前端路由 authz) L                AZ-6 (跨 agent 链) XL
   前置: frontend_mapper                  前置: SharedKnowledge 等价
         apiCalls 提取 (RA-4)                   机制 (RA-3, 当前零基础)
```

**关键依赖**：

- **AZ-2** ⟂ `route-analysis-binding-gap-analysis.md` 的 **RA-1**（漏洞上下文增强缺失）+ **RA-9**（activity 冗余）。修 AZ-2 = 同时解决这两条，**一份 Spec 关闭三个 gap**。数据源：5 类 `{class}_exploitation_queue.json`（`workspaces/.../deliverables/`）。
- **AZ-5** → 依赖 **RA-4**（`frontend_mapper` 的 `apiCalls`/`userInputs` 提取是两版共有空壳）。这是前置工程，不做则 AZ-5 无法落地。
- **AZ-6** → 依赖 **RA-3**（SharedKnowledge 集中式数据流）。重构当前**完全没有**跨 phase 共享知识库（grep `shared_knowledge`/`SharedKnowledge`/`vulnerability_context` 零命中）。这是 AZ-6 的最大前置成本，也是它被排在最后的原因。
- **AZ-1 / AZ-3 / AZ-4** 互相独立，无前置。

---

## 3. 分阶段路线图

### 阶段一：高性价比快速胜利（建议立即开工）

**包含**：AZ-4（DELETE 枚举）+ AZ-2（攻击链 confidence）
**预期收益**：① 补上 `DELETE /api/Feedbacks/:id` 类漏报（与原始共同缺陷，prompt 层快速 win）；② 攻击链报告置信度准确化 + 理顺冗余 activity（RA-9）。
**依赖**：无。
**风险**：低。AZ-4 是 prompt 改动；AZ-2 改动集中在 `attack_chain_builder.py` + `run_attack_chain_assembly` activity。
**为什么先做**：两项都独立、影响明确、工作量小-中，且 AZ-2 一举关闭三个 gap 条目（AZ-2 + RA-1 + RA-9），性价比最高。

### 阶段二：生产能力债（按目标场景决策是否做）

**包含**：AZ-1（多账户身份对照）
**预期收益**：面向**封闭注册/生产目标**的越权验证能力（victim/baseline 对照 → 硬 IDOR 证据）。
**依赖**：无（独立大工程）。
**关键决策点（需你拍板）**：
- 若项目目标**仅含开放注册靶场**（如 Juice Shop benchmarking）→ AZ-1 紧急度**低**（现场注册已绕过，见 gap doc §3.2），可大幅推后甚至跳过。
- 若项目目标**含真实/生产目标扫描** → AZ-1 是 **P1**，应在此阶段做。
**风险**：中-高。涉及 config schema 扩展、多 Playwright session 生命周期、ROE 合规（operator 供给而非自动注册）。

### 阶段三：共短板修复

**包含**：AZ-3（recon 框架标注）+ AZ-5（前端路由 authz）
**预期收益**：① recon 框架来源标注（AZ-3，实测影响小，属完整性补强）；② 前端路由越权盲区（AZ-5，补 `/administration` 类 Admin Section 漏报）。
**依赖**：AZ-5 需先实现 RA-4（`frontend_mapper` apiCalls 提取）。
**风险**：AZ-5 中（前置工程 RA-4 本身是 L 级）；AZ-3 低。

### 阶段四：架构演进（长期）

**包含**：AZ-6（跨 agent 攻击链）
**预期收益**：多步组合攻击重组（如 Video XSS 类：越权改配置 + 文件上传 + XSS 触发），两项目共同盲区。
**依赖**：RA-3（SharedKnowledge 等价机制，零基础）。
**风险**：高。需先建跨 phase 共享知识层，再建重组引擎，再改各 agent 接入。**建议在阶段一~三见效后再评估是否值得投入**。

---

## 4. 各项 Spec 草案大纲

> 每项给出"开 Spec 时的起点"。正式 Spec 仍需各自走 brainstorming → 设计 → 计划。

### 4.1 AZ-2：攻击链置信度升级（P0，阶段一）

**问题陈述**：`attack_chain_builder.py` 不接收漏洞 deliverable，IDOR 链 confidence 恒为 `probable`；`run_attack_chain_assembly`（`activities.py:663`，在 VULN 之后 `workflows.py:326` 调用）仅读 framework/frontend JSON。后果：报告可信度被低估；`attack_chains.json` 实为 `route_chains.json` 副本（RA-9）。

**范围（in）**：
- `attack_chain_builder.py` 增加"已确认漏洞"输入参数与 confidence 升级逻辑（参照原始 `attack-chain-builder.ts:36-52`）。
- `run_attack_chain_assembly` activity 读取 5 类 `{class}_exploitation_queue.json`，提取 confirmed 漏洞传入 builder。
- 理顺 RA-9：让 `attack_chains.json` 与 `route_chains.json` 产出真正区分（或合并冗余 activity）。

**范围（out）**：跨 agent 共享知识库（属 AZ-6）；前端路由关联（属 AZ-5）。

**关键设计点（待 Spec 定）**：
- "confirmed" 信号源：候选 = `exploitation_evidence.md` 存在 / queue 的 `externally_exploitable=true` / evidence 内 EXPLOITED 标记。需在 Spec 中明确（注意 xss/injection 本次扫描无 evidence，信号源选择影响覆盖）。
- 漏洞→攻击链匹配键：endpoint（method + path）对齐。

**验收标准**：
- 当 `AUTHZ-VULN-01`（basket IDOR）已 EXPLOITED 时，对应 `idor-chain` 在 `attack_chains.json` 中 confidence = `confirmed`。
- `attack_chain_builder` 有单测覆盖升级逻辑。
- `attack_chains.json` 与 `route_chains.json` 内容可区分（RA-9 关闭）。

### 4.2 AZ-4：DELETE/PUT 方法显式枚举（P0，阶段一）

**问题陈述**：recon 用 "ALL" 符号表示端点，掩盖 DELETE/PUT/PATCH，导致 `DELETE /api/Feedbacks/:id` 类越权漏测（gap doc C-1，两项目共有）。

**范围（in）**：recon prompt（端点表强制逐方法列出）+ ownership 标注。

**范围（out）**：finale-rest 框架专项（属 AZ-3）。

**验收标准**：
- `recon_deliverable.md` 端点表逐方法列出 GET/POST/PUT/PATCH/DELETE。
- 跑 Juice Shop 能在端点表中标出 `DELETE /api/Feedbacks/:id`。

### 4.3 AZ-1：多账户身份对照（P1，阶段二）

**问题陈述**：单账户共享会话；水平 IDOR 在封闭注册目标无 victim baseline；垂直越权无 admin baseline。原始有完整蓝图（`2026-06-14-authz-multi-account-design.md`）但未落地，重构连蓝图都没有。

**范围（in）**：移植原始蓝图——`accounts[]` config schema、preflight 多登录（每 identity 独立 `auth-state-{id}.json`）、`authz-exploit` victim/baseline ↔ attacker 对照协议、vuln 阶段消费 identity 作 role context。

**范围（out）**：自动 victim 注册（原始明确列为 Non-Goal，ROE 风险）；多租户隔离（v1 single-tenant）。

**关键设计点（待 Spec 定）**：
- Python 侧 `Account` 模型落点（`models/config.py`）。
- 多 Playwright session 生命周期管理（原始用 agent5 单 slot，多 slot 需扩展）。
- attacker 失败 fail-fast、victim/baseline 失败降级的策略。
- 向后兼容：无 `accounts` 时行为不变。

**验收标准**：
- config 支持 `accounts[]`（victim/baseline/attacker）。
- preflight 为每个 identity 建独立 session。
- 封闭注册目标（无现场注册）下，authz-exploit 能用 operator 供给的 victim 产出对照 IDOR 证据。

### 4.4 AZ-3：recon 框架来源标注（P2，阶段三）

**问题陈述**：recon §4.2 缺 `Framework Origin`/`Ownership Check` 列，弱于原始（gap doc §5）。

**范围（in）**：recon prompt §4.2 增框架来源列 + `framework_analyzer.py` 输出对接。

**范围（out）**：vuln-authz 的 finale-rest 专项（删了，但 gap doc §5.2 实测未拉低检出，恢复优先级低）。

**验收标准**：recon §4.2 标注 finale-rest/epilogue 端点来源；vuln-authz 能消费（实测 finale IDOR 检出不退化）。

### 4.5 AZ-5：前端路由越权分析（P2，阶段三）

**问题陈述**：`frontend_mapper` 的 `apiCalls`/`userInputs` 提取是空壳（RA-4），前端 guard（如 `AdminGuard`）客户端可绕过性不被评估（gap doc C-2）。

**范围（in）**：
- 前置：实现 `frontend_mapper` 的 apiCalls/userInputs 提取（RA-4，两版共有空壳）。
- recon 评估前端 guard 客户端校验可绕过性。
- vuln-authz 消费前端路由信息。

**范围（out）**：XSS 链检测（同源空壳，但属 XSS 范畴）。

**验收标准**：`frontend_mapping.json` 的 `apiCalls` 非空；能检出 `/administration` 类前端路由越权。

### 4.6 AZ-6：跨 agent 攻击链（P3，阶段四）

**问题陈述**：各 vuln agent 独立工作，无跨 agent 数据共享，多步组合攻击（Video XSS 类）无法重组（gap doc C-3）。重构无 SharedKnowledge 等价机制（RA-3 零基础）。

**范围（in）**：
- 前置：建立跨 phase 共享知识层（RA-3 的 Python 等价）。
- 攻击链重组引擎（跨 agent findings 组合）。
- 各 vuln agent 接入共享层。

**范围（out）**：自动攻击链发现（v1 仅重组已有 findings）。

**验收标准**：跨 agent 漏洞可共享；能重组 "越权改配置 + 文件上传 + XSS 触发" 类多步链。

---

## 5. 优先级矩阵（影响 × 工作量）

```
   高影响 │  AZ-1(生产场景)          AZ-6
          │
   中影响 │  AZ-2 ★                 AZ-5
          │  AZ-4 ★
          │  AZ-3
   低影响 │
          └──────────────────────────────────
            小工作量              大工作量

   ★ = 阶段一推荐先做（高性价比象限）
```

**读法**：左上象限（高影响 + 小工作量）应优先。AZ-2/AZ-4 落在"中影响 + 小-中工作量"，是最高性价比。AZ-1 的影响取决于目标场景（生产=高 / 靶场=低）。AZ-6 高影响但工作量最大，排最后。

---

## 6. 风险与取舍

| 取舍点 | 判断依据 | 建议 |
|---|---|---|
| **AZ-1 做不做** | 开放靶场已被现场注册绕过（gap doc §3.2） | 取决于目标场景。纯靶场 → 推后；含生产目标 → P1 必做 |
| **AZ-2 vs 单独修 RA-1** | AZ-2 本质 = RA-1 的越权应用 | 合并修，一份 Spec 关闭 AZ-2 + RA-1 + RA-9，不拆 |
| **AZ-6 投入时机** | 需先建 SharedKnowledge（RA-3），工程巨大 | 等阶段一~三见效后，用实测收益评估是否值得 |
| **AZ-3 实际价值** | gap doc §5.2 实测删 framework 专项未拉低 finale IDOR 检出 | 优先级最低，属完整性补强，可 选做 |
| **AZ-5 前置成本** | RA-4（apiCalls 提取）本身是 L 级空壳工程 | 与 XSS 优化共享 RA-4 投入，可合并推进 |

---

## 7. 下一步

1. **推荐第一份正式 Spec：AZ-2（攻击链置信度升级）**——P0、最高性价比、关闭三个 gap、范围可控。
2. 决定 AZ-1 是否纳入近期计划（取决于目标场景，需你确认）。
3. 每份 Spec 独立走 brainstorming → 设计 → writing-plans → 实现。

---

## 8. 交叉参考

- `docs/gap/authz-effect-gap-analysis.md` — 差距判定来源（§7 矩阵）
- `docs/gap/route-analysis-binding-gap-analysis.md` — RA-1/RA-3/RA-4/RA-9 代码级论证（AZ-2/AZ-5/AZ-6 的技术基础）
- `docs/gap/sink-gap-analysis-v2.md` §2.12 — authz prompt 文本对比（SK-15，与 AZ-3 相关）
- `shannon/docs/superpowers/specs/2026-06-14-authz-multi-account-design.md` — AZ-1 的原始蓝图
