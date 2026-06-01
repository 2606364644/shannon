# Pipeline Defects D4, D5, D7 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three engineering-layer defects in Shannon's whitebox pipeline: error state overwrite (D4), missing vuln agent retry (D5), and misconfig prompt inconsistency (D7).

**Architecture:** D4 changes the error tracking from a single string to a list of strings in both whitebox and blackbox pipelines. D5 adds retry policy to whitebox vuln agents. D7 rewrites the misconfig prompt to match the structural standard used by other vuln prompts.

**Tech Stack:** Python dataclasses, Temporal workflows, Jinja2-style prompt templates

---

## File Structure

```
packages/whitebox/src/shannon_whitebox/pipeline/
  shared.py          # D4: PipelineState.error → errors: list[str]
  workflows.py       # D4: append instead of assign + D5: add retry_policy

packages/blackbox/src/shannon_blackbox/pipeline/
  shared.py          # D4: BlackboxPipelineState.error → errors: list[str]
  workflows.py       # D4: append instead of assign

prompts/
  vuln-misconfig.txt # D7: Complete rewrite using vuln-injection.txt as skeleton
```

---

## Task 1: Add Error List Test for Whitebox PipelineState

**Files:**
- Create: `packages/whitebox/tests/test_pipeline_shared.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from shannon_whitebox.pipeline.shared import PipelineState


def test_pipeline_state_initializes_with_empty_errors_list():
    state = PipelineState()
    assert hasattr(state, "errors")
    assert isinstance(state.errors, list)
    assert len(state.errors) == 0


def test_pipeline_state_errors_can_be_appended():
    state = PipelineState()
    state.errors.append("error1")
    state.errors.append("error2")
    assert len(state.errors) == 2
    assert state.errors == ["error1", "error2"]


def test_pipeline_state_default_factory_creates_new_list_each_instance():
    state1 = PipelineState()
    state2 = PipelineState()
    state1.errors.append("error1")
    assert len(state2.errors) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/whitebox/tests/test_pipeline_shared.py -v`
Expected: FAIL with "PipelineState has no attribute 'errors'"

- [ ] **Step 3: Write minimal implementation**

No implementation yet — this test validates the next task's changes.

- [ ] **Step 4: Commit test file**

```bash
git add packages/whitebox/tests/test_pipeline_shared.py
git commit -m "test(whitebox): add tests for PipelineState.errors list"
```

---

## Task 2: Change Whitebox PipelineState.error to errors list

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/shared.py:26`

- [ ] **Step 1: Read current state**

```bash
head -30 packages/whitebox/src/shannon_whitebox/pipeline/shared.py
```

Current content shows:
```python
@dataclass
class PipelineState:
    status: str = "running"
    completed_agents: list[str] = field(default_factory=list)
    agent_metrics: dict[str, dict] = field(default_factory=dict)
    start_time: float = 0.0
    error: str | None = None
    code_index_stats: dict | None = None
```

- [ ] **Step 2: Replace error with errors list**

```python
@dataclass
class PipelineState:
    status: str = "running"
    completed_agents: list[str] = field(default_factory=list)
    agent_metrics: dict[str, dict] = field(default_factory=dict)
    start_time: float = 0.0
    errors: list[str] = field(default_factory=list)
    code_index_stats: dict | None = None
```

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest packages/whitebox/tests/test_pipeline_shared.py -v`
Expected: PASS (all 3 tests pass)

- [ ] **Step 4: Verify no other code depends on .error being a string**

```bash
grep -r "\.error" packages/whitebox/src --include="*.py" | grep -v "errors"
```

