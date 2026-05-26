## Why

Shannon currently covers 5 vulnerability classes (injection, XSS, auth, authz, SSRF) but has a significant blind spot: HTTP-level security misconfigurations including Open Redirect, missing security headers, CORS misconfigurations, missing cookie flags, clickjacking exposure, and information disclosure. Open Redirect is explicitly absent — the auth agent mentions it only as a sub-check within login flow responses, covering auth-related redirects but not general open redirect endpoints. This change adds a `misconfig` vulnerability class as a first-class pipeline stage, making Security Misconfiguration (OWASP A05:2021) a dedicated analysis target.

## What Changes

- Add `misconfig` as a new `VulnClass` and `VulnType` alongside the existing 5 classes
- Add `misconfig-vuln` and `misconfig-exploit` agents to the pipeline, each with dedicated prompt templates
- Add a Zod queue schema for structured misconfig findings (shared base fields + Open Redirect–specific optional fields)
- Add queue validation, deliverable rendering, and workflow orchestration entries for the new class
- Include `misconfig` in the whitebox workflow (code-only analysis is sufficient for all sub-types; browser is optional enhancement)
- Update config schema to accept `misconfig` in `vuln_classes`
- Update report agent prerequisites to include `misconfig-exploit`

## Capabilities

### New Capabilities

- `misconfig-analysis`: White-box analysis of HTTP-level security misconfigurations across 6 sub-types: Open Redirect, Security Headers, CORS, Cookie Flags, Clickjacking, and Information Disclosure. Covers code audit methodology for finding missing or incorrect security controls at the HTTP response layer.
- `misconfig-exploitation`: Live verification and weaponization of misconfig findings. Open Redirect exploit includes protocol/encoding bypass techniques and browser-confirmed redirect verification. Other sub-types produce proof-of-concept attacks demonstrating impact of missing controls.

### Modified Capabilities

## Impact

- **Types layer**: `VulnType`, `VulnClass`, `ALL_AGENTS`, `AgentName` in `types/agents.ts` and `types/config.ts`
- **Agent registration**: `session-manager.ts` (AGENTS, PLAYWRIGHT_SESSION_MAPPING, AGENT_VALIDATORS)
- **Queue infrastructure**: `queue-validation.ts` (VULN_TYPE_CONFIG), `queue-schemas.ts` (new MisconfigVulnerability schema + output format), `findings-renderer.ts` (new renderer)
- **Workflow orchestration**: `workflows.ts` (buildPipelineConfigs, WHITEBOX_VULN_CLASSES, whitebox vulnAgents)
- **Activities**: `activities.ts` (new runMisconfigVulnAgent, runMisconfigExploitAgent, validateDeliverablesExist update)
- **Prompts**: 2 new prompt templates (`vuln-misconfig.txt`, `exploit-misconfig.txt`)
- **Config**: `config-schema.json` (vuln_classes enum), `example-config.yaml` (comment update)
- **Prompt manager**: `prompt-manager.ts` (default vuln_classes fallback list)
- Parallel agent count increases from 5 to 6 in the vulnerability-analysis and exploitation phases
