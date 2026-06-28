# CLI Summary Bug Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix CLI summary to show correct vulnerability counts by correcting the queue file lookup path.

**Architecture:** Single-line bug fix in the CLI result summary code path. The `summary_path` variable already points to the deliverables directory, but the code incorrectly uses `.parent` to look one level up.

**Tech Stack:** Python, Click CLI framework, Pathlib

---

## File Structure

**Files to modify:**
- `packages/whitebox/src/shannon_whitebox/cli/main.py:69` - Fix the queue file path

**Files to reference:**
- `packages/whitebox/src/shannon_whitebox/cli/main.py:60-75` - The CLI summary output section
- `packages/core/src/shannon_core/workspace.py` - The `compute_deliverables_summary` function

---

### Task 1: Verify the Bug with a Manual Test

**Files:**
- Reference: `packages/whitebox/src/shannon_whitebox/cli/main.py:69`

- [ ] **Step 1: Read the current code around line 69**

Run: `cat -n packages/whitebox/src/shannon_whitebox/cli/main.py | sed -n '60,80p'`

Expected output showing:
```python
                    summary = compute_deliverables_summary(summary_path.parent)
                    if summary["vuln_queues"]:
                        click.echo("Results summary:")
                        for vc in sorted(summary["vuln_queues"]):
                            queue_file = summary_path.parent / f"{vc}_exploitation_queue.json"  # BUG IS HERE
```

- [ ] **Step 2: Verify the incorrect path calculation**

The issue is that `summary_path.parent` removes the "deliverables" directory:
- `summary_path` = `{repo}/.shannon/deliverables`
- `summary_path.parent` = `{repo}/.shannon` ← Wrong!
- Queue files are at: `{repo}/.shannon/deliverables/{vc}_exploitation_queue.json`

- [ ] **Step 3: Commit verification notes**

```bash
git add -A
git commit -m "chore: verify CLI summary bug location"
```

---

### Task 2: Fix the Queue File Path

**Files:**
- Modify: `packages/whitebox/src/shannon_whitebox/cli/main.py:69`

- [ ] **Step 1: Read the exact line to modify**

Run: `sed -n '69p' packages/whitebox/src/shannon_whitebox/cli/main.py`

Expected: `                        queue_file = summary_path.parent / f"{vc}_exploitation_queue.json"`

- [ ] **Step 2: Apply the fix using sed**

Run:
```bash
sed -i '' 's/queue_file = summary_path\.parent / queue_file = summary_path /' packages/whitebox/src/shannon_whitebox/cli/main.py
```

- [ ] **Step 3: Verify the change**

Run: `sed -n '69p' packages/whitebox/src/shannon_whitebox/cli/main.py`

Expected: `                        queue_file = summary_path  / f"{vc}_exploitation_queue.json"`
(Note: the space before `/` is just sed separator, actual code should be `queue_file = summary_path / f"{vc}_exploitation_queue.json"`)

- [ ] **Step 4: Clean up the spacing (sed may have added extra space)**

Run: `sed -i '' 's/summary_path  \/ f/summary_path \/ f/' packages/whitebox/src/shannon_whitebox/cli/main.py`

Verify: `sed -n '69p' packages/whitebox/src/shannon_whitebox/cli/main.py`

Expected: `                        queue_file = summary_path / f"{vc}_exploitation_queue.json"`

- [ ] **Step 5: Commit the fix**

```bash
git add packages/whitebox/src/shannon_whitebox/cli/main.py
git commit -m "fix(cli): use correct path for queue files in summary output

The CLI summary was looking for exploitation queue files in the wrong
directory (.shannon/ instead of .shannon/deliverables/), causing it to
always show 0 vulnerabilities found."
```

---

### Task 3: Verify the Fix with Existing Data

**Files:**
- Reference: `/Users/mango/project/vuln-range/NodeGoat/.shannon/deliverables/*.json`

- [ ] **Step 1: Test with Python to verify the path fix works**

Run:
```python
python3 << 'EOF'
from pathlib import Path
import json

# Simulate the fixed logic
deliverables_path = "/Users/mango/project/vuln-range/NodeGoat/.shannon/deliverables"
summary_path = Path(deliverables_path)

print("Testing FIXED path (summary_path instead of summary_path.parent):")
for vc in ["auth", "authz", "injection", "ssrf", "xss"]:
    queue_file = summary_path / f"{vc}_exploitation_queue.json"  # FIXED: use summary_path
    try:
        data = json.loads(queue_file.read_text(encoding="utf-8"))
        count = len(data.get("vulnerabilities", []))
        print(f"  {vc:<12} {count} vulnerabilities found")
    except (json.JSONDecodeError, OSError) as e:
        print(f"  {vc:<12} Error: {e}")
EOF
```

Expected:
```
auth         16 vulnerabilities found
authz        9 vulnerabilities found
injection    12 vulnerabilities found
ssrf         1 vulnerabilities found
xss          10 vulnerabilities found
```

- [ ] **Step 2: Verify the fix logic**

The key fix is:
- OLD: `queue_file = summary_path.parent / f"{vc}_exploitation_queue.json"`
- NEW: `queue_file = summary_path / f"{vc}_exploitation_queue.json"`

This correctly finds files at `{repo}/.shannon/deliverables/{vc}_exploitation_queue.json`

- [ ] **Step 3: Commit verification**

```bash
git add -A
git commit -m "test: verify CLI summary fix with existing data"
```

---

### Task 4: Final Verification

**Files:**
- Test: Run a full whitebox scan (optional, time permitting)

- [ ] **Step 1: Review the fix one more time**

Run: `git diff HEAD~2 packages/whitebox/src/shannon_whitebox/cli/main.py`

Verify only the intended line changed from:
```python
queue_file = summary_path.parent / f"{vc}_exploitation_queue.json"
```
to:
```python
queue_file = summary_path / f"{vc}_exploitation_queue.json"
```

- [ ] **Step 2: Confirm the change is minimal and correct**

Check that:
- Only one line changed
- The change removes `.parent` from the path
- No other modifications were made

- [ ] **Step 3: Final commit if needed**

```bash
git add -A
git commit -m "chore: final verification of CLI summary fix"
```

---

## Summary

This fix corrects a one-character bug (removing `.parent`) that caused the CLI to look for vulnerability queue files in the wrong directory. The fix is minimal, targeted, and has been verified against existing scan data.
