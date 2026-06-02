# Shannon Recon Prompt Fix — Design Spec

**Date:** 2026-06-02
**Status:** Draft
**Scope:** `shannon-py/prompts/recon.txt` only; revert `vuln-injection.txt` and `vuln-xss.txt`

## Background

Shannon 扫描 `ads_oa_fe` 时漏掉了两个 XSS 漏洞：

1. `/preview/v2?bizEntity=</script><img src=x onerror=alert(1)>`（需登录）
2. `/preview/iframe-demo?bizEntity=</script><img src=x onerror=alert(1)>`（免登录）

根因分析定位到 Recon 阶段（`recon.txt`）的两个缺陷，而非下游 Vuln Agent。

### Recon 的两个错误

**错误 A — 参数漏标：** `router.js` 中 `/preview/v2` 和 `/preview/iframe-demo` 共享同一个 `controller.index.preview`，该 controller 接受 `bizEntity` 参数。但 Recon 只在 `/preview/v2` 行标了 `bizEntity`，在 `/preview/iframe-demo` 行标成了 `None`。

**错误 B — 越权安全判断：** Section 9 中 Recon 对 `BIZ_ENTITY` 下了 "properly handled" 的结论。这是 Vuln Agent 的职责。Recon 的任务是报事实，不做安全判断。

### 职责边界

- **Recon**：枚举路由、中间件链、参数、数据流。只报事实。
- **Vuln Agent**：读 Recon 交付物，从 source 追到 sink，判断编码/校验是否匹配。

## Changes

### Change 1: Section 4 — 共享 Controller 参数传播规则

**位置：** `recon.txt` 中 Section 4（API Endpoint Inventory）表格示例之后，Section 5 之前。

**操作：** 追加以下规则文本：

```
**Shared Controller Parameter Propagation:** When multiple routes map to the same controller
handler function, ALL query/body parameters that the handler reads (e.g., via `ctx.query.*`,
`req.query.*`, `request.getParameter()`) must be listed for EVERY route that uses that handler,
regardless of which route you discovered the parameter on. Do NOT assume a parameter is only
available on one route just because you found it there first.

Example: If `GET /preview/v2` and `GET /preview/iframe-demo` both route to `controller.index.preview`,
and the handler reads `ctx.query.bizEntity`, then BOTH route rows must list `bizEntity` as a parameter —
even if one route has no authentication middleware.
```

**预期效果：** Recon 会在 `/preview/iframe-demo` 行也标上 `bizEntity`，下游 Vuln Agent 就能看到这个免登录路由接受用户可控参数。

### Change 2: Section 9 — 禁止安全性判断

**位置：** `recon.txt` 中 Section 9（Injection Sources）的 `**TASK AGENT COORDINATION:**` 之前。

**操作：** 追加以下规则文本：

```
**CRITICAL — No Security Judgments:** Your job is to IDENTIFY and REPORT facts about injection
sources — where user-controllable input enters, where it flows, and what sink it reaches. You
MUST NOT make security judgments about whether a source is "properly handled", "safe", "secure",
or "not exploitable". Determining whether defenses are sufficient is the SOLE responsibility of
the downstream Vulnerability Analysis agents. If you find a user-controllable input reaching a
sink, REPORT IT — even if you believe the encoding or validation in place is adequate. Let the
vuln agents decide.
```

**预期效果：** Recon 不再对 `BIZ_ENTITY` 下 "properly handled" 的结论，Vuln Agent 会独立追踪 `bizEntity` → template sink 的数据流。

### Change 3: 回滚 vuln-injection.txt 和 vuln-xss.txt

**操作：** 回滚这两个文件到本次修改之前的状态：
- 移除 `authentication_required` 和 `accessible_routes` 字段
- 移除 Source Completeness Rule
- 恢复 `combined_sources` 字段

**理由：** 路由和参数信息由 Recon 负责，Vuln Agent 不应查路由文件。职责归位后这些兜底措施不再需要。

## Files Modified

| File | Action |
|------|--------|
| `shannon-py/prompts/recon.txt` | 加两段规则（Change 1 + Change 2） |
| `shannon-py/prompts/vuln-injection.txt` | 回滚到修改前 |
| `shannon-py/prompts/vuln-xss.txt` | 回滚到修改前 |

## Verification

修正后，对 `ads_oa_fe` 重新运行 Shannon 扫描，检查 Recon 交付物：

1. Section 4 中 `/preview/iframe-demo` 行必须标注 `bizEntity` 参数
2. Section 9 中不能出现 "properly handled"、"safe"、"secure" 等安全判断措辞
3. Injection/XSS Vuln Agent 必须产出独立的 `bizEntity` → template sink 漏洞条目
