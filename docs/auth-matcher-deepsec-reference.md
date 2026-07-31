# 借鉴 deepsec 鉴权 matcher 设计，补强 supernova 确定性鉴权召回

> 对象：本仓库 **supernova**（shannon-py）。借鉴来源：`/root/deepsec`（Vercel 出品的 rule-first 静态扫描器）。
> 目的：梳理 deepsec 在「鉴权 / 访问控制」确定性 matcher 的设计，对照 supernova 现状 gap，给出**可落地、且严格遵守本仓双轨铁律**的借鉴方案。
> 日期：2026-07-31。本文为**设计参考**，非已实现功能。

---

## 0. 一句话结论

supernova 的鉴权检测**确定性召回薄弱**：`auth` 无确定性轨（纯 LLM），`authz` 仅 IDOR 有确定性轨。deepsec 把「端点缺认证 / 鉴权绕过 / 跨租户 ID 误用 / dev 旁路 / OAuth·JWT·会话 cookie·CORS 配置 / 签名校验缺失」全做成了**确定性 matcher 候选**，再交 LLM 复核。借鉴方向 = 按 deepsec 的 matcher 清单，在 supernova 的 GitNexus 轨**新建 auth 确定性候选 → 独立 auth judge → 与 `vuln-auth` LLM 轨 OR 合并**（复刻 IDOR 轨的成熟模式，**不重蹈已被删除的 `auth_config_scanner` 覆辙**）。

---

## 1. deepsec 的鉴权 matcher 设计（借鉴来源）

deepsec 是 rule-first SAST，核心是**两段式**：matcher 只找候选位置，AI 才判真伪。

### 1.1 统一 matcher 接口

每个 matcher 是一个 `MatcherPlugin`（`packages/core/src/plugin.ts`），字段：

| 字段 | 作用 |
|---|---|
| `slug` | 规则唯一标识（finding 归类、去重 key） |
| `noiseTier` | `precise` / `normal` / `noisy` 三档——排序时 precise 优先送 AI，控制 AI 投入 |
| `filePatterns` | glob 命中的文件范围 |
| `requires.tech` | 技术门控（如 `["nextjs"]`、`["terraform"]`），由 `detect-tech` 先测绘项目栈，matcher 按需激活 |
| `examples` | 自检样例（CI snapshot 测试断言） |
| `match(content, filePath)` | 返回 `CandidateMatch[]`（slug + lineNumbers + snippet + matchedPattern），**不判漏洞** |

关键：matcher **铺得广也不怕误报**，因为最终判断交给 `process` 阶段的 AI（带 per-tech 威胁高亮 `highlights.ts`，逐接口引导查鉴权）。

### 1.2 鉴权 / 访问控制类 matcher 清单（已核实源码）

> 下列 slug 均位于 `/root/deepsec/packages/scanner/src/matchers/`，检测逻辑已逐一读源码确认。

**A. 接口鉴权缺失（这个端点有没有认证）**

| slug | noiseTier | 检测什么 |
|---|---|---|
| `missing-auth` | normal | 所有 HTTP 入口（`app./router.` 方法、Next.js `GET/POST` 导出、default export handler），**先白名单跳过**用了 `withSchema`/`withAuthentication`/`authMiddleware`/`withAuth`/`requireAuth` 等 auth wrapper 的文件，只标"疑似无鉴权" |
| `public-endpoint` | precise | 显式声明公开的端点：`authStrategy: '__PUBLIC__' / 'static' / 'none'` |
| `server-action-no-auth` | precise | Next.js Server Action 导出且函数体 30 行内**无任何 auth 调用**（`getSession`/`auth()`/`requireAuth`/`verifyToken`…）——每个导出都是公开 POST 端点 |
| `nextjs-middleware-only-auth` | normal | 受保护路由组里的 route handler 自己无 auth、只靠 middleware |
| `catch-all-route-auth` | — | catch-all 路由 / Payload CMS 端点，验证 auth 覆盖所有子路径 |

**B. 鉴权绕过 / 后门**

