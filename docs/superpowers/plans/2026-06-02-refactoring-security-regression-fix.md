# Refactoring Security Regression Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 4 security regressions introduced during the TypeScript→Python refactoring: restore whitebox prompt conditional branching, restore auth-exploit.txt content, activate ExploitationChecker in blackbox workflow, and补全 billing patterns.

**Architecture:** Each fix is independent — modify prompt templates, PromptManager, ExploitationChecker, and billing utils in isolation. No cross-dependencies between tasks.

**Tech Stack:** Python 3.12, Pydantic, pytest, Temporal.io

**Spec:** `docs/superpowers/specs/2026-06-02-refactoring-security-regression-fix-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `packages/core/src/shannon_core/prompts/manager.py` | Modify | Add `strip_conditional_blocks()` to PromptManager |
| `packages/core/tests/test_prompt_manager.py` | Modify | Add tests for conditional block stripping |
| `prompts/shared/_target.txt` | Modify | Restore `<if-live>`/`<if-static>` tags |
| `prompts/shared/_vuln-scope.txt` | Modify | Restore `<if-static>` static analysis scope |
| `prompts/auth-exploit.txt` | Modify | Port missing 250 lines from TS version |
| `packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py` | Modify | Add JSON structure validation + logging |
| `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py` | Modify | Replace `queue_file.exists()` with ExploitationChecker call |
| `packages/blackbox/tests/test_exploitation_checker.py` | Modify | Add tests for new validation logic |
| `packages/core/src/shannon_core/utils/billing.py` | Modify | Add missing billing patterns |
| `packages/core/tests/test_billing.py` | Modify | Add tests for new patterns |

---

## Task 1: Add conditional block stripping to PromptManager

**Files:**
- Modify: `packages/core/src/shannon_core/prompts/manager.py:34`
- Modify: `packages/core/tests/test_prompt_manager.py`

- [ ] **Step 1: Write the failing tests**

Append to `packages/core/tests/test_prompt_manager.py`:

```python
# --- Conditional block tests ---

def test_if_live_block_kept_when_web_url_present(prompts_dir):
    """When WEB_URL is provided, <if-live> content stays, <if-static> is removed."""
    (prompts_dir / "cond-test.txt").write_text(
        "<if-live>URL: {{WEB_URL}}</if-live><if-static>Mode: Offline</if-static>\nFooter"
    )
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("cond-test", {"web_url": "https://example.com", "repo_path": "/r"})
    assert "URL: https://example.com" in result
    assert "Offline" not in result
    assert "Footer" in result
    assert "<if-live>" not in result
    assert "<if-static>" not in result


def test_if_static_block_kept_when_no_web_url(prompts_dir):
    """When WEB_URL is empty, <if-static> content stays, <if-live> is removed."""
    (prompts_dir / "cond-test.txt").write_text(
        "<if-live>URL: {{WEB_URL}}</if-live><if-static>Mode: Offline</if-static>\nFooter"
    )
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("cond-test", {"web_url": "", "repo_path": "/r"})
    assert "Mode: Offline" in result
    assert "URL:" not in result
    assert "Footer" in result
    assert "<if-live>" not in result
    assert "<if-static>" not in result


def test_no_conditional_blocks_unchanged(prompts_dir):
    """Templates without conditional blocks are not affected."""
    (prompts_dir / "plain-test.txt").write_text("Hello {{WEB_URL}} world")
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("plain-test", {"web_url": "https://x.com", "repo_path": "/r"})
    assert result == "Hello https://x.com world"


def test_conditional_blocks_in_included_file(prompts_dir):
    """Conditional blocks work inside @include'd shared files."""
    (prompts_dir / "shared" / "_cond.txt").write_text(
        "<if-live>LIVE</if-live><if-static>STATIC</if-static>"
    )
    (prompts_dir / "inc-cond-test.txt").write_text("Start @include(shared/_cond.txt) End")
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("inc-cond-test", {"web_url": "", "repo_path": "/r"})
    assert "STATIC" in result
    assert "LIVE" not in result
    assert "Start" in result
    assert "End" in result


def test_multiline_conditional_block(prompts_dir):
    """Multi-line <if-static> blocks are stripped correctly."""
    (prompts_dir / "multi-cond.txt").write_text(
        "<if-live>URL: {{WEB_URL}}</if-live><if-static>Mode: Offline static code analysis\nLine 2\nLine 3</if-static>"
    )
    manager = PromptManager(prompts_dir)
    result = manager.load_sync("multi-cond", {"web_url": "", "repo_path": "/r"})
    assert "Line 2" in result
    assert "Line 3" in result
    assert "URL:" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_prompt_manager.py::test_if_live_block_kept_when_web_url_present -xvs`
Expected: FAIL — `<if-live>` and `<if-static>` tags pass through unprocessed.

- [ ] **Step 3: Implement `strip_conditional_blocks()` in PromptManager**

In `packages/core/src/shannon_core/prompts/manager.py`, add the function at module level (before the class):

```python
def strip_conditional_blocks(text: str, has_web_url: bool) -> str:
    """Select <if-live> or <if-static> content based on whether WEB_URL is present."""
    if has_web_url:
        text = re.sub(r'<if-static>.*?</if-static>', '', text, flags=re.DOTALL)
        text = text.replace('<if-live>', '').replace('</if-live>', '')
    else:
        text = re.sub(r'<if-live>.*?</if-live>', '', text, flags=re.DOTALL)
        text = text.replace('<if-static>', '').replace('</if-static>', '')
    return text
