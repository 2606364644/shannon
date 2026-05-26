## Context

Shannon runs a 5-phase penetration testing pipeline (pre-recon → recon → vulnerability analysis → exploitation → reporting) with 5 parallel vuln agents (injection, xss, auth, authz, ssrf). Each has a dedicated vuln + exploit agent pair, a Zod queue schema, and a findings renderer. The system supports two modes: full mode (URL + repo, all phases including browser-based exploitation) and whitebox-only mode (repo only, no browser, skips XSS).

The existing auth agent mentions Open Redirect in one sub-check (login/signup response redirects) but does not provide systematic open redirect detection. Security headers, CORS, cookie flags, clickjacking, and information disclosure are not covered by any agent.

## Goals / Non-Goals

**Goals:**
- Add `misconfig` as a 6th VulnClass with full pipeline integration (vuln + exploit agents)
- Provide systematic Open Redirect detection covering all redirect sinks and common bypass techniques
- Cover OWASP A05:2021 Security Misconfiguration sub-types: headers, CORS, cookies, clickjacking, info disclosure
- Include misconfig in whitebox-only mode (all sub-types support pure code analysis)
- Follow existing agent patterns exactly (queue schema, activity wrapper, session mapping, validators)

**Non-Goals:**
- Attack chain discovery across misconfig and other vuln classes (exploit agent focuses on single-finding verification)
- CSRF detection (deferred to future enhancement)
- Cryptographic weakness auditing (deferred to future enhancement)
- Business logic flaw detection (deferred to future enhancement)
- Modifying existing agent prompts to remove their tangential coverage of misconfig-related checks

## Decisions

### D1: VulnClass named `misconfig`

**Choice:** `misconfig`
**Alternatives considered:** `websec`, `httpsec`, `hardening`, `webconfig`
**Rationale:** Maps directly to OWASP A05:2021 "Security Misconfiguration" — the most widely recognized industry term. Follows existing naming convention (lowercase, single word). A pentester seeing `misconfig-vuln` immediately understands the scope. Open Redirect fits as "URL redirect handling misconfiguration."

### D2: Single agent covers all 6 sub-types

**Choice:** One `misconfig-vuln` agent with a unified methodology covering Open Redirect, Security Headers, CORS, Cookie Flags, Clickjacking, and Information Disclosure.
**Alternatives considered:** Separate agents per sub-type (too many agents, high parallel cost); split into "webconfig" + "logic" agents (methodology boundary is fuzzy).
**Rationale:** All sub-types share the same analysis question: "For each HTTP interaction point, should there be a security control? Is it correctly implemented?" This parallels how the auth agent covers 9 sub-checks (transport, rate limiting, session management, tokens, etc.) under one methodology. Open Redirect requires additional source→sink tracing (Phase B in the prompt), but this is embedded within the same sequential workflow.

### D3: Queue schema follows auth/ssrf pattern with optional Open Redirect fields

**Choice:** Base fields from auth/ssrf schema (`source_endpoint`, `vulnerable_code_location`, `missing_defense`, `exploitation_hypothesis`, `suggested_exploit_technique`) plus three optional fields for Open Redirect: `vulnerable_parameter`, `redirect_sink`, `existing_validation`.
**Alternatives considered:** Injection-style schema with source/path/sink/slot_type (over-engineered for config checks); separate schemas per sub-type (complex, inconsistent).
**Rationale:** The "defense missing" pattern fits all 6 sub-types. Open Redirect needs extra fields to guide the exploit agent (which parameter to manipulate, which redirect function to target, what validation exists but is insufficient).

### D4: Exploit agent uses proof-of-concept verification model