| slug | 检测什么 |
|---|---|
| `auth-bypass` | 可疑鉴权判定/绕过形状：`isAdmin==true`、`if(!session)`、`verify(Token\|JWT\|Session\|Auth)()`、`auth...skip/bypass`、`req.headers['authorization']` |
| `dev-auth-bypass` | dev/test 鉴权后门可能在线上可达：dev-only auth 端点、`NODE_ENV` 守卫、测试 token 接受 |
| `test-header-bypass` | 靠 test/debug/internal 请求头跳过安全检查的条件分支 |

**C. 越权 / 多租户（IDOR / BOLA）**

| slug | noiseTier | 检测什么 |
|---|---|---|
| `cross-tenant-id` | precise | 用户提供的 ID 直接用于 DB 查询、无 ownership 校验：`getTeamById(parsed.body.teamId)`、从请求取 `teamId/ownerId/userId`、`findById(parsed...)` |
| `page-without-auth-fetch` | precise | Next.js Server Component 页面用 URL `params` fetch 资源却不校验访问（IDOR） |
| `unverified-lookup` | — | 未校验的查询（同类 IDOR 思路） |
| `iam-permissions` | — | IAM 权限配置点（配错可提权） |
| `tf-iam-wildcard` | — | Terraform IAM/KMS/S3/SNS/SQS 策略过宽（`actions/resources/Principal = "*"` 配 `Allow`） |

**D. OAuth / 会话 / 配置类鉴权缺陷**

| slug | 检测什么 |
|---|---|
| `oauth-flow` | OAuth authorize/callback 端点 + 带 token 的重定向：`redirect_uri`、`grant_type`、PKCE、URL 里的 `?code=`、redirect 里的 token |
| `jwt-handling` | JWT 处理（算法不固定 `alg=none`、未验签等） |
| `session-cookie-config` | 会话 cookie 配置（HttpOnly/Secure/SameSite 缺失） |
| `cors-wildcard` | `CORS origin:'*'` 配 credentials（CSRF-via-fetch） |
| `security-behind-flag` | 靠 feature flag 守护敏感逻辑 |

**E. 限流 / 签名 / 其他**

| slug | 检测什么 |
|---|---|
| `rate-limit-bypass` | 敏感端点（auth/billing/data export）缺限流 |
| `webhook-handler` / `cron-secret-check` / `slack-signing-verification` | 外部回调/定时任务的**签名校验缺失** |
| `debug-endpoint` | 调试端点暴露 |
| `lua-regex-bypass` | OpenResty Lua 正则校验可被贪婪通配符绕过 |

> 此外 deepsec 还有 **50+ 个框架路由/handler matcher**（express/fastify/hono/nestjs/nextjs/flask/django/spring/gin/echo/.../rust/.net/ruby/php…），承担"端点发现"——这是另一条议题（见 §4.6）。

---

## 2. supernova 现状（鉴权确定性召回）

| 维度 | 现状 |
|---|---|
| 整体架构 | 双轨：**GitNexus 确定性轨** + **LLM 轨**，verdict OR 合并（`dual_track_merger.py`） |
| `auth`（认证） | **纯 LLM 单轨**（`prompts/vuln-auth.txt` 9 类方法论）。**无确定性轨**——原有的 `auth_config_scanner` + `run_auth_gitnexus_judge` 已于 **2026-07-14 删除**（踩铁律 + CORS 越界 misconfig，见 plan `zazzy-roaming-shamir`） |
| `authz`（越权） | **仅 IDOR（水平越权）有确定性轨**：`authz_gitnexus_track.py`（ownership 启发式 `OWNERSHIP_PREDICATE_RE` + 框架自动端点）→ 候选喂**深度 agent 判定** `run_gitnexus_verdict_agent`（`prompts/authz_gitnexus_judge.txt`）。Vertical/Context/多租户**纯 LLM**（`vuln-authz.txt`） |
| 确定性规则形态 | 规则散在 4 个 YAML（`data/{sink_rules,source_rules,sink_candidates,storage_rules}.yml`）+ 各 `vuln_chain_builders/*.py`。**无统一 matcher 抽象、无 noiseTier 分级**。`sink_rules.yml` ~90 条 sink，**唯独没有 auth/authz sink** |
| 入口点发现 | `entry_points.py::detect_entry_points` 仅 5 语言（Python/Go/TS/Java/PHP）的正则，框架覆盖远低于 deepsec |