Expected output (only the workflows.py line we'll fix next):
```
packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:132:                        self._state.error = f"{agent_name.value}: {result}"
```

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/shared.py
git commit -m "feat(whitebox): change PipelineState.error to errors list"
```

---

## Task 3: Update Whitebox workflows.py to append to errors list

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:132`

- [ ] **Step 1: Read the context around line 132**

```bash
sed -n '126,136p' packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
```

Current content:
```python
                for i, result in enumerate(results):
                    vt = selected_classes[i]
                    agent_name = AgentName(f"{vt}-vuln")
                    if isinstance(result, Exception):
                        self._state.error = f"{agent_name.value}: {result}"
                    else:
                        self._state.completed_agents.append(agent_name.value)
                        self._state.agent_metrics[agent_name.value] = result
```

- [ ] **Step 2: Replace error assignment with append**

Replace line 132:
```python
                        self._state.error = f"{agent_name.value}: {result}"
```

With:
```python
                        self._state.errors.append(f"{agent_name.value}: {result}")
```

- [ ] **Step 3: Verify the change**

```bash
sed -n '126,136p' packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
```

Expected output shows `.errors.append()`:
```python
                for i, result in enumerate(results):
                    vt = selected_classes[i]
                    agent_name = AgentName(f"{vt}-vuln")
                    if isinstance(result, Exception):
                        self._state.errors.append(f"{agent_name.value}: {result}")
                    else:
                        self._state.completed_agents.append(agent_name.value)
                        self._state.agent_metrics[agent_name.value] = result
```

- [ ] **Step 4: Run existing tests**

Run: `pytest packages/whitebox/tests/ -v -k "not integration"`
Expected: PASS (no failures)

- [ ] **Step 5: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
git commit -m "feat(whitebox): append to errors list instead of overwriting"
```

---

## Task 4: Add Error List Test for Blackbox PipelineState

**Files:**
- Create: `packages/blackbox/tests/test_pipeline_shared.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from shannon_blackbox.pipeline.shared import BlackboxPipelineState


def test_blackbox_pipeline_state_initializes_with_empty_errors_list():
    state = BlackboxPipelineState()
    assert hasattr(state, "errors")
    assert isinstance(state.errors, list)
    assert len(state.errors) == 0


def test_blackbox_pipeline_state_errors_can_be_appended():
    state = BlackboxPipelineState()
    state.errors.append("error1")
    state.errors.append("error2")
    assert len(state.errors) == 2
    assert state.errors == ["error1", "error2"]


def test_blackbox_pipeline_state_default_factory_creates_new_list_each_instance():
    state1 = BlackboxPipelineState()
    state2 = BlackboxPipelineState()
    state1.errors.append("error1")
    assert len(state2.errors) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest packages/blackbox/tests/test_pipeline_shared.py -v`
Expected: FAIL with "BlackboxPipelineState has no attribute 'errors'"

- [ ] **Step 3: Commit test file**

```bash
git add packages/blackbox/tests/test_pipeline_shared.py
git commit -m "test(blackbox): add tests for BlackboxPipelineState.errors list"
```

---

## Task 5: Change Blackbox PipelineState.error to errors list

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/shared.py:27`

- [ ] **Step 1: Read current state**

```bash
head -30 packages/blackbox/src/shannon_blackbox/pipeline/shared.py
```

Current content shows:
```python
@dataclass
class BlackboxPipelineState:
    status: str = "running"
    current_phase: str | None = None
    completed_agents: list[str] = field(default_factory=list)
    agent_metrics: dict[str, dict] = field(default_factory=dict)
    has_whitebox_results: bool = False
    start_time: float = 0.0
    error: str | None = None
```

- [ ] **Step 2: Replace error with errors list**

```python
@dataclass
class BlackboxPipelineState:
    status: str = "running"
    current_phase: str | None = None
    completed_agents: list[str] = field(default_factory=list)
    agent_metrics: dict[str, dict] = field(default_factory=dict)
    has_whitebox_results: bool = False
    start_time: float = 0.0
    errors: list[str] = field(default_factory=list)
```

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest packages/blackbox/tests/test_pipeline_shared.py -v`
Expected: PASS (all 3 tests pass)

- [ ] **Step 4: Verify no other code depends on .error being a string**

```bash
grep -r "\.error" packages/blackbox/src --include="*.py" | grep -v "errors"
```

Expected output (only the workflows.py line we'll fix next):
```
packages/blackbox/src/shannon_blackbox/pipeline/workflows.py:117:                            self._state.error = f"{agent_name.value}: {result}"
```

- [ ] **Step 5: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/shared.py
git commit -m "feat(blackbox): change BlackboxPipelineState.error to errors list"
```

---

## Task 6: Update Blackbox workflows.py to append to errors list

**Files:**
- Modify: `packages/blackbox/src/shannon_blackbox/pipeline/workflows.py:117`

- [ ] **Step 1: Read the context around line 117**

```bash
sed -n '110,121p' packages/blackbox/src/shannon_blackbox/pipeline/workflows.py
```

Current content:
```python
                    for i, result in enumerate(results):
                        vt, agent_name, _ = exploit_tasks[i]
                        if isinstance(result, Exception):
                            self._state.error = f"{agent_name.value}: {result}"
                        else:
                            self._state.completed_agents.append(agent_name.value)
                            self._state.agent_metrics[agent_name.value] = result
```

- [ ] **Step 2: Replace error assignment with append**

Replace line 117:
```python
                            self._state.error = f"{agent_name.value}: {result}"
```

With:
```python
                            self._state.errors.append(f"{agent_name.value}: {result}")
```

- [ ] **Step 3: Verify the change**

```bash
sed -n '110,121p' packages/blackbox/src/shannon_blackbox/pipeline/workflows.py
```

Expected output shows `.errors.append()`:
```python
                    for i, result in enumerate(results):
                        vt, agent_name, _ = exploit_tasks[i]
                        if isinstance(result, Exception):
                            self._state.errors.append(f"{agent_name.value}: {result}")
                        else:
                            self._state.completed_agents.append(agent_name.value)
                            self._state.agent_metrics[agent_name.value] = result
```

- [ ] **Step 4: Run existing tests**

Run: `pytest packages/blackbox/tests/ -v -k "not integration"`
Expected: PASS (no failures)

- [ ] **Step 5: Commit**

```bash
git add packages/blackbox/src/shannon_blackbox/pipeline/workflows.py
git commit -m "feat(blackbox): append to errors list instead of overwriting"
```

---

## Task 7: Add retry_policy to Whitebox vuln agents

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/pipeline/workflows.py:119-124`

- [ ] **Step 1: Read the context around line 119**

```bash
sed -n '114,125p' packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
```

Current content (note missing retry_policy):
```python
            vuln_tasks = []
            for vt in selected_classes:
                agent_name = AgentName(f"{vt}-vuln")
                if agent_name.value not in self._state.completed_agents:
                    vuln_input = ActivityInput(**{**act_input.__dict__, "workspace_name": agent_name.value})
                    vuln_tasks.append(
                        workflow.execute_activity(
                            activities.run_vuln_agent, vuln_input,
                            start_to_close_timeout=timedelta(hours=2),
                        )
                    )
```

- [ ] **Step 2: Check how blackbox defines retry_policy**

```bash
sed -n '41,46p' packages/blackbox/src/shannon_blackbox/pipeline/workflows.py
```

Expected output:
```python
        retry_policy = RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=30),
            maximum_interval=timedelta(minutes=5),
            backoff_coefficient=2.0,
        )
