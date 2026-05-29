## ADDED Requirements

### Requirement: Input type definition enumeration
The recon Input Validator Agent SHALL read and enumerate ALL fields from the application's input type definitions (e.g., TypeScript interfaces, Zod schemas, Joi schemas, Pydantic models, JSON Schema) for each endpoint, including any wildcard or catch-all fields (e.g., `[key: string]: unknown`).

#### Scenario: Wildcard input type with hidden parameters
- **WHEN** an input type defines explicit fields AND a wildcard index signature (e.g., `RenderWithDataInput` has `site?`, `lang?`, and `[key: string]: unknown`)
- **THEN** the agent SHALL report both the explicit fields and the existence of the wildcard, noting that additional undeclared parameters may pass through

#### Scenario: Input type without wildcard
- **WHEN** an input type defines only explicit typed fields with no catch-all
- **THEN** the agent SHALL report only those explicit fields

### Requirement: Template variable extraction and cross-reference
The recon Injection Source Tracer Agent SHALL extract all variable names used in template rendering (from template files) and cross-reference them against the input type definitions and parameter construction code. Any variable in a template that could originate from user input but is NOT in the explicit input type definition SHALL be reported as a potentially hidden parameter.

#### Scenario: Parameter arrives through wildcard type
- **WHEN** a template uses `<%- seoPath%>` and `seoPath` is extracted from input via `(_input.seoPath as string) || '{}'` but is NOT declared in the `RenderWithDataInput` interface
- **THEN** the agent SHALL report `seoPath` as a hidden parameter that reaches template rendering through the wildcard

#### Scenario: All parameters are explicitly typed
- **WHEN** all template variables map to explicitly declared fields in the input type
- **THEN** the agent SHALL confirm parameter coverage is complete with no hidden parameters

### Requirement: Parameter completeness matrix
Section 5 of the recon deliverable SHALL include a parameter completeness verification for endpoints that use wildcard input types or pass user input to template rendering. The verification SHALL cross-reference: (a) input type definition fields, (b) parameter construction code fields, and (c) template variable names.

#### Scenario: Gap between input type and template variables
- **WHEN** a template uses variables `seoPath`, `global_content`, `oneTapConfig` but the input type only declares `site`, `lang`, `channel`
- **THEN** the agent SHALL list the undeclared parameters (`seoPath`, `global_content`, `oneTapConfig`) with their extraction code location and template sink location

#### Scenario: Complete parameter coverage
- **WHEN** all template variables map to either explicitly typed input fields or server-computed values
- **THEN** the agent SHALL confirm "all template variables accounted for" with no gaps