### 铁律（CLAUDE.md §1，红线）

1. **确定性产物不能喂进 LLM 轨 prompt**（`vuln-auth.txt` / `vuln-authz.txt`）。LLM 轨必须纯 LLM 自给自足，`static_dataflow_hints` 桥梁已拆除、有测试锁定。
2. 确定性候选只能喂**独立 judge LLM**（单次结构化输出，非 agent；或像 authz 那样深度 agent 吃候选）。
3. 确定性轨与 LLM 轨**只在合并器 OR**。
4. `auth_config_scanner` 被删的真因：①确定性产物喂了 LLM 轨 prompt（违反铁律）；②把 CORS 等 misconfig 混进 auth 轨（越界）。**重建时两条都要避开。**

---

## 3. Gap 对照（deepsec 有 / supernova 缺）

| # | deepsec 确定性 matcher | supernova 现状 | 缺口严重度 |
|---|---|---|---|
| 1 | `missing-auth`（端点缺 auth wrapper） | 无（auth 纯 LLM） | **高**——缺端点级认证缺失的确定性召回 |
| 2 | `auth-bypass`（鉴权绕过模式） | 无确定性，全靠 vuln-auth LLM | 中 |
| 3 | `cross-tenant-id` / `unverified-lookup`（tenant ID 误用、findById 无 ownership） | authz 仅 IDOR ownership 启发式，多租户 tenant ID 误用无确定性覆盖 | **高** |
| 4 | `dev-auth-bypass` / `test-header-bypass` / `debug-endpoint` | 无 | 中（dev 旁路） |
| 5 | `oauth-flow` / `jwt-handling` / `session-cookie-config` / `cors-wildcard` | 无确定性（部分在 vuln-auth LLM 方法论里提，但无规则召回） | 中 |
| 6 | `webhook-handler` / `cron-secret-check` / `slack-signing-verification`（签名校验） | 无 | 中（外部回调签名） |
| 7 | `rate-limit-bypass` / `security-behind-flag` | 无 | 低 |
| 8 | 统一 `MatcherPlugin` + `noiseTier` + `examples` 自检 | 规则散在 YAML + builder，无统一抽象 | 架构层（长期） |

---

## 4. 可落地的借鉴方案

### 4.1 总体路径：复刻 IDOR 轨，重建 auth 确定性轨

参照已验证的 authz IDOR 轨，为 auth 建一条对称的确定性轨：

```
[新] auth 确定性 matcher 规则集
        │ （产 Candidate：slug + file:line + snippet + matchedPattern）
        ▼
[新] auth_gitnexus_judge（独立 judge，单次结构化输出；或深度 agent 吃候选）
        │ （保守过报：unclear → vulnerable）
        ▼
   dual_track_merger  ──OR──  vuln-auth LLM 轨（不动，仍纯 LLM 自给自足）
```

- 候选来源复用 `entry_points.py` 的入口点 + 新增鉴权规则。
- judge 产物走合并器 OR，**绝不喂 `vuln-auth.txt`**。
- `SUPERNOVA_LLM_TRACK_ENABLED=0` 时，auth 仍由 LLM 轨兜底（对齐现状）；新 auth 确定性轨作为**额外召回**，关轨时与 LLM 轨 OR（同 IDOR 轨的关轨语义）。

### 4.2 auth judge prompt 骨架（仿 `authz_gitnexus_judge.txt`）