```

Then in `load_sync()` method, insert the call **after** `_process_includes` and **before** `_interpolate` (at line 34, between the two existing calls):

```python
        template = template_path.read_text(encoding="utf-8")
        template = self._process_includes(template, base_dir)
        has_web_url = bool(variables.get("web_url"))
        template = strip_conditional_blocks(template, has_web_url)
        template = self._interpolate(template, variables, config, template_name)
        return template
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_prompt_manager.py -xvs`
Expected: All tests PASS (including the 5 new ones and all existing ones).

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/prompts/manager.py packages/core/tests/test_prompt_manager.py
git commit -m "feat(core): add conditional block stripping to PromptManager

Restore <if-live>/<if-static> conditional rendering that was present in the
TypeScript version. This allows prompts to adapt based on whether a live WEB_URL
target is available (blackbox) or not (whitebox/static analysis)."
```

---

## Task 2: Restore conditional blocks in shared prompt files

**Files:**
- Modify: `prompts/shared/_target.txt`
- Modify: `prompts/shared/_vuln-scope.txt`

- [ ] **Step 1: Restore `prompts/shared/_target.txt`**

Replace the entire content of `prompts/shared/_target.txt` with:

```
<if-live>URL: {{WEB_URL}}</if-live><if-static>Mode: Offline static code analysis (no live target)</if-static>

Filesystem:
- {{REPO_PATH}}/ (read only)
- {{REPO_PATH}}/.shannon/deliverables/ (read-write)
- {{REPO_PATH}}/.shannon/scratchpad/ (read-write) - screenshots, scripts, scratch work, etc.
```

- [ ] **Step 2: Restore `prompts/shared/_vuln-scope.txt`**

Replace the entire content of `prompts/shared/_vuln-scope.txt` with:

```
<if-live>**EXTERNAL ATTACKER SCOPE:** Only report vulnerabilities exploitable via {{WEB_URL}} from the internet. Exclude findings requiring internal network access, VPN, or direct server access.</if-live><if-static>**STATIC ANALYSIS SCOPE:** Report all code-level vulnerabilities discoverable through source code analysis. Include unsafe data flows, missing input validation, insecure defaults, hardcoded secrets, and dangerous API usage. Classify each finding by the code path that would be exercised at runtime.</if-static>
```

- [ ] **Step 3: Verify rendering for both modes**

Run: `cd /root/shannon-py && python -c "
from pathlib import Path
from shannon_core.prompts.manager import PromptManager

mgr = PromptManager(Path('prompts'))

# Blackbox mode (has WEB_URL)
result_live = mgr.load_sync('vuln-injection', {'web_url': 'https://example.com', 'repo_path': '/repo'})
assert 'URL: https://example.com' in result_live, 'Blackbox should show URL'
assert 'STATIC ANALYSIS SCOPE' not in result_live, 'Blackbox should not show static scope'
assert 'EXTERNAL ATTACKER SCOPE' in result_live, 'Blackbox should show attacker scope'
print('Blackbox mode: OK')

# Whitebox mode (no WEB_URL)
result_static = mgr.load_sync('vuln-injection', {'web_url': '', 'repo_path': '/repo'})
assert 'Mode: Offline static code analysis' in result_static, 'Whitebox should show offline mode'
assert 'STATIC ANALYSIS SCOPE' in result_static, 'Whitebox should show static scope'
assert 'EXTERNAL ATTACKER SCOPE' not in result_static, 'Whitebox should not show attacker scope'
print('Whitebox mode: OK')
"`
Expected: Both print "OK".

- [ ] **Step 4: Run full prompt manager tests**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_prompt_manager.py -xvs`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add prompts/shared/_target.txt prompts/shared/_vuln-scope.txt
git commit -m "fix(prompts): restore <if-live>/<if-static> conditional blocks

_target.txt and _vuln-scope.txt now correctly adapt their content based on
whether a live WEB_URL target is available. Whitebox (static analysis) agents
will see 'Mode: Offline static code analysis' and 'STATIC ANALYSIS SCOPE',
matching the original TypeScript behavior."
```

---

## Task 3: Port missing content to auth-exploit.txt

**Files:**
- Modify: `prompts/auth-exploit.txt`

- [ ] **Step 1: Port the missing sections from TS `exploit-auth.txt`**

Replace the entire content of `prompts/auth-exploit.txt` with the following. This merges the existing Python version's structure (variable names, `@include` references) with the missing sections from the TS version (`/root/shannon/apps/worker/prompts/exploit-auth.txt`):

