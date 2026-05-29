## Why

Shannon's pre-recon and recon phases have a template coverage gap that causes the scanner to miss XSS vulnerabilities in variant template files. In a real scan of `official_common_header_footer`, the scanner found sinks in 3 of 4 `variables.html` template files but silently skipped `futu/cmps/header/variables.html` — the file containing the most dangerous unsanitized sink (`seoPath: JSON.parse('<%- seoPath%>')`). This is not an edge case: any multi-brand, multi-locale, or multi-theme application will have variant template directories, and the scanner currently has no mechanism to ensure all variants are exhaustively analyzed.

## What Changes

- **Pre-recon XSS Sink Hunter Agent prompt** (`pre-recon-code.txt`): Replace the single-sentence "find all sinks" instruction with a mandatory two-step process: (1) enumerate all template files via glob, (2) analyze each file independently. Add explicit cross-variant verification (brands, locales, themes).
- **Pre-recon Section 9 report format** (`pre-recon-code.txt`): Add a mandatory "Template Coverage Audit" table that lists every template file and its analysis status, making gaps visible to downstream agents.
- **Recon Section 5 Input Vectors** (`recon-static.txt`, `recon.txt`): Add parameter completeness verification step that cross-references input type definitions, parameter construction code, and template variables to ensure no parameters are silently missed.
- **Recon Section 9 Injection Sources** (`recon-static.txt`, `recon.txt`): Add template variable extraction requirement to the Injection Source Tracer Agent prompt, ensuring parameters that reach template rendering are enumerated even when they come through wildcard input types.

## Capabilities

### New Capabilities
- `template-exhaustive-scan`: Ensures all template/view files are enumerated and independently analyzed during pre-recon, with cross-variant verification for brands, locales, and themes
- `input-parameter-enumeration`: Ensures recon phase enumerates all parameters that flow from HTTP input to template rendering by cross-referencing input type definitions, construction code, and template variables

### Modified Capabilities

## Impact

- **Files modified**: `apps/worker/prompts/pre-recon-code.txt`, `apps/worker/prompts/recon-static.txt`, `apps/worker/prompts/recon.txt`
- **Prompt token budget**: Minor increase — template enumeration adds ~200 tokens to pre-recon agent, cross-reference instructions add ~150 tokens to recon agents
- **Scan duration**: Negligible — the enumeration step is lightweight; per-file analysis is work the agent should already be doing
- **Downstream agents**: No changes needed — injection, auth, authz, ssrf, and report agents consume recon output and will automatically benefit from improved upstream coverage
- **Backward compatibility**: No breaking changes — existing scan results are a subset of the new coverage