```text
<role>
You are an Authentication Verdict Judge. You are given a list of auth-candidate
sites produced by deterministic matchers (endpoints lacking auth wrappers,
suspected bypass/dev-backdoor patterns, misconfigured OAuth/JWT/session/CORS).
Confirm or reject each based ONLY on the evidence in each candidate.
</role>

<objective>
For EACH candidate, emit one AuthVulnerability verdict. Be conservative:
when auth coverage is unclear, judge vulnerable (prefer over-reporting —
the merge phase reconciles with the LLM track).
</objective>

<input>
{{AUTH_GITNEXUS_CANDIDATES}}
</input>

<output_format>
{
  "vulnerabilities": [
    {
      "ID": "AUTH-GN-NN",
      "vulnerability_type": "missing-auth | auth-bypass | dev-backdoor | misconfig",
      "externally_exploitable": true,
      "endpoint": "POST /api/users/create",
      "vulnerable_code_location": "file:line",
      "guard_evidence": "no auth wrapper; route registered before auth middleware",
      "reason": "1-2 lines why vulnerable/safe",
      "minimal_witness": "curl POST with no credentials → 200",
      "confidence": "high | med | low",
      "notes": "candidate source: missing-auth | auth-bypass | ..."
    }
  ]
}

Rules:
- Emit ONE entry per candidate. Rejected: externally_exploitable=false + reason.
- If zero candidates, emit {"vulnerabilities": []}.
</output_format>
```

### 4.3 deepsec matcher → supernova 落地映射

> 原则：**模式明确、低误报**的做成确定性正则规则；**需语义判断**的留 LLM 轨；确定性候选一律经独立 judge。

| deepsec matcher | supernova 落地方式 | 优先级 |
|---|---|---|
| `missing-auth` | **确定性规则**：复用 `entry_points.py` 入口点，白名单 auth wrapper（对齐 supernova 各框架：`@login_required`/`Depends(get_current_user)`/`@UseGuards`/`withAuth`/`@Authorize`…），未命中 wrapper 的端点 → 候选 | P0 |
| `cross-tenant-id` | **确定性规则**：`getXxxById(req.body.teamId)`、从请求取 `tenantId/ownerId/userId` 直查 DB → 候选，喂 **authz judge**（补 authz 多租户缺口） | P0 |
| `public-endpoint` / `server-action-no-auth` | 确定性规则（框架特定，Next.js 项目才有） | P1 |
| `auth-bypass` / `dev-auth-bypass` / `test-header-bypass` | **确定性规则**（正则：`NODE_ENV!=='production'` 分支跳过 auth、`x-test/debug` header 绕过、`isAdmin==true`） | P1 |
| `oauth-flow` / `jwt-handling` | 部分确定性（`redirect_uri`/`alg=none`/未验签的形状）+ judge；语义重的留 vuln-auth LLM | P2 |
| `cors-wildcard` / `session-cookie-config` | **谨慎**：当年正是 CORS misconfig 越界导致 auth 轨被删。若做，须严格限定"鉴权相关"（如 `origin:'*'` + `credentials:true`），勿泛化成通用 misconfig 扫描 | P2（带警示） |
| `webhook-handler` / `cron-secret-check` / `slack-signing-verification` | 确定性规则（签名校验缺失） | P2 |
| `rate-limit-bypass` / `security-behind-flag` | 留 vuln-auth LLM（收益低、误报高） | — |
| `iam-permissions` / `tf-iam-wildcard` | IaC/云权限，与 supernova 当前 Web/API 定位较远；按需 | P3 |

### 4.4 规则载体：复用现有 YAML 机制

短期不引入新抽象，直接复用 `data/` 下的 YAML 规则风格：

- 新建 `data/auth_rules.yml`（鉴权缺失/绕过/后门的正则规则，按语言+框架，带 `rule_id`、`noise_tier` 字段对齐 deepsec 的 noiseTier 思路）。
- detector `.py` 只管加载/匹配（沿用现有「detector 管逻辑、规则在 YAML」的约定，见 CLAUDE.md §1）。
- `noise_tier` 用于排序/优先级（precise 候选先送 judge），**不改变双轨合并语义**。

### 4.5 authz 扩面（补 Vertical/多租户，不止 IDOR）

supernova authz 目前只做 Horizontal IDOR。deepsec 的 `cross-tenant-id` / `unverified-lookup` 提供了**多租户 tenant ID 误用**的确定性候选来源，可喂现有 `authz_gitnexus_judge`（或其候选扩展 spec-1b 通道），补 authz 在多租户场景的召回——这是比新增 auth 轨更小的改动，建议作为**先导试点**。