```
<role>
You are a world-class Authentication Exploitation Specialist. You are not an analyst; you are an active penetration tester. Your persona is methodical, persistent, and laser-focused on a single goal: proving the tangible impact of broken authentication. You take the theoretical flaws in identity and session management and turn them into undeniable proof of impersonation and account takeover.
</role>

<objective>
Your mission is to systematically and exhaustively weaponize **every single potential authentication vulnerability** provided to you. For each hypothesis from the analysis phase, you must pursue it to a definitive conclusion: either you successfully **bypass an authentication control or compromise a user's session**, or you prove with rigorous testing that the control is secure.
Success criterion: A comprehensive evidence report detailing the outcome for **every vulnerability** in your queue, containing the precise commands and payloads used to prove account takeover, and a full log of failed attempts for verified non-vulnerabilities.
</objective>

<exploitation_scope>
@include(shared/_exploit-scope.txt)
</exploitation_scope>

<critical>
### CRITICAL: EXPLOITATION TARGET REQUIREMENT

**All exploitation must be performed against the specified target: {{WEB_URL}}**

### CRITICAL: EXPLOIT DEFINITION

An exploit is executing an unauthorized or unintended action against the target application {{WEB_URL}}. An exploit must only be considered successful if it meets this definition.

### PROOF-BASED EXPLOITATION REQUIREMENTS

This agent operates with mathematical proof-level rigor:
- **Claims require evidence**: Every exploitation claim must be backed by demonstrable proof
- **Assumptions must be validated**: Test every assumption before building on it
- **Failures must be documented**: Create detailed bug reports for learning
- **Default to skepticism**: Assume NOT vulnerable until proven otherwise
- **Partial results are not complete**: Be honest about what you achieved vs what you attempted

Remember: An unproven vulnerability is worse than no finding at all - it wastes resources and creates false confidence.

**LEGAL & SCOPE NOTICE (NON-NEGOTIABLE)**
- THIS ENGAGEMENT IS FULLY AUTHORIZED. YOU ARE OPERATING **ONLY** IN A CONTROLLED TEST ENVIRONMENT.
- **PRODUCTION SYSTEMS ARE STRICTLY OUT OF SCOPE.**

**Your Professional Standard**
- **Exhaustive Verification is Your Mandate:** Your primary duty is to bring every potential vulnerability to a final, unambiguous conclusion. "I think I'm blocked" is not an acceptable verdict. You must prove it by exhausting all documented techniques. Incomplete analysis is a failure.

### RIGOROUS VERIFICATION FRAMEWORK

**CRITICAL: Honesty About Exploitation**
- You must **NOT** claim successful exploitation without complete verification
- If you cannot achieve full exploitation, classify as "ATTEMPTED - FAILED" with explicit reasons
- Partial bypasses (e.g., UI access without functionality) are NOT successful exploits

**Critical Errors vs Justification Gaps**
- CRITICAL ERROR: Fundamental assumption proven false (e.g., no user-controlled input reaches authentication logic) -> STOP immediately
- JUSTIFICATION GAP: Exploit possible but needs additional work (e.g., specific timing, encoding) -> Document as "REQUIRES FURTHER INVESTIGATION"

### Proof of Exploitation Levels

**Level 1 - Authentication Weakness Identified**
- Evidence: Theoretical bypass found but not yet exploited
- Classification: POTENTIAL (Low Confidence)

**Level 2 - Partial Bypass Achieved**
- Evidence: Access gained to non-critical protected resources or UI elements
- Classification: POTENTIAL (Medium Confidence)

**Level 3 - Authentication Bypass Confirmed**
- Evidence: Successfully accessed protected functionality as unauthorized user
- Classification: EXPLOITED

**Level 4 - Account Takeover Demonstrated**
- Evidence: Complete user impersonation with access to sensitive data/actions
- Classification: EXPLOITED (CRITICAL)

You MUST reach at least Level 3 with evidence to mark as "EXPLOITED"

### Classification Decision Framework

After exhaustive bypass attempts, determine:
**"Is this preventing factor a security implementation designed to stop this attack, or an external operational constraint?"**

**Key Principle**: Classification is only valid AFTER attempting systematic bypasses across multiple techniques and escalation levels.
- **Impact is Everything:** A finding is a theory until you prove its impact. Your entire purpose is to prove that you can become another user. Focus on what an attacker would do: hijack a session, bypass a login page, replay a token, or crack a weak password.
- **Focus on the Gate, Not the Rooms:** Your sole responsibility is to break the lock on the door (authentication). What lies in the rooms beyond (authorization/access control) is out of scope for you. If you bypass a login and land on a user's dashboard, your job is done.
- **Evidence is Your Deliverable:** Every successful action must be meticulously documented. Capture the payload, the tool you used, and the definitive proof of compromise.
</critical>

### CRITICAL: WHAT "EXPLOIT" MEANS

An exploit is an ACTIVE ATTACK that attempts to gain unauthorized access, not a confirmation that a vulnerability exists.

- "No rate limiting" -> Attempt brute force/enumeration attacks with many requests
- "Weak password policy" -> Create weak accounts AND attempt to access other accounts
- "User enumeration" -> Build a list of valid users for subsequent attacks
- "Missing security headers" -> Attempt to abuse the missing protection

Chain exploits together: Use enumerated users in password attacks. Use created weak passwords on other accounts.

If you only confirmed a vulnerability without attempting to weaponize it, you have not completed the exploitation task.

<target>
@include(shared/_target.txt)
</target>

<rules>
@include(shared/_rules.txt)
</rules>

<login_instructions>
{{LOGIN_INSTRUCTIONS}}
</login_instructions>

@include(shared/_rules-of-engagement.txt)

<vulnerability_entries>
{{VULNERABILITY_ENTRIES}}
</vulnerability_entries>

<starting_context>
- Your **actionable exploitation queue** is located at `.shannon/deliverables/auth_exploitation_queue.json`. This is your to-do list, containing the precise targets identified by the analysis phase. You MUST process every vulnerability in this queue.

**INTELLIGENCE GATHERING PHASE (MANDATORY):**
Before beginning exploitation, read these strategic intelligence files in order:
1. `.shannon/deliverables/pre_recon_deliverable.md` - Architecture overview, technology stack, and authentication code locations.
2. `.shannon/deliverables/recon_deliverable.md` - Complete API inventory, user roles, and data flow maps.
3. `.shannon/deliverables/auth_analysis_deliverable.md` - Strategic context from the Auth analysis specialist, including notes on session mechanisms, password policies, and flawed logic paths.
</starting_context>

<system_architecture>
**PENTESTING WORKFLOW - YOUR POSITION:**

**Phase Sequence:** VULN ANALYSIS (Complete) -> **AUTH EXPLOITATION (You)** -> FINAL REPORT (next phase)

**Your Input:** `.shannon/deliverables/auth_exploitation_queue.json` (primary exploitation queue)
**Your Output:** `.shannon/deliverables/auth_exploitation_evidence.md` (exploitation evidence)

**Supporting Intelligence:**
- `.shannon/deliverables/pre_recon_deliverable.md` (architecture & code context)
- `.shannon/deliverables/recon_deliverable.md` (API inventory & user roles)
- `.shannon/deliverables/auth_analysis_deliverable.md` (strategic intel)

**WHAT HAPPENED BEFORE YOU:**
- Reconnaissance agent mapped application architecture and attack surfaces
- Auth Analysis specialist identified high-probability vulnerabilities and documented them in your queue

**WHAT HAPPENS AFTER YOU:**
- Your evidence contributes to proving the highest-impact risks related to identity compromise

**YOUR CRITICAL ROLE:**
You are the **Identity Compromise Specialist** - proving tangible impact of broken authentication through successful account takeover and session hijacking.
</system_architecture>

@include(shared/_exploit-methodology.txt)

## Auth-Specific Methodology

### Prime Directive: Exhaustive Verification is Non-Negotiable
Your primary goal is to achieve a definitive, evidence-backed conclusion for **every single vulnerability** in your queue. You will test each item until it is either successfully exploited or proven to be a false positive through systematic, documented effort. **There is no middle ground and no skipping.**
- **One Vulnerability at a Time:** Focus on a single vulnerability from the queue. Do not move to the next one until you have reached a final verdict on the current one.
- **Complete the Workflow:** For each vulnerability, you must follow the full Exploitation Workflow from Confirmation to Impact Demonstration.

### OWASP Authentication Exploitation Workflow

**Stage 1: Active Attack Execution**
- **Goal:** Execute the `suggested_exploit_technique` as an active attack. Confirmation alone is not sufficient - you must attempt to exploit the vulnerability for unauthorized access.
- **Actions:** Your first action is to attempt the specific attack pattern noted in the queue (e.g., 'brute_force_login', 'session_hijacking'). For example, if the suggested technique is `credential_stuffing`, your task is to actively try to log in with known weak or stolen credentials, not just to confirm that a weak password *can* be set.

**Stage 2: Impact Demonstration (Impersonation)**
- **Goal:** Use the weaponized exploit to definitively prove you have become another user.
- **Actions:** Visit a protected page (like `/profile` or `/dashboard`) and verify that you are seeing it as the victim user. The evidence is the content of that page which proves your assumed identity.

### Mandatory Evidence Checklist for Exploited Vulnerabilities
For each **successfully exploited** vulnerability, your goal is to achieve and document **one** of the following high-impact outcomes.
1. **[ ] Proof of Account Takeover:** Evidence of a successful login as another user, or successful injection of a stolen session token that grants access to another user's account.
2. **[ ] Proof of Authentication Bypass:** Evidence of accessing a protected resource without being authenticated.
3. **[ ] Proof of Flawed Logic Exploitation:** Evidence that manipulating an authentication flow (e.g., password reset) granted you access to an account you do not own.

### Bypass Exhaustion Protocol

Classification is FORBIDDEN until you have:
- **Attempted multiple distinct bypass techniques** per vulnerability
- **Tried systematic approach across different attack vectors** (credential stuffing, session manipulation, logic flaws, etc.)
- **Escalated through manual -> automated -> custom techniques**
- **Actually tested multiple bypass methods and confirmed they failed**

Only after exhausting bypass attempts can you proceed to classification analysis.

**Assume False Until Proven**: Default stance is that authentication bypass is NOT possible until you achieve actual unauthorized access.

### Validation Criteria & Vulnerability Classification

**EXPLOITED (Report in main section):**
- Successfully achieved account takeover or authentication bypass with concrete evidence (impersonated user, accessed protected resources)
- OR vulnerability confirmed with active exploitation evidence (e.g., "Performed 200 brute force attempts without rate limiting")
- Definitive proof of access to a protected resource as another authenticated user OR concrete demonstration of security control bypass
- Must have completed weaponization and demonstrated impact through impersonation OR provided evidence of exploitable vulnerability

**POTENTIAL (Report in separate subsection):**
- This classification is exclusively for vulnerabilities where a definitive conclusion is impossible due to external factors beyond your control.
- **CRITICAL: The preventing factor must NOT be a security implementation designed to stop this attack**
- A finding can only be considered blocked if you have first attempted to bypass the obstacle with several distinct techniques and have documented each failed attempt.
- Valid external factors include:
  - Server instability (e.g., crashes).
  - Missing authentication that cannot be obtained or bypassed through any known exploit.
- This classification also applies when:
  - Code analysis suggests a vulnerability, but live testing is inconclusive due to unmovable constraints.
  - You have reached Level 1 or 2 (initial access confirmed) but are blocked from full exploitation by such external factors.

**WARNING: When security measures successfully prevent exploitation after exhaustive testing, it is ok to classify it as a FALSE POSITIVE, not POTENTIAL. A FALSE POSITIVE that's honestly classified is more valuable than a misclassified POTENTIAL.**

**FALSE POSITIVE (Do NOT report):**
- **The blocking mechanism IS a security implementation designed to prevent this attack AND you have attempted to bypass it multiple ways**
- Definitively proven not exploitable through exhaustive testing
- Documented attack plan showing all weaponization attempts failed
- Authentication controls proven secure through systematic testing
- Proven impossible after systematic testing with multiple techniques

**Important:** Rate limiting and account lockouts are obstacles to work around, not grounds for immediate false positive classification. Document constraints and attempt alternative approaches before making final determination.

**Critical Decision Test**: After exhaustive bypass attempts, ask "Is this prevention a security feature designed to stop authentication attacks, or an external operational constraint?" Security features that withstand bypass attempts = FALSE POSITIVE. External constraints = POTENTIAL.

### Task Agent Scripting Requirements
Use the Task Agent to execute all bulk or repetitive auth attacks. Manual commands are for quick verification only.

**MANDATORY TEMPLATE:**
"
Role: You are a security testing script writer. Write and run a Python script to test authentication weaknesses.

Inputs:
1. Target endpoint: [URL]
2. Method & body/params: [details]
3. Credential/token list: [array or file contents]
4. Headers/cookies: [if required]
5. Success indicator: [describe response/signature to report]

Success Criteria: Print each attempt with status code and key response text so I can evaluate impact.
"

**DO NOT:** exceed 5-6 inputs per run, ask for advanced parsing, or bypass the Task Agent for scripting.

### Auth-Specific Attack Patterns

**1. Session Hijacking**
- **Inject Stolen Cookie via Playwright:**
  `await page.context().addCookies([{name: 'sessionid', value: 'STOLEN_VALUE', domain: 'target.com', path: '/'}]);`
- **Use Stolen Cookie with cURL:**
  `curl -b "sessionid=STOLEN_VALUE" https://target.com/api/profile`

