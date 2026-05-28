## Why

Whitebox scanning already works without a URL (`--whitebox-only`), but it is treated as a secondary mode. Users must explicitly opt in. Meanwhile, the prompts shared by all 5 vuln agents contain contradictory instructions when no URL is present — `_vuln-scope.txt` resolves to "Only report vulnerabilities exploitable via (offline — source code analysis only) from the internet," which is semantically incoherent and may degrade LLM analysis quality. Whitebox should be the natural default: provide a repo and get results, no URL required.

## What Changes

- **CLI auto-detection**: When no `-u/--url` is provided, automatically run in whitebox-only mode instead of erroring. Remove the requirement for `--whitebox-only` flag in the no-URL case.
- **Worker auto-inference**: The Temporal worker entry point already has partial fallback logic; extend it to consistently infer `whiteboxOnly` when `webUrl` is absent.
- **Conditional prompt blocks**: Introduce `<if-live>/<if-static>` conditional sections in shared prompt templates, following the existing `<rules>...</rules>` stripping pattern. This replaces the ambiguous `(offline — source code analysis only)` fallback text with intentional, mode-specific instructions.
- **Shared partial fixes**: Rewrite `shared/_vuln-scope.txt` and `shared/_target.txt` to contain both mode variants using conditional blocks.
- **Recon-static wording cleanup**: Fix contradictory inline language in `recon-static.txt` where "EXTERNAL ATTACKER ... from the internet" and "offline/static analysis mode" coexist without reconciliation.
- **Help text update**: Reflect that `--url` is optional, not required.

## Capabilities

### New Capabilities
- `conditional-prompt-blocks`: Extends the prompt template engine with `<if-live>/<if-static>` conditional sections that are resolved based on whether a target URL is present. Follows the existing section-stripping pattern (`<rules>`, `<rules_of_engagement>`, `<code_path_rules>`).
- `whitebox-default-cli`: CLI and worker entry points automatically infer whitebox-only mode when no target URL is provided, making `--whitebox-only` a redundant-but-accepted flag rather than a required opt-in.

### Modified Capabilities

## Impact

- **apps/worker/src/services/prompt-manager.ts**: New `stripConditionalBlocks` function, called during `interpolateVariables`.
- **apps/worker/prompts/shared/_target.txt**: Rewritten with `<if-live>/<if-static>` blocks.
- **apps/worker/prompts/shared/_vuln-scope.txt**: Rewritten with `<if-live>/<if-static>` blocks.
- **apps/worker/prompts/recon-static.txt**: Inline wording fixes for `<scope_boundaries>` and `<attacker_perspective>` sections.
- **apps/cli/src/index.ts**: Remove URL-required error, auto-set `whiteboxOnly = true` when no URL.
- **apps/cli/src/docker.ts**: Already handles `whiteboxOnly` flag — no change expected.
- **apps/worker/src/temporal/worker.ts**: Extend existing fallback logic to consistently infer `whiteboxOnly`.
- **apps/cli/src/commands/local-start.ts**: Remove URL requirement.
