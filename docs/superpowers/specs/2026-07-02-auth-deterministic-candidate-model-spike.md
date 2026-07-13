> ⚠️ auth 部分已回退 2026-07-14（对齐原始 shannon：`auth_config_scanner` 踩 §1 铁律「确定性产物不喂 LLM 轨 prompt」+ CORS 越界被裁的 misconfig；authz GitNexus 轨保留。详见 plan zazzy-roaming-shamir / memory auth-gitnexus-track-reverted）

# auth 确定性候选模型 spike（spec-2a） 

> 日期：2026-07-02　分支：`feat/fork-py`　所属 epic：`2026-07-02-gitnexus-deep-agent-auth-authz-design.md`（子项目 2a）
>
> **性质：可行性研究（spike），非实现 spec。** epic G2 要把 auth 深度判定搬进 GitNexus 轨，但 auth 是 missing-control、无"候选点"概念（不像 IDOR 有明确对象引用）。本 spike 回答："auth 的确定性候选模型能否设计出？"——可行则启动 spec-2b；证伪则 epic 降级（§4 退路，auth 仍靠 LLM 轨）。
>
> **为何先做 spike**：auth 候选模型是 epic 最大风险（epic R1，dead-end）。先证伪/证实，避免在基础设施（spec-0）和 authz（spec-1）做完后发现 auth 搬不动。spike 可与 spec-0/1 并行（研究不阻塞实现）。

---

## 1. 目标 / 非目标

### 目标

- **G1（可行性裁决）**：给出 auth 确定性候选模型的**可行 / 证伪**二选一结论，附数据。
- **G2（若可行）**：产出候选 schema 草案（字段、生成逻辑、与 code_index / auth_config_scan 的集成方式）+ 覆盖面/精度评估。
- **G3（聚焦证伪）**：在最可行方向上快速原型，优先证伪（dead-end 越早暴露越好）。

### 非目标

- **不实现完整 auth 深度 agent**：那是 spec-2b，依赖本 spike 通过。
- **不改 `auth_config_scan`**：config 类（cookie/HSTS/CORS/JWT nOAuth/rate-limit）已确定性覆盖，本 spike 只攻**逻辑类**。
- **不改 LLM 轨 `vuln-auth.txt`**：它是参照系（方法论来源），不动。
- **不追求生产级精度**：spike 只要"够判断可行性"的数据，不优化到合并质量。

---

## 2. 现状（为何要 spike）

| 现状 | 证据 |
|---|---|
| auth_config_scan 覆盖 5 类 config | `auth_config_scanner.py:111-321`（cookie/HSTS/CORS/JWT nOAuth/rate-limit），纯确定性正则，**独立于 recon** |
| vuln-auth agent 9 类方法论 | `prompts/vuln-auth.txt:148-201`：transport / rate-limit / session / token / session-fixation / password-policy / login-response / recovery / SSO-OAuth。读 `auth_config_scan.json` + recon 自主分析 |
| 逻辑类未确定性覆盖 | session 固定、密码明文存储、用户枚举、密码重置 token 可预测、OAuth state 缺失、remember-me 不安全——需语义判断 |
| code_index 无 auth sink/source | `data/sink_rules.yml` 全 taint 类（sql/command/file/...），grep `auth\|login\|session\|cookie\|token\|password` 返回 0 auth 专用规则；`source_detector` 检测的是用户输入源（req.body 等），非 auth 机制源 |
| auth/authz 边界未建模 | `EntryPoint.authentication`（`models.py:59`）只有 public/required/unknown 三态粗标（`entry_point_fusion.py:218-228` 正则提取），非缺陷检测 |
| recon 有丰富 auth 情报但未确定性消费 | NodeGoat `recon_deliverable.md:177-196`：session 机制/密码存储(Plaintext)/登录流程调用链/用户枚举/默认凭证——全 Markdown，未被 code_index 结构化（`entry_point_fusion` 只抽 authentication 三态） |

---

## 3. 候选方向（调查已评估 3 个）

| 方向 | 思路 | 可行性 | 覆盖 vuln-auth 9 类 | 难度 |
|---|---|---|---|---|
| **A：auth sink 规则化** | `session.regenerate`/`bcrypt.hash`/`jwt.sign`/`crypto.randomBytes` 等作 sink，走 call graph 追 | 中 | 3-4 类部分 | 中（authz dominance heuristic 依赖 ownership guard 模式，auth 无等价物——auth 的"guard"是整个认证流程语义） |
| **B：认证端点 handler + 检查清单** | 确定性识别 /login /logout /signup /reset /token /oauth/callback handler，对每个做结构化检查（session.regenerate 调用？密码 hash？reset token 生成方式？OAuth state 校验？） | **中高** | **4-5 类部分** | **中低**（复用 auth_config_scan 的 `_AUTH_ROUTE_RE` + code_index EntryPoint；检查项是"某函数是否在某段代码被调用"，可在 call graph 做） |
| **C：recon 情报结构化消费** | 像 entry_point_fusion 解析 recon 路由一样，解析 recon 的 auth section（session 配置/密码哈希/默认凭证）为结构化事实 | 高（实现易） | 与 recon 同 | 低（实现）但**脆弱**（依赖 LLM 输出格式稳定性） |