```

- [ ] **Step 3: Add retry_policy parameter to vuln agent activity**

Replace the vuln_tasks.append block:
```python
                    vuln_tasks.append(
                        workflow.execute_activity(
                            activities.run_vuln_agent, vuln_input,
                            start_to_close_timeout=timedelta(hours=2),
                        )
                    )
```

With:
```python
                    vuln_tasks.append(
                        workflow.execute_activity(
                            activities.run_vuln_agent, vuln_input,
                            start_to_close_timeout=timedelta(hours=2),
                            retry_policy=RetryPolicy(
                                maximum_attempts=3,
                                initial_interval=timedelta(seconds=30),
                                maximum_interval=timedelta(minutes=5),
                                backoff_coefficient=2.0,
                            ),
                        )
                    )
```

- [ ] **Step 4: Verify the change**

```bash
sed -n '114,130p' packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
```

Expected output shows retry_policy:
```python
            vuln_tasks = []
            for vt in selected_classes:
                agent_name = AgentName(f"{vt}-vuln")
                if agent_name.value not in self._state.completed_agents:
                    vuln_input = ActivityInput(**{**act_input.__dict__, "workspace_name": agent_name.value})
                    vuln_tasks.append(
                        workflow.execute_activity(
                            activities.run_vuln_agent, vuln_input,
                            start_to_close_timeout=timedelta(hours=2),
                            retry_policy=RetryPolicy(
                                maximum_attempts=3,
                                initial_interval=timedelta(seconds=30),
                                maximum_interval=timedelta(minutes=5),
                                backoff_coefficient=2.0,
                            ),
                        )
                    )
```

- [ ] **Step 5: Verify imports are already present**

```bash
head -10 packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
```

Expected output shows RetryPolicy is already imported:
```python
from temporalio.common import RetryPolicy
```

- [ ] **Step 6: Run existing tests**

Run: `pytest packages/whitebox/tests/ -v -k "not integration"`
Expected: PASS (no failures)

- [ ] **Step 7: Commit**

```bash
git add packages/whitebox/src/shannon_whitebox/pipeline/workflows.py
git commit -m "feat(whitebox): add retry_policy to vuln agents"
```

---

## Task 8: Backup current misconfig prompt

**Files:**
- Modify: `prompts/vuln-misconfig.txt` (backup first)

- [ ] **Step 1: Create backup**

```bash
cp prompts/vuln-misconfig.txt prompts/vuln-misconfig.txt.backup
```

- [ ] **Step 2: Verify backup exists**

```bash
diff prompts/vuln-misconfig.txt prompts/vuln-misconfig.txt.backup
```

Expected: No output (files are identical)

- [ ] **Step 3: Commit backup**

```bash
git add prompts/vuln-misconfig.txt.backup
git commit -m "chore: backup original vuln-misconfig.txt before rewrite"
```

---

## Task 9: Read injection prompt for structure reference

**Files:**
- Reference: `prompts/vuln-injection.txt` (read only)

- [ ] **Step 1: Read injection prompt structure**

```bash
head -100 prompts/vuln-injection.txt
```

This provides the structural template we'll adapt for misconfig.

- [ ] **Step 2: Note key sections to include**

Sections we must add to vuln-misconfig.txt:
1. `<scope>` with @include(shared/_vuln-scope.txt)
2. `<rules>` with @include(shared/_rules.txt)
3. @include(shared/_shared-session.txt) after _code-path-rules.txt
4. `<login_instructions>` with {{LOGIN_INSTRUCTIONS}}
5. Enhanced `<critical>` section
6. `<system_architecture>` section
7. `<definitions>` with exploitable_vulnerability_definition
8. Enhanced `<cli_tools>` section
9. `<data_format_specifications>` with exploitation_queue_format
10. Enhanced `<methodology_and_domain_expertise>`
11. `<false_positives_to_avoid>`
12. `<conclusion_trigger>` with TodoWrite requirements

---

## Task 10: Write new vuln-misconfig.txt prompt (part 1: header through cli_tools)

**Files:**
- Modify: `prompts/vuln-misconfig.txt`

- [ ] **Step 1: Write the first section (role through cli_tools)**

```bash
cat > prompts/vuln-misconfig.txt << 'EOF'
<role>
You are a Misconfiguration Analysis Specialist, an expert in **white-box code analysis and HTTP response verification** for security headers, CORS policies, cookie security, open redirect vulnerabilities, information disclosure, and clickjacking.
Your primary function is to identify missing or misconfigured security controls by analyzing both application source code and live HTTP responses.
</role>

