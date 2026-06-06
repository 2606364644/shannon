# Remove Misconfig Vulnerability Class

**Date:** 2026-06-06
**Status:** Approved
**Scope:** Remove the misconfig vulnerability detection and exploitation feature entirely

## Background

Shannon-Py supports 6 vulnerability classes: injection, xss, auth, ssrf, authz, and misconfig. The misconfig feature detects security misconfigurations (missing headers, CORS issues, cookie flags, open redirects, information disclosure, clickjacking). It is fully implemented with two agents (`misconfig-vuln` and `misconfig-exploit`), dedicated prompts, schemas, and test coverage.

The feature is no longer needed and should be completely removed.

## Decision

Remove all misconfig-related code, prompts, models, tests, and configuration. Do not leave any traces or deprecated stubs.

## Changes

### Files to Delete

| File | Description |
|------|-------------|
| `prompts/vuln-misconfig.txt` | Vulnerability analysis prompt (359 lines) |
| `prompts/misconfig-exploit.txt` | Exploitation prompt (383 lines) |
| `prompts/vuln-misconfig.txt.backup` | Historical backup (89 lines) |

### Source Code Modifications

#### `packages/core/src/shannon_core/models/agents.py`

- Remove `"misconfig"` from `VulnType` literal
- Remove `MISCONFIG_VULN` and `MISCONFIG_EXPLOIT` from `AgentName`
- Remove both agent definitions from `AGENTS` registry
- Remove `AgentName.MISCONFIG_EXPLOIT` from `REPORT` prerequisites
- Remove `"misconfig"` from `ALL_VULN_CLASSES`
- Remove `"misconfig-vuln"` and `"misconfig-exploit"` from `AGENT_PHASE_MAP`

#### `packages/core/src/shannon_core/models/config.py`

- Remove `"misconfig"` from `VulnClass` literal
- Remove `"misconfig"` from `ALL_VULN_CLASSES` list

#### `packages/core/src/shannon_core/models/queue_schemas.py`

- Remove `MisconfigVulnerability` class definition
- Remove `MisconfigVulnerability` from `Vulnerability` Union type

#### `packages/core/src/shannon_core/services/findings_renderer.py`

- Remove `MisconfigVulnerability` import
- Remove `render_misconfig_entry` function
- Remove `"misconfig"` entry from `CLASS_CONFIG`

#### `packages/core/src/shannon_core/services/playwright_config_writer.py`

- Remove `"misconfig-exploit": "agent-misconfig"` from `AGENT_SESSION_MAPPING`

### Test Modifications

#### `packages/core/tests/test_agents.py`

- Remove 7 misconfig-related test functions:
  - `test_misconfig_vuln_agent_name`
  - `test_misconfig_exploit_agent_name`
  - `test_misconfig_vuln_in_registry`
  - `test_misconfig_exploit_in_registry`
  - `test_misconfig_vuln_prerequisites`
  - `test_misconfig_exploit_prerequisites`
  - `test_report_includes_misconfig_exploit`

#### `packages/core/tests/test_config.py`

- Remove `test_misconfig_in_vuln_class`
- Remove `test_all_vuln_classes_includes_misconfig`
- Update `ALL_VULN_CLASSES` count assertion (6 → 5) in any remaining tests

#### `packages/core/tests/test_queue_schemas.py`

- Remove `test_misconfig_vulnerability`
- Remove `test_misconfig_in_vulnerability_union`

#### `packages/core/tests/test_findings_renderer.py`

- Remove `MisconfigVulnerability` import
- Remove `render_misconfig_entry` import
- Remove `test_render_misconfig_entry_full`

#### `packages/core/tests/test_agent_phase_map.py`

- Remove `test_misconfig_agents_mapped`

#### `packages/blackbox/tests/test_integration.py`

- Update hardcoded vuln class lists that include "misconfig"
- Tests iterating over `ALL_VULN_CLASSES` will automatically exclude misconfig

## Impact Assessment

- **VulnType/VulnClass** shrinks from 6 to 5 values: `["injection", "xss", "auth", "ssrf", "authz"]`
- **Agent registry** loses 2 agents; report agent prerequisites drop from 6 to 5 exploit dependencies
- **Dependency chain** severed: `recon → misconfig-vuln → misconfig-exploit → report`
- **Other vulnerability classes** are unaffected
- **Workflow orchestration** simply runs one fewer parallel branch in vulnerability analysis phase

## Verification

1. Run full test suite: `pytest packages/`
2. Grep for residual references: `grep -r "misconfig" --include="*.py" --include="*.txt" --include="*.md" packages/ prompts/`
3. Confirm `ALL_VULN_CLASSES` count is 5
4. Confirm no broken imports or dangling references