**spike 聚焦方向 B**（最可行 + 覆盖广 + 难度低），辅以方向 C 补情报（B 的检查项需要 session 配置等事实，C 可供）。

---

## 4. spike 方法（聚焦方向 B）

**原型范围**：选 2-3 个真实 repo（含 NodeGoat——已知有 session 固定 + 明文密码 + 默认凭证缺陷），实现方向 B 的最小原型：

1. **认证端点识别**：复用 spec-1 G4 扩框架 + `auth_config_scanner.py:282-286` 的 `_AUTH_ROUTE_RE`，识别 /login /logout /signup /reset /token /oauth/callback handler。
2. **结构化检查项**（每项一个确定性检测）：
   - login handler 成功路径是否调 `session.regenerate()`（session 固定）
   - 密码写入 DB 前是否经 hash（`bcrypt.hash`/`crypto.pbkdf2`/`argon2`）（明文存储）
   - reset token 生成是否密码学安全（`crypto.randomBytes`/`randomUUID` vs 弱生成器）
   - OAuth callback 是否校验 `state`
   - login handler 内是否多个不同 error response（用户枚举）
3. **对照基准**：vuln-auth agent 在同 repo 的发现（读 `auth_exploitation_queue.json`）作为召回上限参照。
4. **数据采集**：原型发现的 auth 逻辑缺陷 vs vuln-auth 发现；误报率（人工核查原型输出）；跨语言（至少 Node.js/Express + 1 个其他）。

---

## 5. 判据（可行 / 证伪）

**可行**（启动 spec-2b）：
- 方向 B 覆盖 vuln-auth 9 类中 **≥4 类**的部分场景。
- 误报率可接受（**<40%**——逻辑类天然比 config 类噪，阈值放宽）。
- 至少 **2 种语言/框架**可用（跨语言通用性）。
- 候选 schema 能落到 code_index（新 sink category 或独立 track）。

**证伪**（epic 降级，auth 仍靠 LLM 轨）：
- 覆盖 <3 类，或误报 >60%，或跨语言不可行。
- → spec-2b 取消；epic 收窄为"只 authz"；auth 经 per-class 豁免保 LLM 轨（回退到部分 A）。

**中间态**（部分可行）：覆盖 3-4 类但某语言盲——spec-2b 限定语言范围启动（如先 Node.js + Python），其余语言 auth 仍靠 LLM 轨。

---

## 6. 产出（spike 报告）

写到 `docs/superpowers/specs/2026-07-02-auth-deterministic-candidate-model-spike-report.md`（本文件是计划，报告另出）：

1. **裁决**：可行 / 证伪 / 部分可行（附 §5 判据数据）。
2. **若可行——候选 schema 草案**：`AuthCandidate` 字段（endpoint_id / check_type / evidence / ...）、生成逻辑、与 code_index 集成方式（新 `auth_sink` category？独立 `auth_gitnexus_track.py` 类比 authz？扩展 `auth_config_scanner`？）。
3. **覆盖面/精度数据**：每类检查项的召回 / 误报 / 跨语言表现。
4. **对 spec-2b 的建议**：候选模型定稿建议 + 范围（语言/检查项子集）+ 风险。

---

## 7. spike 要回答的关键问题

1. 方向 B 精度：call graph 上检测"login 成功路径是否调 session.regenerate()"的精度？session.regenerate 可能在 helper 函数而非直接调用链——call graph 能追到吗？
2. 跨语言通用性：Express session vs Django session vs Go gorilla/sessions 差异大，一套检查模式覆盖多少？
3. A vs B 取舍：定义 auth sink 走 call graph（A，通用但路径长）vs handler 层检查清单（B，直接但需先识别 handler）——哪个性价比高？
4. 方向 C 可靠性：recon 的 auth 情报输出是否足够结构化稳定到程序解析？（看多项目 recon deliverable 实例）
5. 边界：哪些 auth 缺陷本质"需理解业务逻辑"（认证流程绕过），确定性候选只能产 low-confidence 交 LLM 深判？
6. 与 auth_config_scan 关系：扩展 `auth_config_scanner.py`（B+C）vs 新建独立 `auth_gitnexus_track.py`（A，类比 authz）？
7. code_index 集成：auth 候选需新 sink category + sink_rules？还是保持 config scanner 独立模式（不进 sink/source 体系）？