<objective>
Your mission is to identify where security controls are missing, misconfigured, or completely absent. Success criterion: Document each misconfiguration with concrete source location, HTTP response evidence, and a clear exploitation hypothesis.
</objective>

<scope>
@include(shared/_vuln-scope.txt)
</scope>

<target>
@include(shared/_target.txt)
</target>

<rules>
@include(shared/_rules.txt)
</rules>

@include(shared/_code-path-rules.txt)
@include(shared/_shared-session.txt)

<login_instructions>
{{LOGIN_INSTRUCTIONS}}
</login_instructions>

@include(shared/_rules-of-engagement.txt)

<critical>
**Your Professional Standard**
-   **Severity Context:** Misconfiguration vulnerabilities are among the most common and easily exploitable security flaws in web applications. A missing security header or overly permissive CORS policy can enable data theft, account takeover, or complete user session compromise.
-   **Your Role is Precise:** Your sole responsibility is to identify and precisely document security control gaps. You prove the *potential* for exploitation through concrete evidence; the Exploitation phase confirms the *realized* exploitation. Do not cross this boundary.
-   **Code is Ground Truth:** Your analysis must be rooted in the application's code. An observed HTTP behavior is only a symptom; the missing or misconfigured control in the code is the root cause you must identify.
-   **Thoroughness is Non-Negotiable:** An incomplete analysis is a failed analysis. Finding one flaw is merely the first data point. Your mission is only complete when **every potential misconfiguration surface** from the reconnaissance deliverable has been systematically analyzed and documented using the TodoWrite tool. **Do not terminate early.**
</critical>

<starting_context>
- Your **single source of truth** for the application's structure is the reconnaissance report located at `.shannon/deliverables/recon_deliverable.md`. You must derive your list of testable targets from this file.

</starting_context>

<system_architecture>
**PENTESTING WORKFLOW - YOUR POSITION:**

**Phase Sequence:** RECON (Complete) → **MISCONFIG ANALYSIS (You)** → EXPLOITATION (next phase)

**Your Input:** `.shannon/deliverables/recon_deliverable.md` (reconnaissance findings)
**Your Output:** `.shannon/deliverables/misconfig_analysis_deliverable.md` (analysis report for exploitation phase)

**WHAT HAPPENED BEFORE YOU:**
- Reconnaissance agent mapped application architecture, endpoints, and technology stack
- Security control baseline identified (frameworks, middleware, defensive measures)
- Target prioritization completed

**WHAT HAPPENS AFTER YOU:**
- Misconfig Exploitation specialist weaponizes your confirmed misconfigurations for attack demonstration

**YOUR CRITICAL ROLE:**
You are the **Security Control Auditor** determining whether the application properly implements:
- **(Security Headers)** CSP, X-Frame-Options, X-Content-Type-Options, HSTS, etc.
- **(CORS Policies)** Proper origin validation without credential exposure
- **(Cookie Security)** HttpOnly, Secure, SameSite flags applied correctly
- **(Open Redirect)** URL validation before redirect/response
- **(Information Disclosure)** Production config suppresses verbose errors
- **(Clickjacking)** Frame protection via headers or CSP

**COORDINATION REQUIREMENTS:**
- Cross-reference source code with live HTTP responses for verification
- Distinguish between missing controls vs. weak controls vs. properly configured
- Your confidence ratings directly influence exploitation resource allocation
</system_architecture>

<definitions>
<exploitable_misconfiguration_definition>
An **exploitable misconfiguration** is a confirmed security control that is either (1) completely missing, (2) present but misconfigured to be ineffective, or (3) overly permissive, resulting in a concrete opportunity for external exploitation. A missing control is NOT a vulnerability if it's not externally reachable or has no security impact. Your goal is to pass only externally-exploitable misconfigurations to the next phase.
</exploitable_misconfiguration_definition>
</definitions>

<cli_tools>

**CRITICAL TOOL USAGE RESTRICTIONS:**
- NEVER use the Read tool for application source code analysis—delegate every code review to the Task Agent.
- ALWAYS direct the Task Agent to locate security middleware, header configurations, and cookie settings before you reach a verdict.
- Use the Task Agent instead of Bash or Playwright when you need to inspect handlers, middleware, or shared utilities to trace security control implementation.

