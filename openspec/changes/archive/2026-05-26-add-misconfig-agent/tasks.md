## 1. Type Definitions

- [x] 1.1 Add `'misconfig'` to `VulnType` union and `'misconfig-vuln' | 'misconfig-exploit'` to `ALL_AGENTS` array in `apps/worker/src/types/agents.ts`
- [x] 1.2 Add `'misconfig'` to `VulnClass` union and `ALL_VULN_CLASSES` array in `apps/worker/src/types/config.ts`
- [x] 1.3 Extend `PlaywrightSession` type to include `'agent6'` in `apps/worker/src/types/agents.ts`

## 2. Agent Registration

- [x] 2.1 Add `misconfig-vuln` and `misconfig-exploit` entries to `AGENTS` record in `apps/worker/src/session-manager.ts` (promptTemplate: `'vuln-misconfig'` / `'exploit-misconfig'`, deliverableFilenames, modelTier, prerequisites)
- [x] 2.2 Add `misconfig-vuln` → `'agent6'` and `misconfig-exploit` → `'agent6'` to `PLAYWRIGHT_SESSION_MAPPING`
- [x] 2.3 Add `misconfig-vuln` and `misconfig-exploit` validators to `AGENT_VALIDATORS` (vuln uses `createVulnValidator('misconfig')`, exploit uses `createExploitValidator('misconfig')`)
- [x] 2.4 Add `misconfig-vuln` and `misconfig-exploit` to `AGENT_PHASE_MAP`

## 3. Queue Schema

- [x] 3.1 Add `MisconfigVulnerability` Zod schema and `MisconfigFinding` type in `apps/worker/src/ai/queue-schemas.ts` (base fields + optional `vulnerable_parameter`, `redirect_sink`, `existing_validation`)
- [x] 3.2 Add `misconfig-vuln` output format entries to both `OUTPUT_FORMATS_EXPLOIT` and `OUTPUT_FORMATS_ANALYSIS` maps
- [x] 3.3 Add `misconfig-vuln` → `'misconfig_exploitation_queue.json'` to `VULN_AGENT_QUEUE_FILENAMES`

## 4. Queue Validation

- [x] 4.1 Add `misconfig` entry to `VULN_TYPE_CONFIG` in `apps/worker/src/services/queue-validation.ts` (deliverable: `'misconfig_analysis_deliverable.md'`, queue: `'misconfig_exploitation_queue.json'`)

## 5. Prompt Templates

- [x] 5.1 Create `apps/worker/prompts/vuln-misconfig.txt` with role, objective, scope, methodology covering all 6 sub-types (Open Redirect Phase A-D, Security Headers, CORS, Cookie Flags, Clickjacking, Info Disclosure), deliverable instructions, and conclusion trigger
- [x] 5.2 Create `apps/worker/prompts/exploit-misconfig.txt` with role, proof levels (1-4), per-sub-type exploit workflows, evidence deliverable template, and conclusion trigger

## 6. Findings Renderer

- [x] 6.1 Add `MisconfigFinding` import and misconfig `ClassConfig` entry to `apps/worker/src/services/findings-renderer.ts` with render function for queue entries
- [x] 6.2 Add misconfig to the renderer dispatch logic (classConfigs array or equivalent)

## 7. Workflow Integration

- [x] 7.1 Add `misconfig` to `buildPipelineConfigs()` in `apps/worker/src/temporal/workflows.ts` (both vulnAgentName/exploitAgentName and activity functions)
- [x] 7.2 Add `misconfig` to `WHITEBOX_VULN_CLASSES` array
- [x] 7.3 Add `misconfig-vuln` to the whitebox `vulnAgents` array (with `runMisconfigVulnAgent` activity)
- [x] 7.4 Add `misconfig-exploit` to report agent prerequisites in `AGENTS` (both full and whitebox report agent)

## 8. Activities

- [x] 8.1 Add `runMisconfigVulnAgent` and `runMisconfigExploitAgent` activity functions in `apps/worker/src/temporal/activities.ts` following the existing thin-wrapper pattern
- [x] 8.2 Add `'misconfig'` to `VulnTypeValues` array in `validateDeliverablesExist` function

## 9. Config & Prompt Manager

- [x] 9.1 Add `'misconfig'` to `vuln_classes` enum in `apps/worker/configs/config-schema.json`
- [x] 9.2 Update default vuln_classes fallback in `apps/worker/src/services/prompt-manager.ts` to include `'misconfig'`
- [x] 9.3 Update comment in `apps/worker/configs/example-config.yaml` to list all 6 classes

## 10. Verification

- [x] 10.1 Run `pnpm run check` and fix any type errors
- [x] 10.2 Run `pnpm biome` and fix lint/format issues
- [x] 10.3 Verify the misconfig agent appears in `openspec status` or pipeline startup logs