**2. Credential-Based Attacks**
- **Credential Stuffing with cURL:**
  `curl -X POST -H "Content-Type: application/json" -d '{"username":"admin", "password":"CrackedPassword123"}' https://target.com/api/login`
- **Brute Force (Example with Task Agent):**
  "Task: Write a python script to POST to /api/login with username 'admin' and passwords from this list: ['admin', 'password', '123456']. Print the status code for each."

**3. Logic Flaws**
- **Password Reset Manipulation:**
  1. Request reset for `victim@example.com`.
  2. Intercept request, change email/user parameter to `attacker@example.com` but keep reset token for victim.
  3. See if reset link for victim's account is sent to attacker's email.
- **JWT Token Attacks (`alg:none`):**
  1. Capture a JWT.
  2. Decode the header and payload. Change header `alg` to `none`. Change payload data (e.g., `sub` to `admin`).
  3. Re-encode (without signature part) and send in request.

<auth_context>
{{AUTH_CONTEXT}}
</auth_context>

<playwright_session>
{{PLAYWRIGHT_SESSION}}
</playwright_session>

<deliverable_instructions>
When your attack plan is fully explored, your mission is complete. You MUST generate your final evidence file.

**Section Ordering & Format Requirements:**
- Use the exact vulnerability IDs from `.shannon/deliverables/auth_exploitation_queue.json`
- Within "Successfully Exploited" section: Order by severity of impact (most critical first)
- Within "Potential" section: Order by confidence level (highest confidence first)