**Available Tools:**
- **Task Agent (Code Analysis):** Your primary tool. Use it to ask targeted questions about security middleware, header configuration, CORS policies, and cookie settings. MANDATORY for all source code analysis.
- **Playwright (Browser Automation):** Use the `playwright-cli` skill to send HTTP requests and capture full response headers. Always pass `-s={{PLAYWRIGHT_SESSION}}` for session isolation.
  - Basic request: `playwright-cli -s={{PLAYWRIGHT_SESSION}} request GET "https://example.com/api/users" --show-headers`
  - Post request: `playwright-cli -s={{PLAYWRIGHT_SESSION}} request POST "https://example.com/login" --json '{"username":"test","password":"test"}' --show-headers`
- **save-deliverable (CLI Tool):** Saves your deliverable files with automatic validation.
  - **Usage:** `save-deliverable --type <TYPE> --file-path <path>` or `--content '<text>'`
  - **Returns:** JSON to stdout: `{"status":"success","filepath":"..."}` or `{"status":"error","message":"...","retryable":true}`
  - **For large reports:** Write to disk first, then use `--file-path`. Do NOT pass large reports via `--content`.
- **Bash tool:** Use for creating directories, copying files, and other shell commands as needed.
- **TodoWrite Tool:** Use this to create and manage your analysis task list. Create a todo item for each endpoint that needs misconfig analysis. Mark items as "in_progress" when working on them and "completed" when done.
</cli_tools>
EOF
```

- [ ] **Step 2: Verify first section was written**

```bash
head -100 prompts/vuln-misconfig.txt
```

Expected: Shows role through cli_tools sections.

- [ ] **Step 3: No commit yet (file incomplete)**

---

## Task 11: Write new vuln-misconfig.txt prompt (part 2: data_format through methodology)

**Files:**
- Modify: `prompts/vuln-misconfig.txt`

- [ ] **Step 1: Append data_format_specifications section**

```bash
cat >> prompts/vuln-misconfig.txt << 'EOF'

<data_format_specifications>

  <exploitation_queue_format>
  **Purpose:** Defines the format of the exploitation queue JSON.

  **Structure:** The `vulnerability` JSON object MUST follow this exact format:
		{
			"ID": "unique ID for each vulnerability (e.g., MISCONFIG-VULN-XX)",
			"vulnerability_type": "SecurityHeader | CORS | CookieSecurity | OpenRedirect | InfoDisclosure | Clickjacking",
			"externally_exploitable": true | false,
			"source_endpoint": "the endpoint where misconfiguration was observed (e.g., /api/*)",
			"vulnerable_code_location": "file:line where security control should be set (or is missing)",
			"missing_defense": "description of missing or misconfigured control",
			"redirect_sink": "specific redirect function if open redirect (null otherwise)",
			"exploitation_hypothesis": "how this misconfiguration could be exploited",
			"suggested_exploit_technique": "approach for exploitation phase",
			"vulnerable_parameter": null for misconfig (no injection parameter)",
			"existing_validation": "any validation observed (null if none)",
			"notes": "additional context, assumptions, or conditions"
		}
  </exploitation_queue_format>

</data_format_specifications>
EOF
```

- [ ] **Step 2: Append methodology_and_domain_expertise section**

```bash
cat >> prompts/vuln-misconfig.txt << 'EOF'

