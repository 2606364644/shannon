# auth GitNexus 轨深度 agent（spec-2b，条件性框架） 

> 日期：2026-07-02　分支：`feat/fork-py`　所属 epic：`2026-07-02-gitnexus-deep-agent-auth-authz-design.md`（子项目 2b）
>
> **条件性**：依赖 spec-2a（auth 候选模型 spike）通过。spike 证伪则本 spec **取消**（epic §4 退路：auth 仍靠 LLM 轨 `vuln-auth`，epic 收窄为"只 authz"）。spike 部分可行则本 spec 按其建议限定范围启动。
>
> **本文件是框架草案**：候选模型细节（schema、生成逻辑、集成方式）待 spec-2a 报告定稿后细化。各节标【待 2a 细化】处为已知待填项，非实现时再想。

---

## 1. 目标 / 非目标

### 目标

- **G1（auth 候选生成）**：按 spec-2a 确定的候选模型，在 GitNexus 轨确定性生成 auth 候选（认证端点 + 逻辑检查项）。
- **G2（auth 多轮判定）**：候选经 `run_gitnexus_verdict_agent`（spec-0）多轮深判，产 `auth_gitnexus_queue.json`（与现有 `auth_config_scan` 产的同名 queue 合并/区分，待 2a 定）。
- **G3（独立兜底）**：关 LLM 轨时，GitNexus 轨对 auth 逻辑类（非 config）有深度判定产出。

### 非目标

- **不改 `auth_config_scan`**：config 类（cookie/HSTS/CORS/JWT/rate-limit）已覆盖；本 spec 只补逻辑类。
- **不改 LLM 轨 `vuln-auth.txt`**：保留为可选增强（双轨 OR）。
- **不改双轨 merger**：queue schema 兼容。
- **不覆盖所有 auth 逻辑缺陷**：spike 证伪的子类（如认证流程绕过这类纯业务逻辑）仍交 LLM 轨，本 spec 只做 spike 确认可确定性的子集。

---

## 2. 候选模型【待 2a 细化】

**基线方向**（spec-2a 聚焦的 B）：认证端点 handler 识别 + 结构化检查清单。

- **候选来源 1（端点）**：复用 spec-1 G4 扩框架识别 + `auth_config_scanner.py:282-286` `_AUTH_ROUTE_RE`，识别 /login /logout /signup /reset /token /oauth/callback。
- **候选来源 2（检查项）**：每端点跑一组确定性检查——session.regenerate 调用 / 密码 hash / reset token 生成方式 / OAuth state 校验 / 用户枚举（多 error response）。
- **候选 schema 草案**（待 2a 报告定稿字段）：
  ```
  AuthCandidate（草案）:
    endpoint_id / handler_id      # 复用 EntryPoint / FuncBlock
    check_type                    # session_fixation / plaintext_password / weak_reset_token / oauth_state_missing / user_enumeration
    evidence                      # file:line + 代码片段
    confidence                    # 确定性置信（low/medium）
    needs_review                  # True（逻辑类天然需 LLM 复核）
  ```
- **集成方式**【待 2a 裁决】：三选一——(a) 扩 `auth_config_scanner.py` 加逻辑检查 pass；(b) 新建 `auth_gitnexus_track.py`（类比 `authz_gitnexus_track.py`）；(c) 加 auth sink category 进 code_index（`data/sink_rules.yml`）。

---

## 3. 设计（框架）

### 3.1 候选生成【待 2a schema 定稿】

按 spec-2a 报告的候选模型实现生成器。结构对标 `build_authz_gitnexus_track`：读 `code_index.json`（entry_points / source_points / call graph）→ 跑检查项 → 产候选列表 + markdown 渲染。

### 3.2 多轮判定（用 spec-0 脚手架）

候选经 `run_gitnexus_verdict_agent` 多轮深判（带 grep/read 追链）。结构对标 spec-1 的 authz 判定段：
- 候选 ≤ 阈值全量塞 prompt；> 阈值分批并行（fan-out + Semaphore）。
- prompt（新 `auth_gitnexus_judge_deep.txt`）含检查项类型 + evidence + 工具引导。
- structured_output 产 `auth_gitnexus_queue.json`（schema 对齐 `AuthVulnerability`，`models/queue_schemas.py:41-46`）。

### 3.3 候选空探索【对标 spec-1 G2】

候选空（端点未识别 / 检查项全过）时，不静默写空 queue——agent 读认证相关源码（grep login/session/token/password）自主探索，产软候选（`needs_review=True`）。

### 3.4 render + 喂 LLM

候选描述含 evidence（file:line + 代码片段）+ check_type + 端点 route。对标 spec-1 G3。

### 3.5 与 auth_config_scan 的产物关系【待 2a 定】

现状：`run_auth_config_scan`（`activities.py:1084`）产 `auth_gitnexus_queue.json`（config 类）。本 spec 的逻辑类候选判定也产 auth queue——需定合并方式：
- (a) 同 queue 文件追加（config + 逻辑两类混在一个 `auth_gitnexus_queue.json`）；
- (b) 分文件（`auth_config_queue.json` + `auth_logic_queue.json`）再 merge。
【待 2a 报告建议 + merger 兼容性确认】

---

## 4. 验收【待 2a 后细化，框架对标 spec-1】

- **V1**：候选非空时多轮深判产含 evidence 的 `auth_gitnexus_queue.json`。
- **V2**：候选空时自主探索产软候选（或"已探索无发现"证据）。
- **V3（覆盖面，2a 数据）**：spike 报告确认的覆盖范围（≥4 类 / 限定语言子集）在实现后达成。
- **V4（精度，2a 数据）**：误报率 < spike 报告阈值。
- **V5（独立兜底）**：`SHANNON_LLM_TRACK_ENABLED=0` 时 auth 逻辑类有 GitNexus 轨产出（非空或已探索证据）。
- **V6（双引擎）**：双引擎探针实测。
- **V7（与 config scan 共存）**：`auth_config_scan` 行为不变，本 spec 产物不冲突。

---

## 5. 风险

- **R1（依赖 2a，最大）**：候选模型若 spike 证伪/部分证伪，本 spec 取消或缩范围。**对策**：2a 先行，通过才启动；中间态按语言/检查项子集启动。
- **R2（auth 候选确定性程度低于 authz）**：authz 有 ownership guard 明确模式（`OWNERSHIP_PREDICATE_RE`），auth 逻辑类"guard"是流程语义，确定性更弱。**对策**：候选全标 `needs_review=True` + `confidence=low`，交 LLM 深判（conservative，对齐 chain_verdict fallback）。
- **R3（与 auth_config_scan 边界模糊）**：config 类（已覆盖）和逻辑类（本 spec）的边界可能漂移（如 session saveUninitialized 是 config 还是逻辑？）。**对策**：2a 报告明确分类；本 spec 只做 spike 确认的逻辑类，config 类不动 auth_config_scan。
- **R4（覆盖有限，用户预期管理）**：spike 确认可确定性的 auth 子集 ≠ 全部 auth 缺陷。认证流程绕过等纯业务逻辑类仍靠 LLM 轨。**对策**：spec 明确"GitNexus 轨 auth 兜底覆盖 spike 确认子集，非全集"，避免"搬了 auth 就全覆盖"误解。