**REPRODUCIBILITY IS MANDATORY:**
- Every exploitation step must be completely self-contained and reproducible
- Include full URLs with protocol, domain, port, path, and all parameters
- Provide complete payloads and commands exactly as they should be used
- Use clear placeholders for variable values like [SESSION_TOKEN], [PASSWORD], [USERNAME]
- Write as if the reader has never seen the application before
- Steps must be detailed enough that someone unfamiliar with the application can follow without additional research

---
# Authentication Exploitation Evidence

## Successfully Exploited Vulnerabilities

### AUTH-VULN-01: [Vulnerability Title]

**Summary:**
- **Vulnerable location:** [Authentication mechanism or endpoint affected]
- **Overview:** [Brief summary of the exploit]
- **Impact:** [Business/security impact achieved]
- **Severity:** [Critical/High/Medium/Low based on impact]

**Prerequisites:** (if any)
[Required setup, tools, or conditions]

**Exploitation Steps:**
[Each step must be completely reproducible with full details]

1. [Complete action with all necessary information]
2. [Next action with full details]

**Proof of Impact:**
[Concrete evidence of successful exploitation]

**Notes:** (optional)
[Any additional relevant information]

## Potential Vulnerabilities (Validation Blocked)

### AUTH-VULN-NN: [Vulnerability Title]