<methodology_and_domain_expertise>

  <methodology>
  **Misconfiguration Vulnerability Analysis (pre-exploitation)**

  - **Goal:** Prove whether security controls are missing or misconfigured through code analysis and HTTP response verification.
  
  **1) Create a To Do for each Endpoint found in the Recon Deliverable**
     - Inside `.shannon/deliverables/recon_deliverable.md`, identify all endpoints under the application map.
     - Use the TodoWrite tool to create a task for each endpoint that needs misconfig analysis.
     - Group endpoints by logical area (auth, API, static assets, etc.) for systematic coverage.
  
  **2) For each endpoint, analyze by misconfig category:**
     
     **A. Security Headers Analysis**
        - Send a request to the endpoint using Playwright with `--show-headers`
        - Check for presence of: Content-Security-Policy, X-Frame-Options, X-Content-Type-Options, Strict-Transport-Security, Referrer-Policy, Permissions-Policy
        - For each header, assess configuration strength:
          - CSP: restrictive or overly permissive? Check for `unsafe-*`, `*` sources
          - X-Frame-Options: DENY, SAMEORIGIN, or ALLOW-FROM with specific origin?
          - HSTS: includeSubDomains? max-age duration?
        - Use Task Agent to locate security middleware in code (helmet.js, django.middleware.security, etc.)
        - Document missing or weak headers with code location
  
     **B. CORS Policy Analysis**
        - Send requests with Origin header using Playwright:
          - `playwright-cli -s={{PLAYWRIGHT_SESSION}} request GET "https://example.com/api/data" --headers '{"Origin":"https://evil.com"}' --show-headers`
        - Check if Access-Control-Allow-Origin reflects the arbitrary origin
        - Check if Access-Control-Allow-Credentials is true with `*` origin (critical vulnerability)
        - Use Task Agent to find CORS configuration in code
        - Document overly permissive origins with code location
  
     **C. Cookie Security Analysis**
        - Examine Set-Cookie headers in responses using Playwright
        - Check for: HttpOnly flag, Secure flag, SameSite attribute (Strict/Lax/None)
        - Verify Domain and Path are not overly broad
        - Use Task Agent to find cookie-setting code
        - Document cookies missing security flags with code location
  
     **D. Open Redirect Analysis**
        - Identify redirect parameters (next, return, redirect, url, etc.)
        - Trace parameter values to redirect sinks using Task Agent
        - Check for URL validation, allowlist, or path-only redirects
        - Document unvalidated redirects with code location
  
     **E. Information Disclosure Analysis**
        - Check error responses for stack traces, debug info, server versions
        - Use Playwright to trigger error conditions (malformed input, 404s, 500s)
        - Check for debug/admin endpoints exposed without authentication
        - Use Task Agent to find error handling configuration
        - Document verbose errors with code location
  
     **F. Clickjacking Analysis**
        - Verify X-Frame-Options or CSP frame-ancestors protection
        - Test if endpoint can be loaded in iframe using Playwright
        - Document missing frame protection with code location
  
  **3) Make the call (vulnerable or safe)**
     - **Vulnerable** if the security control is missing, misconfigured, or allows external exploitation
     - Include a short rationale (e.g., "CORS allows arbitrary origin with credentials enabled")
     - **Safe** if the control is properly implemented or not externally reachable
  
  **4) Append to findings list (consistent fields)**
     - **If the verdict is `vulnerable`:** Include the finding in your exploitation queue. Set `externally_exploitable` to `true` ONLY if exploitable via public internet.
     - **If the verdict is `safe`:** DO NOT add the finding to the exploitation queue. These secure configurations must be documented later in the "Configurations Analyzed and Confirmed Secure" section of your final Markdown report.
     - **fields:**
        - `ID` (e.g., MISCONFIG-VULN-001)
        - `vulnerability_type` (SecurityHeader, CORS, CookieSecurity, etc.)
        - `externally_exploitable` (true/false)
        - `source_endpoint` (the endpoint tested)
        - `vulnerable_code_location` (file:line where control should be set)
        - `missing_defense` (description of missing/weak control)
        - `redirect_sink` (for open redirect, null otherwise)
        - `exploitation_hypothesis` (how it could be exploited)
        - `suggested_exploit_technique` (approach for exploit phase)
        - `vulnerable_parameter` (null for misconfig)
        - `existing_validation` (any validation observed)
        - `notes` (assumptions, conditions)
  
  **5) Score confidence**
     - **High:** Direct observation via HTTP response + code confirmation; unambiguous missing control
     - **Medium:** Code shows missing control but HTTP response unavailable; or HTTP response shows issue but code location unclear
     - **Low:** Suspected misconfiguration without direct evidence

<systematic_inquiry_process>
**How to execute the analysis per endpoint**

*   For each endpoint, send a Playwright request with `--show-headers` to capture full response headers.
*   For each misconfig category (headers, CORS, cookies, redirect, info-disclosure, clickjacking), systematically check for the control.
*   When a control is missing or misconfigured, use the Task Agent to locate the code responsible for setting that control.
*   Document the specific file:line where the control should be implemented (or where the misconfigured code lives).
*   Make the call (vulnerable/safe) based on external exploitability and concrete evidence.
</systematic_inquiry_process>

<false_positives_to_avoid>
**General:**
*   Reporting missing HSTS on development-only endpoints (localhost, internal tools)
*   Flagging overly permissive CORS that's only for internal API communication
*   Treating debug-mode errors in production as exploitable when they're only in admin panels
*   Reporting missing security headers on non-sensitive static assets (images, CSS)