### 4.6 端点发现（议题 B，单独评估）

deepsec 的 50+ 框架路由 matcher vs supernova `entry_points.py` 的 5 语言正则，是**端点召回**的差距。但这属于攻击面发现（影响所有 vuln 类），非鉴权专属，建议单独立项（可参考 `docs/gap/entry-point-gap-analysis.md`、`route-analysis-binding-gap-analysis.md`）。

---

## 5. 风险与注意

1. **铁律红线**：确定性候选**绝不喂** `vuln-auth.txt` / `vuln-authz.txt`。新轨产物只进独立 judge + 合并器。`packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py` 锁定了"prompt 不 include 确定性产物"，新 prompt 同样受约束。
2. **勿重蹈 `auth_config_scanner`**：当年删除两大原因——①确定性产物喂了 LLM 轨；②把 CORS 等非鉴权 misconfig 混进 auth 轨（越界）。重建 auth 确定性轨时，规则**严格限定"鉴权/访问控制"语义**，CORS/cookie 等边界配置单独评估、勿打包。
3. **保守过报 + merge OR**：judge 对 unclear 判 vulnerable（同 `authz_gitnexus_judge.txt`），靠 LLM 轨在合并器 reconcile——与 IDOR 轨一致，不要在 judge 里追求高精度。
4. **judge 立场**：auth 候选用**独立 judge（轻量单次结构化输出）**还是**深度 agent（吃候选多轮）**，取决于候选复杂度。IDOR 用深度 agent 是因 ownership 判定需多轮追码；missing-auth/dev-bypass 这类形状明确的，轻量 judge 足矣（更省 token）。
5. **框架覆盖**：确定性规则按 supernova 实际目标栈优先（Python Django/FastAPI/Flask、Java Spring、Go、Node 全家桶），不必一次性铺到 deepsec 的几十框架。

---

## 6. 参考文件索引

**deepsec 侧（借鉴来源，`/root/deepsec/`）**
- 鉴权 matcher 目录：`packages/scanner/src/matchers/`（`missing-auth.ts`、`auth-bypass.ts`、`cross-tenant-id.ts`、`dev-auth-bypass.ts`、`test-header-bypass.ts`、`oauth-flow.ts`、`public-endpoint.ts`、`server-action-no-auth`…）
- matcher 注册表：`packages/scanner/src/matchers/index.ts`（198 个）
- 插件接口：`packages/core/src/plugin.ts`、`packages/core/src/types.ts`（`CandidateMatch`）、`packages/scanner/src/types.ts`（`NoiseTier`）
- per-tech 鉴权威胁高亮：`packages/processor/src/prompt/highlights.ts`

**supernova 侧（本仓，待优化处）**
- 双轨铁律：`CLAUDE.md` §1（改前必读）
- authz 确定性轨（IDOR 模式范本）：`packages/core/src/supernova_core/code_index/authz_gitnexus_track.py`
- authz judge prompt（judge 模板范本）：`prompts/authz_gitnexus_judge.txt`
- ownership 正则：`packages/core/src/supernova_core/code_index/patterns.py`（`OWNERSHIP_PREDICATE_RE`）
- 入口点检测：`packages/core/src/supernova_core/code_index/entry_points.py`
- 规则 YAML（新 `auth_rules.yml` 的载体范本）：`packages/core/src/supernova_core/code_index/data/{sink_rules,source_rules,sink_candidates}.yml`
- 合并器：`packages/core/src/supernova_core/.../dual_track_merger.py`
- LLM 主轨（勿动、勿喂）：`prompts/vuln-auth.txt`、`prompts/vuln-authz.txt`
- 相关 gap 分析：`docs/gap/authz-effect-gap-analysis.md`、`docs/gap/2026-06-21-auth-effect-gap-analysis.md`、`docs/gap/entry-point-gap-analysis.md`、`docs/gap/sink-gap-analysis-v2.md`
- 当年删除记录：plan `zazzy-roaming-shamir`（`auth_config_scanner` 下线）
