## 1. Conditional Block Engine

- [x] 1.1 Add `stripConditionalBlocks(content: string, hasWebUrl: boolean): string` function in `apps/worker/src/services/prompt-manager.ts` that strips `<if-live>...</if-live>` or `<if-static>...</if-static>` blocks (including tags) based on `hasWebUrl`
- [x] 1.2 Call `stripConditionalBlocks` inside `interpolateVariables` after `@include()` resolution and before `{{variable}}` interpolation, using `!!variables.webUrl` as the discriminator

## 2. Shared Prompt Partials

- [x] 2.1 Rewrite `apps/worker/prompts/shared/_target.txt` with `<if-live>/<if-static>` blocks — live path keeps current `URL: {{WEB_URL}}`, static path shows `Mode: Offline static code analysis (no live target)`
- [x] 2.2 Rewrite `apps/worker/prompts/shared/_vuln-scope.txt` with `<if-live>/<if-static>` blocks — live path keeps current external-attacker scope, static path provides source-code-analysis scope directive

## 3. Recon-Static Wording Fix

- [x] 3.1 Update `<scope_boundaries>` section in `apps/worker/prompts/recon-static.txt` to replace "network-accessible attack surface" / "network-reachable" / "network requests" with static-analysis-appropriate language
- [x] 3.2 Update `<attacker_perspective>` section in `apps/worker/prompts/recon-static.txt` to replace "public internet" framing with "code paths reachable through web entry points"

## 4. CLI Auto-Inference

- [x] 4.1 Update `apps/cli/src/index.ts` `parseStartArgs`: replace URL-required error block with auto-set `whiteboxOnly = true` when no URL is provided, logging info message "No target URL provided — running in whitebox-only (static analysis) mode"
- [x] 4.2 Update `apps/cli/src/index.ts` `showHelp`: change `-u, --url` description from "required" to "optional, omit for whitebox-only static analysis"
- [x] 4.3 Update `apps/cli/src/commands/local-start.ts`: remove any URL requirement that would prevent whitebox-only invocation without explicit `--whitebox-only`

## 5. Worker Auto-Inference

- [x] 5.1 Update `apps/worker/src/temporal/worker.ts` `parseCliArgs`: when `webUrl` is absent and `blackboxOnly` is false, auto-set `whiteboxOnly = true` and log info message instead of erroring
- [x] 5.2 Update `apps/worker/src/temporal/worker.ts` `buildPipelineInput`: ensure auto-inferred `whiteboxOnly` propagates to `PipelineInput`
