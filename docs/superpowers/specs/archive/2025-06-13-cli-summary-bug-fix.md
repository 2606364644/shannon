# CLI Summary Bug Fix Design

## Problem Description

After a white-box scan completes, the CLI summary incorrectly shows "0 vulnerabilities found" for all vulnerability categories, even though the scan successfully found vulnerabilities and saved them to queue files.

## Root Cause

The CLI looks for exploitation queue files in the wrong directory:
- **Looking in**: `{repo}/.shannon/*.json`
- **Actual location**: `{repo}/.shannon/deliverables/*.json`

## Fix

### File to Change
`packages/whitebox/src/shannon_whitebox/cli/main.py`

### Code Change

**Current (line ~100)**:
```python
queue_file = summary_path.parent / f"{vc}_exploitation_queue.json"
```

**Fixed**:
```python
queue_file = summary_path / f"{vc}_exploitation_queue.json"
```

### Explanation

- `summary_path` = `/path/to/repo/.shannon/deliverables`
- Queue files are located at `summary_path/{vc}_exploitation_queue.json`
- The original code used `summary_path.parent`, causing it to look in `/path/to/repo/.shannon/` instead

## Expected Result After Fix

The CLI summary should correctly display vulnerability counts:

```
Results summary:
  ├─ auth         16 vulnerabilities found
  ├─ authz        9 vulnerabilities found
  ├─ injection    12 vulnerabilities found
  ├─ ssrf         1 vulnerabilities found
  ├─ xss          10 vulnerabilities found
```

## Testing

1. Run a white-box scan on a test repository
2. Verify the summary shows correct vulnerability counts
3. Verify the counts match the actual queue file contents