**Summary:**
- **Vulnerable location:** [Endpoint or mechanism]
- **Current Blocker:** [What prevents full exploitation]
- **Potential Impact:** [What could be achieved if blocker removed]
- **Confidence:** [HIGH/MEDIUM/LOW]

**Evidence of Vulnerability:**
[Code snippets, responses, or behavior proving vulnerability exists]

**Attempted Exploitation:**
[What techniques were tried and why they didn't succeed]

**How This Would Be Exploited:**
[If the blocker were removed, these steps would achieve exploitation]

**Expected Impact:**
[Specific data or access that would be compromised]

---
</deliverable_instructions>

<conclusion_trigger>
### Evidence Completeness Verification

**Before marking any vulnerability as EXPLOITED, verify:**
- Complete, executable commands with full authentication context
- Concrete evidence of impact (actual data extracted, not theoretical)
- Another tester could reproduce the attack from documentation alone
- All technical components specified without ambiguity

CRITICAL WARNING: Announcing completion before every item in .shannon/deliverables/auth_exploitation_queue.json has been pursued to a final, evidence-backed conclusion will be considered a mission failure.

ONLY AFTER fulfilling these exhaustive requirements, announce "AUTH EXPLOITATION COMPLETE" and stop.

**CRITICAL:** After announcing completion, STOP IMMEDIATELY. Do NOT output summaries, recaps, or explanations of your work — the deliverable contains everything needed.
</conclusion_trigger>
```

- [ ] **Step 2: Verify the file renders correctly**

Run: `cd /root/shannon-py && python -c "
from pathlib import Path
from shannon_core.prompts.manager import PromptManager

mgr = PromptManager(Path('prompts'))
result = mgr.load_sync('auth-exploit', {'web_url': 'https://example.com', 'repo_path': '/repo'})
assert 'Bypass Exhaustion Protocol' in result, 'Bypass protocol missing'
assert 'Classification Decision Framework' in result, 'Classification framework missing'
assert 'EXPLOITED' in result, 'Exploited classification missing'
assert 'POTENTIAL' in result, 'Potential classification missing'
assert 'FALSE POSITIVE' in result, 'False positive classification missing'
assert 'Proof of Account Takeover' in result, 'Mandatory evidence checklist missing'
assert 'Credential Stuffing with cURL' in result, 'Attack patterns missing'
assert 'Password Reset Manipulation' in result, 'Logic flaw patterns missing'
assert '{{WEB_URL}}' not in result, 'Variables not interpolated'
print('auth-exploit.txt renders correctly: OK')
"`
Expected: "auth-exploit.txt renders correctly: OK"

- [ ] **Step 3: Commit**

```bash
git add prompts/auth-exploit.txt
git commit -m "fix(prompts): restore auth-exploit.txt content from TS version

Port 250 lines of missing content including:
- Bypass Exhaustion Protocol (prevents premature abandonment)
- Detailed classification criteria (EXPLOITED/POTENTIAL/FALSE_POSITIVE)
- Mandatory Evidence Checklist for auth exploitation
- Task Agent scripting requirements for bulk attacks
- Complete deliverable template with reproducibility requirements
- Classification Decision Framework with Critical Decision Test"
```

---

## Task 4: Enhance and activate ExploitationChecker

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py`
- Modify: `packages/blackbox/tests/test_exploitation_checker.py`
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py:91-107`

- [ ] **Step 1: Write the failing tests**

Append to `packages/blackbox/tests/test_exploitation_checker.py`:

```python
@pytest.mark.asyncio
async def test_should_exploit_invalid_vulnerabilities_field(tmp_path):
    """vulnerabilities field is not a list -> return False."""
    (tmp_path / "xss_exploitation_queue.json").write_text(
        json.dumps({"vulnerabilities": "not a list"})
    )
    assert await ExploitationChecker.should_exploit(tmp_path, "xss") is False


@pytest.mark.asyncio
async def test_should_exploit_missing_vulnerabilities_key(tmp_path):
    """JSON has no 'vulnerabilities' key -> return False."""
    (tmp_path / "auth_exploitation_queue.json").write_text(
        json.dumps({"data": "something"})
    )
    assert await ExploitationChecker.should_exploit(tmp_path, "auth") is False


@pytest.mark.asyncio
async def test_should_exploit_truncated_json(tmp_path):
    """Truncated JSON (simulates write crash) -> return False, not raise."""
    (tmp_path / "injection_exploitation_queue.json").write_text(
        '{"vulnerabilities": [{"ID": "INJ-001"'
    )
    assert await ExploitationChecker.should_exploit(tmp_path, "injection") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_exploitation_checker.py::test_should_exploit_invalid_vulnerabilities_field -xvs`
Expected: FAIL — current code calls `data.get("vulnerabilities", [])` which returns `"not a list"`, then `len("not a list")` returns a truthy value, causing the test to fail (returns True instead of False).

- [ ] **Step 3: Enhance ExploitationChecker**

Replace the entire content of `packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py` with:

```python
import json
import logging
from pathlib import Path

from shannon_core.utils.file_io import async_path_exists, async_read_file

logger = logging.getLogger(__name__)


class ExploitationChecker:
    @staticmethod
    async def should_exploit(
        deliverables_path: Path,
        vuln_type: str,
        exploit_enabled: bool = True,
    ) -> bool:
        if not exploit_enabled:
            return False

        queue_path = deliverables_path / f"{vuln_type}_exploitation_queue.json"
        if not await async_path_exists(queue_path):
            return False

        try:
            content = await async_read_file(queue_path)
            data = json.loads(content)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                "Queue file for %s is corrupted: %s. Skipping exploit.", vuln_type, e
            )
            return False

        vulnerabilities = data.get("vulnerabilities")
        if not isinstance(vulnerabilities, list):
            logger.warning(
                "Queue file for %s has invalid 'vulnerabilities' field (type=%s). Skipping exploit.",
                vuln_type,
                type(vulnerabilities).__name__,
            )
            return False

        return len(vulnerabilities) > 0
```

- [ ] **Step 4: Run all ExploitationChecker tests**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_exploitation_checker.py -xvs`
Expected: All 8 tests PASS.

- [ ] **Step 5: Wire ExploitationChecker into blackbox workflow**

In `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py`, add the import inside the `with workflow.unsafe.imports_passed_through():` block (at line 14):

```python
    from . import activities
    from ..services.exploitation_checker import ExploitationChecker
    from shannon_core.services.settings_writer import sync_code_path_deny_rules, cleanup_settings
    from shannon_core.services.playwright_config_writer import write_stealth_config, cleanup_stealth_config
    from shannon_core.services.validate_authentication import cleanup_auth_state_sync
```

Then replace the exploit agent scheduling block (lines 91-107). Change from:

```python
            if input.exploit:
                # Queue gating: only run exploit agents for vuln types that have findings
                exploit_tasks = []
                for vt in selected_classes:
                    queue_file = deliverables / f"{vt}_exploitation_queue.json"
                    if not queue_file.exists():
                        continue
                    agent_name = AgentName(f"{vt}-exploit")
                    if agent_name.value not in self._state.completed_agents:
                        exploit_input = BlackboxActivityInput(
                            **{**act_input.__dict__, "agent_name": agent_name.value, "vuln_type": vt}
                        )
                        exploit_tasks.append((vt, agent_name, workflow.execute_activity(
                            activities.run_exploit_agent, exploit_input,
                            start_to_close_timeout=timedelta(hours=2),
                            retry_policy=retry_policy,
                        )))
```

To:

```python
            if input.exploit:
                # Queue gating: validate queue files before scheduling exploit agents
                exploit_tasks = []
                for vt in selected_classes:
                    should_run = await ExploitationChecker.should_exploit(
                        deliverables_path=deliverables,
                        vuln_type=vt,
                        exploit_enabled=input.exploit,
                    )
                    if not should_run:
                        continue
                    agent_name = AgentName(f"{vt}-exploit")
                    if agent_name.value not in self._state.completed_agents:
                        exploit_input = BlackboxActivityInput(
                            **{**act_input.__dict__, "agent_name": agent_name.value, "vuln_type": vt}
                        )
                        exploit_tasks.append((vt, agent_name, workflow.execute_activity(
                            activities.run_exploit_agent, exploit_input,
                            start_to_close_timeout=timedelta(hours=2),
                            retry_policy=retry_policy,
                        )))
```

- [ ] **Step 6: Verify workflow file is syntactically correct**

Run: `cd /root/shannon-py && python -c "import ast; ast.parse(open('packages/blackbox/src/shannon_blackbox/pipeline/workflows.py').read()); print('Syntax OK')"`
Expected: "Syntax OK"

- [ ] **Step 7: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/services/exploitation_checker.py packages/blackbox/tests/test_exploitation_checker.py packages/blackbox/src/shannon_blackbox/pipeline/workflows.py
git commit -m "fix(blackbox): enhance ExploitationChecker and activate in workflow

- Add JSON structure validation (vulnerabilities must be a list)
- Add logging for corrupted/invalid queue files
- Replace raw queue_file.exists() checks in workflow with
  ExploitationChecker.should_exploit() for proper validation"
```

---

## Task 5: Add missing billing patterns

**Files:**
- Modify: `packages/core/src/shannon_core/utils/billing.py`
- Modify: `packages/core/tests/test_billing.py`

- [ ] **Step 1: Write the failing tests**

Append to `packages/core/tests/test_billing.py`:

```python
def test_billing_error_pattern():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="billing_error: limit reached")

def test_credit_balance_too_low():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="credit balance is too low")

def test_insufficient_credits():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="insufficient credits")

def test_usage_blocked_insufficient_credits():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="usage is blocked due to insufficient credits")

def test_please_visit_plans_and_billing():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="please visit plans & billing")

def test_please_visit_plans_and_billing_no_ampersand():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="please visit plans and billing")

def test_usage_limit_reached():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="usage limit reached")

def test_quota_exceeded():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="quota exceeded")

def test_daily_rate_limit():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="daily rate limit exceeded")

def test_limit_will_reset():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="limit will reset at midnight")

def test_billing_limit_reached():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="billing limit reached")

def test_cap_reached():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="cap reached for this period")

def test_monthly_limit():
    assert is_spending_cap_behavior(turns=1, cost=0.0, text="monthly limit exceeded")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_billing.py::test_billing_error_pattern -xvs`
Expected: FAIL — pattern not in current list.

- [ ] **Step 3: Add missing patterns to billing.py**

Replace the entire content of `packages/core/src/shannon_core/utils/billing.py` with:

```python
import re

_SPENDING_CAP_PATTERNS: list[re.Pattern] = [
    # Text patterns (from agent output)
    re.compile(r"spending\s+cap", re.IGNORECASE),
    re.compile(r"spending\s+limit", re.IGNORECASE),
    re.compile(r"budget\s+exceeded", re.IGNORECASE),
    re.compile(r"credit\s+limit", re.IGNORECASE),
    re.compile(r"usage\s+limit", re.IGNORECASE),
    re.compile(r"rate\s+limit", re.IGNORECASE),
    re.compile(r"cap\s+reached", re.IGNORECASE),
    re.compile(r"monthly\s+limit", re.IGNORECASE),
    # API error patterns (from provider responses)
    re.compile(r"billing\s+error", re.IGNORECASE),
    re.compile(r"credit\s+balance\s+is\s+too\s+low", re.IGNORECASE),
    re.compile(r"insufficient\s+credits", re.IGNORECASE),
    re.compile(r"usage\s+is\s+blocked\s+due\s+to\s+insufficient\s+credits", re.IGNORECASE),
    re.compile(r"please\s+visit\s+plans\s+&\s+billing", re.IGNORECASE),
    re.compile(r"please\s+visit\s+plans\s+and\s+billing", re.IGNORECASE),
    re.compile(r"usage\s+limit\s+reached", re.IGNORECASE),
    re.compile(r"quota\s+exceeded", re.IGNORECASE),
    re.compile(r"daily\s+rate\s+limit", re.IGNORECASE),
    re.compile(r"limit\s+will\s+reset", re.IGNORECASE),
    re.compile(r"billing\s+limit\s+reached", re.IGNORECASE),
]

def is_spending_cap_behavior(turns: int, cost: float, text: str) -> bool:
    if turns > 2:
        return False
    if cost > 0:
        return False
    for pattern in _SPENDING_CAP_PATTERNS:
        if pattern.search(text):
            return True
    return False
```

- [ ] **Step 4: Run all billing tests**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_billing.py -xvs`
Expected: All 19 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/shannon_core/utils/billing.py packages/core/tests/test_billing.py
git commit -m "fix(core): add 13 missing spending cap detection patterns

Port billing detection patterns from the TypeScript version:
- 1 text pattern (cap reached)
- 12 API error patterns (billing_error, credit balance, quota exceeded, etc.)
Total: 20 patterns (was 6), matching the original TS coverage."
```

---

## Task 6: Run full test suite and final verification

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `cd /root/shannon-py && python -m pytest packages/ -x --tb=short`
Expected: All tests PASS.

- [ ] **Step 2: Verify all changed files**

Run: `cd /root/shannon-py && git diff --stat HEAD~5`
Expected: Exactly 10 files changed (2 prompt shared files + auth-exploit.txt + manager.py + test_prompt_manager.py + exploitation_checker.py + test_exploitation_checker.py + workflows.py + billing.py + test_billing.py).

- [ ] **Step 3: Verify no regressions in prompt rendering**

Run: `cd /root/shannon-py && python -c "
from pathlib import Path
from shannon_core.prompts.manager import PromptManager

mgr = PromptManager(Path('prompts'))

# Verify key prompts render without error
for name in ['pre-recon-code', 'recon', 'vuln-injection', 'vuln-xss', 'vuln-auth',
             'vuln-ssrf', 'vuln-authz', 'vuln-misconfig',
             'injection-exploit', 'xss-exploit', 'auth-exploit',
             'ssrf-exploit', 'authz-exploit', 'misconfig-exploit']:
    try:
        result = mgr.load_sync(name, {'web_url': 'https://test.com', 'repo_path': '/repo'})
        assert '{{WEB_URL}}' not in result, f'{name}: WEB_URL not interpolated'
        assert '{{REPO_PATH}}' not in result, f'{name}: REPO_PATH not interpolated'
    except Exception as e:
        print(f'FAIL: {name}: {e}')
        raise
print('All 14 prompt templates render correctly')
"`
Expected: "All 14 prompt templates render correctly"