**Choice:** The exploit agent constructs concrete PoC attacks (malicious redirect URLs, iframe embedding pages, CORS exploitation JS, error-triggering requests) and verifies them against the live target.
**Alternatives considered:** Skip exploit agent entirely (config findings are "self-evident"); run exploit agent only for Open Redirect.
**Rationale:** While misconfig exploits are shallower than injection exploits, the live verification step produces concrete evidence (actual 3xx to evil.com, actual missing CSP header in response, actual CORS reflection). This distinguishes "code says it's vulnerable" from "we confirmed it on the live target." The proof levels adapt: Open Redirect has 4 levels (up to phishing PoC with token leakage), while headers/CORS have 2-3 levels (response confirmation + weaponized PoC).

### D5: Included in whitebox-only mode

**Choice:** `misconfig` is added to `WHITEBOX_VULN_CLASSES`.
**Alternatives considered:** Skip misconfig in whitebox mode (like XSS).
**Rationale:** Unlike XSS (which requires browser JS execution to verify payloads), misconfig sub-types can be analyzed purely through code audit: checking middleware configuration for headers, CORS policy objects, cookie option flags, redirect validation logic. The prompt will note that confidence may be lower without browser verification, and blackbox-mode can upgrade it later.

### D6: Playwright session uses `agent6`... but there are only 5 sessions

**Choice:** Reuse an existing session slot. The misconfig-vuln agent shares session `agent1` (same as injection-vuln and pre-recon-code, which never run concurrently with misconfig-vuln in the pipeline). The misconfig-exploit agent shares session `agent1` (same as injection-exploit).
**Alternatives considered:** Add agent6 session (requires Playwright infrastructure change).
**Rationale:** The current `PlaywrightSession` type is `'agent1' | 'agent2' | 'agent3' | 'agent4' | 'agent5'`. Adding a 6th session would require changes to the browser session infrastructure. Since misconfig agents run in the same parallel batch as the other 5 agents but the sessions are assigned per-agent (not per-pipeline), session 1 is safe to share because injection-vuln and misconfig-vuln are different agents running concurrently — but each agent gets its own session mapping. Looking at the pipeline, all 6 vuln agents run concurrently, so misconfig-vuln needs a unique session. The simplest fix is to extend `PlaywrightSession` to include `'agent6'` and add it to the session infrastructure.

**Revision:** After checking the pipeline execution, all 6 vuln agents DO run concurrently. So misconfig needs its own Playwright session. Extend `PlaywrightSession` type to `'agent1' | 'agent2' | 'agent3' | 'agent4' | 'agent5' | 'agent6'`.

## Risks / Trade-offs

**[Risk] Prompt length and agent focus** → The vuln-misconfig prompt must cover 6 distinct sub-type methodologies. If too long, the agent may lose focus or skip sub-types. **Mitigation:** Follow the auth agent pattern — numbered checklist items with explicit "for all" instructions that map to TodoWrite tasks. The auth prompt successfully covers 9 sub-checks in ~190 lines of methodology.

**[Risk] False positives on security headers** → Many applications intentionally omit certain headers (e.g., CSP for API-only services). **Mitigation:** The prompt instructs the agent to consider context (API vs. HTML page, SPA vs. server-rendered) before flagging. Queue entries include `notes` field for context.

**[Risk] Open Redirect scope creep** → The agent might over-investigate JavaScript-based redirects (`window.location = ...`) that are less exploitable than server-side 302 redirects. **Mitigation:** Prioritize server-side redirect sinks in the prompt methodology. JS redirects are checked but flagged with lower default confidence.

**[Risk] Parallel agent count increases from 5 to 6** → More concurrent agents means higher LLM API costs and potential rate limiting. **Mitigation:** The `max_concurrent_pipelines` config already exists and defaults to 5. Users can increase it to 6 or leave it at 5 (misconfig will queue and run when a slot opens).

**[Trade-off] No CSRF in V1** → CSRF protection auditing is deferred. This is a common misconfiguration but requires understanding state-changing endpoints and token mechanisms, adding significant prompt complexity. **Mitigation:** Can be added as a 7th sub-type in a follow-up change without architectural changes.
