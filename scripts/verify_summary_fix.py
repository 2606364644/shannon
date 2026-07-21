#!/usr/bin/env python3
"""
Verification script for CLI summary queue file path fix.

This script verifies that the fix correctly reads queue files from:
{repo}/.supernova/deliverables/{vc}_exploitation_queue.json

The bug was using summary_path.parent which would look in:
{repo}/.supernova/{vc}_exploitation_queue.json (incorrect)

The fix uses summary_path which correctly looks in:
{repo}/.supernova/deliverables/{vc}_exploitation_queue.json (correct)
"""
from pathlib import Path
import json

def test_fixed_path_logic():
    """Test the fixed path logic with existing NodeGoat scan data."""
    deliverables_path = "/Users/mango/project/vuln-range/NodeGoat/.supernova/deliverables"
    summary_path = Path(deliverables_path)

    print("Testing FIXED path logic:")
    print(f"  deliverables_path: {deliverables_path}")
    print(f"  summary_path: {summary_path}")
    print()

    expected_counts = {
        "auth": 16,
        "authz": 9,
        "injection": 12,
        "ssrf": 1,
        "xss": 10
    }

    all_passed = True

    for vc in ["auth", "authz", "injection", "ssrf", "xss"]:
        # FIXED: use summary_path directly, not summary_path.parent
        queue_file = summary_path / f"{vc}_exploitation_queue.json"
        expected = expected_counts[vc]

        try:
            data = json.loads(queue_file.read_text(encoding="utf-8"))
            count = len(data.get("vulnerabilities", []))
            status = "✓" if count == expected else "✗"
            print(f"  {status} {vc:<12} {count} vulnerabilities found (expected {expected})")
            if count != expected:
                all_passed = False
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ✗ {vc:<12} Error: {e}")
            all_passed = False

    print()
    if all_passed:
        print("✓ All tests passed!")
        return 0
    else:
        print("✗ Some tests failed!")
        return 1

if __name__ == "__main__":
    exit(test_fixed_path_logic())
