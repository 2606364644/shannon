# Report Translation Design

Automatically translate all markdown deliverables to Chinese after a scan completes.

## Overview

After `generateReportOutputActivity` runs at the end of every pipeline workflow (pentest, whitebox, blackbox), a `ReportTranslationProvider` scans the `deliverables/` directory, translates each `.md` file to Chinese using Claude, and writes the results to `deliverables-cn/`.

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `apps/worker/src/providers/report-translation-provider.ts` | New | Implements `ReportOutputProvider`, orchestrates scan/translate/write |
| `apps/worker/src/providers/translation-prompt.ts` | New | Translation prompt template matching existing `-cn.md` style |
| `apps/worker/src/providers/register.ts` | New | Calls `setContainerFactory()` to inject the translation provider |
| `apps/worker/src/temporal/worker.ts` | Modify 1 line | Add `import '../providers/register.js'` at the top |

All new code lives under `apps/worker/src/providers/`. The only change to an existing file is a single import line in `worker.ts`.

## Architecture

```
worker.ts
  └── import '../providers/register.js'    ← side effect: setContainerFactory()
        └── ReportTranslationProvider      ← injected into every Container

Workflow (workflows.ts)
  └── generateReportOutputActivity         ← existing official hook
        └── container.reportOutputProvider.generate(input, logger)
              └── ReportTranslationProvider.generate()
                    ├── Scan deliverables/*.md
                    ├── For each file: runClaudePrompt(modelTier='small')
                    └── Write deliverables-cn/{name}-cn.md
```

## Component Details

### report-translation-provider.ts

Implements `ReportOutputProvider` interface from `apps/worker/src/interfaces/report-output-provider.ts`.

```
generate(input: ActivityInput, logger: ActivityLogger) → Promise<{ outputPath?: string }>

Steps:
1. Resolve source directory: path.join(input.repoPath, input.deliverablesSubdir || '.shannon/deliverables')
2. Read all *.md files from the source directory
3. If no files found, return early with {}
4. Create output directory: path.join(input.repoPath, '.shannon/deliverables-cn')
5. For each .md file:
   a. Read file content
   b. Call runClaudePrompt() with the translation prompt + file content
   c. Write translated content to {outputDir}/{basename without .md}-cn.md
   d. Log success or warning on failure
6. Return { outputPath: outputDir }
```

Calls `runClaudePrompt()` from `apps/worker/src/ai/claude-executor.ts` with:
- `modelTier: 'small'` (Haiku — sufficient for translation, lowest cost)
- `apiKey`, `deliverablesSubdir`, `providerConfig` from the container config
- This reuses the same SDK infrastructure as all other agents (provider routing, billing, error handling)

### translation-prompt.ts

Exports a function that builds the translation prompt. The prompt encodes the translation style observed in existing `-cn.md` files:

- Markdown structure preserved exactly (headings, lists, tables, code blocks)
- Vulnerability IDs kept in English (e.g., `INJ-VULN-01`, `AUTH-VULN-10`)
- HTTP methods, paths, status codes, URLs kept in English
- Technical abbreviations kept in English (XSS, SSRF, CSRF, RBAC, IDOR, SSO, etc.)
- Severity levels shown bilingually: `严重 (Critical)`, `高 (High)`, `中 (Medium)`, `低 (Low)`
- Narrative and descriptive text translated to Chinese
- File paths and code snippets kept in English
- The prompt instructs Claude to return only the translated markdown, no preamble

### register.ts

```typescript
import { setContainerFactory, Container } from '../services/container.js';
import { ReportTranslationProvider } from './report-translation-provider.js';

const factory: ContainerFactory = (workflowId, sessionMetadata, config) => {
  return new Container({
    sessionMetadata,
    config,
    reportOutputProvider: new ReportTranslationProvider(),
  });
};

export function registerProviders(): void {
  setContainerFactory(factory);
}
```

Called via side-effect import in `worker.ts`.

### worker.ts modification

One line added in the import section at the top of the file:

```typescript
import '../providers/register.js';
```

This import executes `registerProviders()` as a side effect, which calls `setContainerFactory()` before any workflow starts.

## Output Directory Structure

```
{repoPath}/
└── .shannon/
    ├── deliverables/                              # Original (unchanged)
    │   ├── comprehensive_security_assessment_report.md
    │   ├── injection_findings.md
    │   ├── xss_findings.md
    │   └── ...
    └── deliverables-cn/                           # Chinese translations
        ├── comprehensive_security_assessment_report-cn.md
        ├── injection_findings-cn.md
        ├── xss_findings-cn.md
        └── ...
```

File naming: `{original-name without .md}-cn.md` (e.g., `report.md` → `report-cn.md`).

## Error Handling

Translation is best-effort — failures must never affect the pipeline success/failure status:

- Single file translation failure: skip that file, log a warning, continue with remaining files
- Source directory missing or empty: return `{}` immediately, no error
- All files fail to translate: return `{ outputPath: undefined }`
- `generate()` never throws — all exceptions caught internally
- Translation status logged via `ActivityLogger` for post-scan debugging

## Design Decisions

1. **Why ReportOutputProvider over a standalone script?** The official interface is the intended extension point. Running as a Temporal Activity gives us heartbeat support and automatic logging in the workflow log.

2. **Why runClaudePrompt instead of raw Anthropic SDK?** Consistency with the rest of the codebase. Reuses provider routing (Bedrock/Vertex/LiteLLM), billing tracking, and error handling infrastructure.

3. **Why Haiku (small tier)?** Translation is a straightforward task. Haiku is fast and inexpensive while producing high-quality Chinese translations of security reports.

4. **Why deliverables-cn/ instead of -cn suffix in same directory?** Separation keeps the original directory pristine and makes it easy to copy/share just the Chinese versions.

5. **Why auto-trigger instead of opt-in?** The user's requirement is to always translate after a scan. If needed later, an environment variable like `SHANNON_TRANSLATE=false` can be added as a kill switch without changing the architecture.

## Future Considerations

- **Environment variable opt-out:** `SHANNON_TRANSLATE=false` to disable translation without code changes
- **Additional languages:** The provider pattern supports future `ReportOutputProvider` implementations for other languages
- **Parallel translation:** For workspaces with many deliverable files, translations could run concurrently (currently sequential to keep things simple)
