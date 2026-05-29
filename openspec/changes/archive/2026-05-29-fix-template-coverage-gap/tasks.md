## 1. Pre-recon XSS Sink Hunter prompt rewrite

- [x] 1.1 Replace the XSS Sink Hunter Agent prompt in `apps/worker/prompts/pre-recon-code.txt` (lines 128-129) with the two-step forced enumeration version: Step 1 (glob template inventory) + Step 2 (per-file sink analysis with escaping mode distinction)
- [x] 1.2 Add cross-variant verification requirement to the new prompt text, explicitly instructing the agent to check for equivalent template files across brand/locale/theme directories

## 2. Pre-recon Section 9 coverage audit table

- [x] 2.1 Insert the "Template Coverage Audit" table requirement into Section 9 of `apps/worker/prompts/pre-recon-code.txt` (after line 269), requiring a table listing every template file, sink count, and analysis status before the detailed sink listing

## 3. Recon static prompt parameter enumeration

- [x] 3.1 Add input type definition enumeration requirement to Section 5 of `apps/worker/prompts/recon-static.txt`, requiring the Input Validator Agent to report wildcard/catch-all fields in addition to explicit fields
- [x] 3.2 Add template variable extraction and cross-reference requirement to Section 9 of `apps/worker/prompts/recon-static.txt`, requiring the Injection Source Tracer Agent to extract template variables and cross-reference against input types and parameter construction code

## 4. Recon live prompt parameter enumeration

- [x] 4.1 Apply the same Section 5 changes from task 3.1 to `apps/worker/prompts/recon.txt`
- [x] 4.2 Apply the same Section 9 changes from task 3.2 to `apps/worker/prompts/recon.txt`

## 5. Verification

- [x] 5.1 Review all modified prompt files for consistency — verify recon-static.txt and recon.txt have identical structural changes in Sections 5 and 9
- [x] 5.2 Verify no other prompt files reference the modified sections in ways that would conflict with the changes