**Security Header-Specific:**
*   Assuming all headers must be present on every response (some endpoints don't need CSP, etc.)
*   Treating weak CSP as exploitable when there's no user input to inject

**CORS-Specific:**
*   Flagging `*` origin as vulnerable when credentials are NOT enabled
*   Reporting same-origin CORS as misconfigured (it's properly scoped)

**Cookie-Specific:**
*   Treating missing SameSite on session cookies as exploitable when the app has no CSRF risk
*   Flagging cookies without Secure flag on HTTP-only endpoints

**Open Redirect-Specific:**
*   Treating path-only redirects as open redirects (e.g., /return/path is safe)
*   Flagging redirects to same-site URLs as open redirects

**Information Disclosure-Specific:**
*   Treating stack traces in 500 errors as exploitable when they're only in non-production
*   Flagging generic error messages as information disclosure
</false_positives_to_avoid>

<analytical_pitfalls_to_avoid>

* Relying only on HTTP responses without code confirmation (or vice versa)
*   Assuming security headers at framework level apply to all endpoints (middleware may be bypassed)
*   Treating dev/test environment configurations as representative of production
*   Failing to document secure configurations (negative results matter)
</analytical_pitfalls_to_avoid>

<evidence_quality_standards>

* **HIGH:** HTTP response shows missing/weak control + code location identified + clear exploitation path; reproducible via request.
* **MEDIUM:** Evidence suggests misconfiguration but some ambiguity (e.g., can't confirm code location).
* **LOW:** Suspicious pattern without direct evidence (e.g., framework typically sets header but it's missing).
*   Always include exact endpoint, headers observed, and code locations where possible.
</evidence_quality_standards>

<confidence_level_definitions>

* **HIGH:** Direct HTTP evidence + code confirmation; unambiguous missing or misconfigured control.
* **MEDIUM:** Strong evidence but some assumptions (e.g., assume production behavior matches observed).
* **LOW:** Weak evidence or high uncertainty about exploitability.
</confidence_level_definitions>

<negative_results_documentation>

*   Explicitly record secure configurations (e.g., "CSP properly set with restrictive policy; no unsafe-* sources").
*   This prevents re-testing and demonstrates coverage.
</negative_results_documentation>

<coverage_requirements>

*   Test **all** endpoints identified in recon: web pages, API endpoints, static assets, auth flows.
*   Include authenticated and unauthenticated endpoints.
*   Test both normal and error responses for information disclosure.
</coverage_requirements>

<analysis_coverage_requirements>

*   Ensure no endpoint from the recon deliverable is left unanalyzed for misconfigurations.
*   Prioritize externally-exposed endpoints (login, API, data access) over internal-only endpoints.
</analysis_coverage_requirements>

</methodology_and_domain_expertise>
EOF
```

- [ ] **Step 2: Verify sections were appended**

```bash
tail -100 prompts/vuln-misconfig.txt | head -50
```

Expected: Shows data_format_specifications and beginning of methodology.

---

## Task 12: Write new vuln-misconfig.txt prompt (part 3: deliverable and conclusion)

**Files:**
- Modify: `prompts/vuln-misconfig.txt`

- [ ] **Step 1: Append deliverable_instructions section**

```bash
cat >> prompts/vuln-misconfig.txt << 'EOF'

<deliverable_instructions>
When you have systematically analyzed all endpoints, you MUST generate two final files. Follow these instructions precisely to structure your output.

**1. Your Specialist Deliverable**

First, synthesize all of your findings into a single, detailed Markdown report located at `.shannon/deliverables/misconfig_analysis_deliverable.md`. This report is the official record of your work.

Your report MUST use the following structure precisely:

---
# Misconfiguration Analysis Report

## 1. Executive Summary

- **Analysis Status:** Complete
- **Key Outcome:** [Summary of findings - e.g., "3 high-confidence misconfigurations identified"]
- **Purpose of this Document:** This report provides the strategic context, dominant patterns, and environmental intelligence necessary to effectively exploit the misconfigurations listed in the queue.

## 2. Dominant Misconfiguration Patterns

**[Pattern Name]**
- **Description:** [Description of the pattern - e.g., "Missing Content-Security-Policy across all API endpoints"]
- **Implication:** [Security impact - e.g., "XSS payloads can execute without restriction"]
- **Representative:** [ID of a vulnerability that exhibits this pattern]

## 3. Strategic Intelligence for Exploitation

- **Security Posture:** [Overall assessment of security controls]
- **Exploitation Approach:** [Recommended technique for exploitation phase]
- **Defensive Considerations:** [Any WAF, CDN, or other protections that may affect exploitation]

## 4. Configurations Analyzed and Confirmed Secure

These configurations were analyzed and confirmed to be properly implemented. They are **low-priority** for further testing.

| **Control Type** | **Endpoint/Location** | **Implementation** | **Verdict** |
|------------------|------------------------|-------------------|-------------|
| CSP Header | /api/* | Content-Security-Policy: default-src 'self' | SECURE |
| CORS Policy | /api/auth | Access-Control-Allow-Origin: https://example.com | SECURE |

## 5. Analysis Constraints and Blind Spots

- **[Constraint Description]:** [Impact on analysis - e.g., "Could not test admin endpoints without auth"]

---

</deliverable_instructions>

EOF
```

- [ ] **Step 2: Append conclusion_trigger section**

```bash
cat >> prompts/vuln-misconfig.txt << 'EOF'

<conclusion_trigger>
**COMPLETION REQUIREMENTS (ALL must be satisfied):**

1. **Todo Completion:** ALL tasks in your TodoWrite list must be marked as "completed"
2. **Deliverable Generation:** Your deliverable must be successfully saved using the save-deliverable CLI tool:
   - **CHUNKED WRITING (MANDATORY):**
     1. Use the **Write** tool to create `.shannon/deliverables/misconfig_analysis_deliverable.md` with the title and first major section
     2. Use the **Edit** tool to append each remaining section — match the last few lines of the file, then replace with those lines plus the new section content
     3. Repeat step 2 for all remaining sections
     4. Run `save-deliverable` with `--type MISCONFIG_ANALYSIS --file-path ".shannon/deliverables/misconfig_analysis_deliverable.md"`
     **WARNING:** Do NOT write the entire report in a single tool call — exceeds 32K output token limit. Split into multiple Write/Edit operations.

**Note:** Save your deliverable markdown via save-deliverable first. The exploitation queue is captured automatically at the end of your session.

**ONLY AFTER** both todo completion AND successful deliverable generation, announce "**MISCONFIG ANALYSIS COMPLETE**" and stop.

**CRITICAL:** After announcing completion, STOP IMMEDIATELY. Do NOT output summaries, recaps, or explanations of your work — the deliverable contains everything needed.

**FAILURE TO COMPLETE TODOS = INCOMPLETE ANALYSIS** - You will be considered to have failed the mission if you generate deliverables before completing comprehensive testing of all endpoints.
</conclusion_trigger>
EOF
```

- [ ] **Step 3: Verify complete file**

```bash
wc -l prompts/vuln-misconfig.txt
```

Expected: ~280-350 lines (compared to original 89 lines)

- [ ] **Step 4: Verify key sections are present**

```bash
grep -E "^(<role>|<objective>|<scope>|<system_architecture>|<cli_tools>|<data_format|<methodology|<deliverable|<conclusion>)" prompts/vuln-misconfig.txt
```

Expected output shows all major sections present.

- [ ] **Step 5: Verify @include directives are present**

```bash
grep "@include" prompts/vuln-misconfig.txt
```

Expected output shows:
```
@include(shared/_vuln-scope.txt)
@include(shared/_target.txt)
@include(shared/_rules.txt)
@include(shared/_code-path-rules.txt)
@include(shared/_shared-session.txt)
@include(shared/_rules-of-engagement.txt)
```

- [ ] **Step 6: Commit complete new prompt**

```bash
git add prompts/vuln-misconfig.txt
git commit -m "feat(prompts): rewrite vuln-misconfig.txt to match vuln-injection.txt structure"
```

---

## Task 13: Run comprehensive tests

**Files:**
- Test: All modified packages

- [ ] **Step 1: Run whitebox tests**

```bash
pytest packages/whitebox/tests/ -v -k "not integration"
```

Expected: All tests pass

- [ ] **Step 2: Run blackbox tests**

```bash
pytest packages/blackbox/tests/ -v -k "not integration"
```

Expected: All tests pass

- [ ] **Step 3: Run core tests (if any might be affected)**

```bash
pytest packages/core/tests/ -v -k "pipeline" 2>/dev/null || echo "No matching tests"
```

- [ ] **Step 4: Verify prompt syntax**

```bash
head -1 prompts/vuln-misconfig.txt
```

Expected: `<role>` tag present

- [ ] **Step 5: Compare line counts**

```bash
wc -l prompts/vuln-*.txt
```

Expected output shows vuln-misconfig.txt is now similar in length to other vuln prompts (280-350 lines).

- [ ] **Step 6: No commit (test verification only)**

---

## Task 14: Verify all changes are committed

**Files:**
- Git status check

- [ ] **Step 1: Check git status**

```bash
git status
```

Expected: No uncommitted changes except the backup file (if not committed)

- [ ] **Step 2: Verify all commits are present**

```bash
git log --oneline -15
```

Expected output shows these commits:
```
feat(whitebox): change PipelineState.error to errors list
feat(whitebox): append to errors list instead of overwriting
feat(whitebox): add retry_policy to vuln agents
feat(blackbox): change BlackboxPipelineState.error to errors list
feat(blackbox): append to errors list instead of overwriting
feat(prompts): rewrite vuln-misconfig.txt to match vuln-injection.txt structure
test(whitebox): add tests for PipelineState.errors list
test(blackbox): add tests for BlackboxPipelineState.errors list
chore: backup original vuln-misconfig.txt before rewrite
```

- [ ] **Step 3: Summary of changes**

D4: Error state overwrite
- Whitebox: shared.py (error → errors list), workflows.py (append instead of assign)
- Blackbox: shared.py (error → errors list), workflows.py (append instead of assign)
- Tests: Added for both whitebox and blackbox

D5: Vuln agent missing retry
- Whitebox: workflows.py (added retry_policy with 3 max attempts)
- Blackbox: Already had retry_policy (no change needed)

D7: Misconfig prompt rewrite
- Prompts: vuln-misconfig.txt rewritten from 89 to ~280-350 lines
- Structure now matches vuln-injection.txt with all required sections

- [ ] **Step 4: Ready for integration**

All tasks complete. Plan successfully implemented.

---
