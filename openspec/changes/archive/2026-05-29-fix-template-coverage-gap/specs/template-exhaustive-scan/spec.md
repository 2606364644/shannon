## ADDED Requirements

### Requirement: Template file inventory enumeration
The XSS Sink Hunter Agent SHALL perform a glob-based enumeration of ALL template and view files in the project before analyzing individual files. The enumeration SHALL cover common template extensions (html, ejs, hbs, pug, jsx, tsx, vue, svelte, php, erb, jinja2, tmpl) and any additional template extensions discovered during analysis.

#### Scenario: Multi-brand template project
- **WHEN** the codebase contains variant template directories (e.g., `views/moomoo/` and `views/futu/`)
- **THEN** the agent SHALL enumerate template files in ALL variant directories and report the complete file inventory organized by directory tree

#### Scenario: Single-brand project with no templates
- **WHEN** the codebase contains no template or view files
- **THEN** the agent SHALL explicitly report "no template files found" and skip template sink analysis

### Requirement: Cross-variant template verification
The XSS Sink Hunter Agent SHALL verify that equivalent template files exist and are analyzed across all variant directories (brands, locales, themes, sub-applications). When a template file is found in one variant directory (e.g., `views/brandA/header/variables.html`), the agent MUST check whether equivalent files exist in other variant directories (e.g., `views/brandB/header/variables.html`) and analyze them independently.

#### Scenario: Brand variant with matching template structure
- **WHEN** `views/moomoo/cmps/header/variables.html` is found and analyzed
- **AND** `views/futu/cmps/header/variables.html` exists in the codebase
- **THEN** both files SHALL be independently analyzed and reported with their respective sinks

#### Scenario: Locale variant with differing template structure
- **WHEN** `views/en/header.html` contains 5 sinks and `views/ja/header.html` contains 7 sinks
- **THEN** both files SHALL be reported independently with their exact sink counts — the agent SHALL NOT assume they are identical

### Requirement: Per-file sink analysis with escaping mode
The XSS Sink Hunter Agent SHALL analyze each template file independently for sinks, reporting the escaping/output mode for each occurrence. For server-side template engines, the agent SHALL distinguish between escaped directives (e.g., EJS `<%= %>`, Jinja2 `{{ }}`) and unescaped directives (e.g., EJS `<%- %>`, Jinja2 `{{|safe}}`).

#### Scenario: Template with mixed escaping modes
- **WHEN** a template file contains `<%- JSON.stringify(footerExtraShow)%>` and `<%= site %>`
- **THEN** the agent SHALL report the first as "unescaped" and the second as "HTML-escaped", as they have different security implications

#### Scenario: Template with bare unescaped output
- **WHEN** a template file contains `<%- seoPath%>` without any `JSON.stringify` wrapper
- **THEN** the agent SHALL flag this as the highest-risk sink pattern (bare unescaped output)

### Requirement: Template coverage audit table
Section 9 of the pre-recon deliverable SHALL include a "Template Coverage Audit" table listing every template/view file discovered, the number of sinks found, and analysis status. This table SHALL be placed before the detailed sink listing.

#### Scenario: Complete coverage with sinks
- **WHEN** 4 template files are discovered and all 4 are analyzed
- **THEN** the table SHALL list all 4 files with their sink counts and "Analyzed" status

#### Scenario: Gap in coverage
- **WHEN** 4 template files are discovered but only 3 are analyzed
- **THEN** the table SHALL show the missing file with "NOT ANALYZED" status, making the gap visible to downstream agents
