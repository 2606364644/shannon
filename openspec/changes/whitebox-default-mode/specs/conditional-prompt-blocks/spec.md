## ADDED Requirements

### Requirement: Conditional block syntax
The prompt template engine SHALL support `<if-live>...</if-live>` and `<if-static>...</if-static>` paired conditional blocks. The engine SHALL resolve these blocks based on whether a target URL is present in the prompt variables.

#### Scenario: URL present — live block retained, static block stripped
- **WHEN** `variables.webUrl` is a non-empty string
- **THEN** content inside `<if-live>...</if-live>` SHALL be retained (opening/closing tags removed), and content inside `<if-static>...</if-static>` SHALL be entirely removed including tags

#### Scenario: URL absent — static block retained, live block stripped
- **WHEN** `variables.webUrl` is undefined or empty
- **THEN** content inside `<if-static>...</if-static>` SHALL be retained (opening/closing tags removed), and content inside `<if-live>...</if-live>` SHALL be entirely removed including tags

### Requirement: Conditional block processing order
Conditional block processing SHALL occur after `@include()` resolution and before `{{variable}}` interpolation. This allows conditional blocks to appear inside shared included partials.

#### Scenario: Conditional block inside an included partial
- **WHEN** a shared partial file (e.g., `shared/_target.txt`) contains `<if-live>/<if-static>` blocks
- **THEN** the blocks SHALL be resolved correctly after the partial is inlined via `@include()`

### Requirement: Conditional block coexistence with existing section stripping
The `<if-live>/<if-static>` processing SHALL NOT interfere with existing conditional section stripping (`<rules>`, `<code_path_rules>`, `<rules_of_engagement>`). Both mechanisms SHALL operate independently.

#### Scenario: Template contains both conditional blocks and rules sections
- **WHEN** a template contains `<if-live>/<if-static>` AND `<rules>...</rules>` sections
- **THEN** both SHALL be processed correctly without interaction or ordering issues

### Requirement: Static vuln scope directive
When no target URL is present, the vulnerability scope directive SHALL instruct agents to report all code-level vulnerabilities discoverable through source code analysis, including unsafe data flows, missing validation, insecure defaults, and hardcoded secrets — classified by the code path that would be exercised at runtime.

#### Scenario: Whitebox-only scan receives static scope directive
- **WHEN** a whitebox-only scan runs the injection vuln agent
- **THEN** the agent prompt SHALL contain a scope directive about source code analysis, NOT "Only report vulnerabilities exploitable via (offline — source code analysis only) from the internet"

### Requirement: Static target block
When no target URL is present, the target block SHALL indicate offline static code analysis mode with no live target, rather than showing "URL: (offline — source code analysis only)".

#### Scenario: Whitebox-only scan receives static target block
- **WHEN** a whitebox-only scan runs any vuln agent
- **THEN** the target section SHALL display "Mode: Offline static code analysis (no live target)" instead of "URL: (offline — source code analysis only)"
