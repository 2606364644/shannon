## Context

Shannon supports three pipeline modes: full (whitebox + blackbox), whitebox-only, and blackbox-only. The whitebox-only mode already exists as `--whitebox-only` and runs `whiteboxPipelineWorkflow` — a 4-phase pipeline (pre-recon → recon-static → 5 vuln agents → report) that skips exploit agents and XSS. However, two problems remain:

1. **UX friction**: Users must pass `--whitebox-only` to run without a URL. The CLI errors with "url is required" if omitted.
2. **Prompt quality**: Shared prompt templates (`shared/_target.txt`, `shared/_vuln-scope.txt`) use `{{WEB_URL}}` which resolves to the fallback string `(offline — source code analysis only)`. This produces semantically contradictory instructions like "Only report vulnerabilities exploitable via (offline — source code analysis only) from the internet."

The prompt manager already has a section-stripping pattern: `<rules>...</rules>`, `<code_path_rules>...</code_path_rules>`, and `<rules_of_engagement>...</rules_of_engagement>` are conditionally stripped based on whether they have content. This change extends that pattern with bidirectional conditional blocks.

## Goals / Non-Goals

**Goals:**
- Make whitebox the default when no URL is provided (zero-config code scanning)
- Fix contradictory prompt instructions in whitebox-only mode
- Introduce a reusable conditional block mechanism for prompt templates
- Maintain full backward compatibility — `--whitebox-only` flag still works

**Non-Goals:**
- Changing the vuln agent prompt files themselves (they are already white-box-focused)
- Adding new vuln classes or agents to the whitebox pipeline
- Modifying the blackbox-only workflow
- Creating separate static-mode prompt files for each vuln agent (Path A approach)

## Decisions

### 1. Conditional blocks over separate template files

**Decision**: Use `<if-live>/<if-static>` conditional blocks in shared templates, resolved by `prompt-manager.ts`.

**Alternatives considered**:
- **Separate static template files** (`_target-static.txt`, `_vuln-scope-static.txt`): Would require extending `processIncludes` to accept a mode parameter and resolve to different files. Creates maintenance burden for 6-line and 1-line files. Doesn't generalize to inline sections in larger prompts.
- **Rewrite shared templates to be mode-agnostic**: Would lose the specificity that makes the live-target scope directive valuable for blackbox mode.

**Rationale**: The existing section-stripping pattern (`<rules>...</rules>`) is already established. Bidirectional conditional blocks are a natural extension — zero new concepts, single source of truth per template, and both modes visible for comparison.

### 2. Condition resolved by webUrl presence, not a separate mode flag

**Decision**: `stripConditionalBlocks(content, hasWebUrl: boolean)` uses `variables.webUrl` presence as the discriminator.

**Rationale**: `webUrl` is already available in `interpolateVariables`. No new plumbing needed. The presence/absence of a URL IS the mode distinction — no need for a separate boolean.

### 3. Auto-inference at CLI and worker entry points

**Decision**: Both `apps/cli/src/index.ts` and `apps/worker/src/temporal/worker.ts` auto-set `whiteboxOnly = true` when no URL is provided, instead of erroring.

**Rationale**: The worker entry point already has partial fallback (L133-136: repositioning args when `whiteboxOnly` is set). Extending this to auto-infer is minimal. The CLI change is a single `if` condition rewrite.

### 4. Recon-static inline wording: reconcile, don't bifurcate

**Decision**: Edit `recon-static.txt` inline to use language that is accurate for static analysis. The "external attacker" perspective is still valid (you analyze from attacker POV), but "from the public internet" and "network-accessible" should be reframed as "code paths reachable through web entry points."

**Rationale**: `recon-static.txt` is already a separate file only used in whitebox mode. No conditional blocks needed — just fix the wording.

## Risks / Trade-offs

- **[Prompt regression]** → Existing full-pipeline scans use the same shared templates. The `<if-live>` block must produce identical output to the current unconditional text. Mitigation: the live-mode text is copy-pasted into `<if-live>` unchanged.

- **[Conditional block misuse]** → Teams might overuse `<if-live>/<if-static>` for things that should be separate templates. Mitigation: document that this is for small shared partials only; large mode-specific prompts (like `recon.txt` vs `recon-static.txt`) should remain separate files.

- **[Auto-inference surprise]** → Users who forget `-u` will silently get a whitebox scan instead of an error. Mitigation: log an explicit info message "No target URL provided — running in whitebox-only (static analysis) mode." This makes the auto-inference visible without blocking the user.
