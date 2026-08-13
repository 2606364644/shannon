# Combined Whitebox-Blackbox Scan Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the Web combined whitebox→blackbox scan preserve phase semantics, handle unauthenticated targets, propagate workflow failures, finalize terminal state correctly, and expose usable progress data.

**Architecture:** Keep the existing independent Temporal whitebox and blackbox workflows. Fix the Web `ScanManager` handoff boundary: explicitly mark the whitebox input as combined, pass an optional auth config path, validate returned workflow state before advancing, and centralize terminal cleanup. Do not redesign precheck into a new asynchronous workflow in this patch.

**Tech Stack:** Python 3.12, FastAPI, Temporal Python SDK, pytest/pytest-asyncio, existing `ScanManager` and session/event abstractions.

---

### Task 1: Add failing regression tests for combined handoff inputs

**Files:**
- Modify: `packages/web/tests/test_combined_orchestrator.py`
- Modify: `packages/web/tests/test_combined_precheck.py`
- Modify: `packages/web/tests/test_combined_resume_cancel.py`

- [ ] **Step 1: Test whitebox submission carries `combined=True`**

Capture the `PipelineInput` passed by `_submit_whitebox` and assert `combined is True` when the handoff is a combined scan. Cover the resume path as well.

- [ ] **Step 2: Test unauthenticated combined handoff passes `config_path=None`**

Seed a valid whitebox deliverable set without `scan-config.yaml`, run `_run_blackbox_phase`, capture `_submit_blackbox` arguments, and assert `config_path is None`.

- [ ] **Step 3: Run the new tests and verify they fail**

Run:

```bash
./.venv/bin/pytest -q \
  packages/web/tests/test_combined_orchestrator.py \
  packages/web/tests/test_combined_precheck.py \
  packages/web/tests/test_combined_resume_cancel.py
```

Expected: failures showing `PipelineInput.combined == False` and a non-None nonexistent config path.

---

### Task 2: Fix whitebox combined propagation and optional auth config

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py:271-425,1615-1661`

- [ ] **Step 1: Extend `_submit_whitebox` with an explicit `combined` argument**

Use a default of `False` for non-combined scans, construct `PipelineInput(..., combined=combined)`, and pass `combined=True` from both the initial combined start path and the combined resume path.

- [ ] **Step 2: Make `_run_blackbox_phase` pass only an existing config file**

Replace the unconditional `str(scan_dir / "scan-config.yaml")` with an existence check and pass `None` when the scan has no auth snapshot.

- [ ] **Step 3: Run Task 1 tests and verify they pass**

Run the same targeted pytest command. Expected: PASS for the new handoff tests and no regressions in the existing combined tests.

---

### Task 3: Add failing tests for workflow status propagation and terminal cleanup

**Files:**
- Modify: `packages/web/tests/test_combined_orchestrator.py`
- Modify: `packages/web/tests/test_combined_reconcile.py`
- Modify: `packages/web/tests/test_combined_precheck.py`

- [ ] **Step 1: Test a returned whitebox failure stops blackbox submission**

Make the fake whitebox handle return `{"status": "failed", "error": "wb failed"}` without raising. Assert `_run_blackbox_phase` is not awaited and the session is marked `bb_phase=failed`.

- [ ] **Step 2: Test a returned blackbox failure does not generate the combined report**

Make the fake blackbox handle return a state/dict with `status="failed"`. Assert `_generate_combined_report` is not awaited and the combined phase is failed.

- [ ] **Step 3: Test precheck failure writes failed session and event state**

Run the real `start()` fail-fast branch with `_run_precheck=False` and assert `session.status == "failed"`, `bb_phase == "failed"`, and the last `scan_end.status == "failed"`.

- [ ] **Step 4: Run the new tests and verify they fail**

Run:

```bash
./.venv/bin/pytest -q \
  packages/web/tests/test_combined_orchestrator.py \
  packages/web/tests/test_combined_reconcile.py \
  packages/web/tests/test_combined_precheck.py
```

Expected: returned workflow failures currently continue, and fail-fast cleanup currently reports running/completed.

---

### Task 4: Implement status-aware combined orchestration and cleanup

**Files:**
- Modify: `packages/web/src/supernova_web/components/scan_manager.py:1546-1661,1719-1790`

- [ ] **Step 1: Add a small status extractor/guard**

Normalize dict and dataclass workflow results. Treat only `status == "completed"` as successful; treat `failed`, `cancelled`, and unknown terminal states as failures with a useful error message.

- [ ] **Step 2: Apply the guard to whitebox and blackbox handoff**

In `_combined_orchestrator`, inspect the whitebox result before calling `_run_blackbox_phase`. In `_run_blackbox_phase` and `_combined_report_orchestrator`, inspect the blackbox result before rendering or marking `bb_phase=completed`.

- [ ] **Step 3: Make `_ensure_scan_end` synchronize session terminal state**

When it creates a terminal event, pass `session_status=status` and `scan_dir`. Ensure exception/fail-fast paths call it with `status="failed"`; skipped paths call it with the agreed terminal status and retain `bb_phase="skipped"`. Preserve the existing idempotent no-second-`scan_end` behavior.

- [ ] **Step 4: Run Task 3 tests and verify they pass**

Run the targeted combined test command and confirm all status/cleanup assertions pass.

---

### Task 5: Add and wire expected progress denominator

**Files:**
- Modify: `packages/web/tests/test_combined_precheck.py` or `packages/web/tests/test_combined_session_fields.py`
- Modify: `packages/web/src/supernova_web/components/scan_manager.py:219-247`

- [ ] **Step 1: Add a failing assertion that a combined start writes `expected_agents.whitebox`**

Keep the existing mocked start path and assert `session.json["expected_agents"]["whitebox"]` equals `_compute_expected_agents(req)["whitebox"]`.

- [ ] **Step 2: Write `expected_agents` before precheck/whitebox submission**

For combined scans, merge the whitebox expected count into session data before the precheck begins. Preserve any existing fields and let `_run_blackbox_phase` add the blackbox denominator later.

- [ ] **Step 3: Run progress/session tests**

```bash
./.venv/bin/pytest -q \
  packages/web/tests/test_combined_precheck.py \
  packages/web/tests/test_combined_session_fields.py
```

Expected: PASS.

---

### Task 6: Full verification and diff review

**Files:**
- No new source files.

- [ ] **Step 1: Run combined backend tests**

```bash
./.venv/bin/pytest -q \
  packages/web/tests/test_combined_orchestrator.py \
  packages/web/tests/test_combined_precheck.py \
  packages/web/tests/test_combined_rerun.py \
  packages/web/tests/test_combined_resume_cancel.py \
  packages/web/tests/test_combined_reconcile.py \
  packages/web/tests/test_combined_session_fields.py \
  packages/whitebox/tests/test_finalize_combined_phase_event.py
```

- [ ] **Step 2: Run combined frontend tests**

```bash
./node_modules/.bin/vitest run \
  src/components/__tests__/ScanNewPageCombined.test.tsx \
  src/routes/WorkspaceDetail/__tests__/ScanListCombined.test.tsx \
  src/routes/WorkspaceDetail/__tests__/ReportTabCombined.test.tsx
```

Run from `packages/web/frontend`.

- [ ] **Step 3: Review diff and preserve unrelated work**

Use `git diff --check` and inspect only the combined-scan files/tests changed by this fix. Do not revert or reformat unrelated uncommitted modifications.

- [ ] **Step 4: Report remaining unrelated test failures honestly**

If the full repository suite remains red because of pre-existing/unrelated uncommitted changes, list those separately from the combined-scan result.
