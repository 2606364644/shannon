## ADDED Requirements

### Requirement: Misconfig agent discovers Open Redirect vulnerabilities

The misconfig-vuln agent SHALL identify all server-side redirect sinks in the codebase (framework redirect methods, HTTP Location headers, meta refresh tags) and trace user-controllable input to those sinks. For each candidate endpoint, the agent SHALL evaluate whether redirect target validation is sufficient (domain whitelist, relative path enforcement) or absent/bypassable.

#### Scenario: Open Redirect via unvalidated query parameter
- **WHEN** the codebase contains an endpoint that calls `res.redirect(req.query.url)` without URL validation
- **THEN** the agent SHALL produce a queue entry with `vulnerability_type: "Open_Redirect"`, `vulnerable_parameter` set to the parameter name, `redirect_sink` set to the sink function, and `suggested_exploit_technique` set to a specific bypass technique

#### Scenario: Open Redirect with insufficient validation
- **WHEN** the codebase validates redirect URLs by checking `url.startsWith('/')` but does not reject `'//'` prefix
- **THEN** the agent SHALL produce a queue entry with `existing_validation` describing the insufficient check, `confidence: "High"`, and `suggested_exploit_technique: "open_redirect_protocol_bypass"`

#### Scenario: Redirect endpoint with domain whitelist
- **WHEN** the codebase validates redirect URLs against a whitelist of allowed domains
- **THEN** the agent SHALL mark the endpoint as safe and document it in the "Secure by Design" section of the deliverable

### Requirement: Misconfig agent checks Security Headers

The misconfig-vuln agent SHALL check all HTTP responses for presence and correctness of security headers: Content-Security-Policy, Strict-Transport-Security, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, and Permissions-Policy.

#### Scenario: Missing Content-Security-Policy header
- **WHEN** the application serves HTML pages without a Content-Security-Policy header (no CSP middleware configured)
- **THEN** the agent SHALL produce a queue entry with `vulnerability_type: "Missing_Security_Headers"` and `missing_defense` describing the absent CSP

#### Scenario: HSTS header with insufficient max-age
- **WHEN** the application sets Strict-Transport-Security with `max-age` below 1 year (31536000 seconds)
- **THEN** the agent SHALL produce a queue entry noting the weak HSTS configuration

#### Scenario: API-only service without CSP
- **WHEN** the application serves only JSON API responses and does not set CSP
- **THEN** the agent SHALL NOT flag the missing CSP as a vulnerability, noting in `notes` that CSP is not applicable for non-HTML responses

### Requirement: Misconfig agent checks CORS configuration

The misconfig-vuln agent SHALL analyze CORS middleware configuration for dynamic origin reflection, wildcard origins with credentials, and overly permissive allowed methods/headers.

#### Scenario: CORS reflects arbitrary origins with credentials
- **WHEN** the CORS middleware sets `Access-Control-Allow-Origin` to the request Origin value and `Access-Control-Allow-Credentials` to `true`
- **THEN** the agent SHALL produce a queue entry with `vulnerability_type: "CORS_Misconfiguration"` and `suggested_exploit_technique: "cors_origin_reflection"`

#### Scenario: CORS wildcard without credentials
- **WHEN** the CORS middleware sets `Access-Control-Allow-Origin: *` without `Access-Control-Allow-Credentials`
- **THEN** the agent SHALL mark this as safe (public API pattern) and document it in the "Secure by Design" section

### Requirement: Misconfig agent checks Cookie security flags

The misconfig-vuln agent SHALL verify that all session and authentication cookies set the HttpOnly, Secure, and SameSite flags.

#### Scenario: Session cookie missing HttpOnly flag
- **WHEN** the application sets a session cookie without the `HttpOnly` flag
- **THEN** the agent SHALL produce a queue entry with `vulnerability_type: "Missing_Cookie_Flags"` and `missing_defense` specifying the absent flag

#### Scenario: Cookie with all security flags
- **WHEN** the application sets a cookie with `HttpOnly`, `Secure`, and `SameSite=Lax|Strict`
- **THEN** the agent SHALL mark the cookie as safe

### Requirement: Misconfig agent checks Clickjacking protection

The misconfig-vuln agent SHALL verify that HTML pages are protected against framing by CSP `frame-ancestors` directive or `X-Frame-Options` header.

#### Scenario: No frame-busting protection
- **WHEN** the application serves HTML pages without `Content-Security-Policy: frame-ancestors` or `X-Frame-Options` header
- **THEN** the agent SHALL produce a queue entry with `vulnerability_type: "Clickjacking_Vulnerable"`

### Requirement: Misconfig agent checks Information Disclosure

The misconfig-vuln agent SHALL verify that error responses do not expose stack traces, server version headers, database error details, or debug-mode information.

#### Scenario: Verbose error page in production
- **WHEN** the application error handler returns stack traces or internal paths in error responses
- **THEN** the agent SHALL produce a queue entry with `vulnerability_type: "Information_Disclosure"` and `missing_defense` describing the leaked information type

#### Scenario: Generic error handler configured
- **WHEN** the application uses a generic error handler that returns sanitized error messages
- **THEN** the agent SHALL mark the error handling as safe

### Requirement: Misconfig queue schema follows defense-missing pattern

The misconfig exploitation queue SHALL use a JSON schema with base fields (`ID`, `vulnerability_type`, `externally_exploitable`, `source_endpoint`, `vulnerable_code_location`, `missing_defense`, `exploitation_hypothesis`, `suggested_exploit_technique`, `confidence`, `notes`) plus optional Open Redirect fields (`vulnerable_parameter`, `redirect_sink`, `existing_validation`).

#### Scenario: Open Redirect queue entry
- **WHEN** the agent identifies an Open Redirect vulnerability
- **THEN** the queue entry SHALL include `vulnerable_parameter`, `redirect_sink`, and `existing_validation` fields in addition to base fields

#### Scenario: Security Headers queue entry
- **WHEN** the agent identifies a missing security header
- **THEN** the queue entry SHALL include only base fields; Open Redirect optional fields SHALL be absent

### Requirement: Misconfig agent runs in whitebox-only mode

The misconfig-vuln agent SHALL be included in the `WHITEBOX_VULN_CLASSES` array, enabling it to run without a live URL target. The agent SHALL analyze code configuration files, middleware settings, and redirect logic purely through static code review.

#### Scenario: Whitebox-only run with misconfig
- **WHEN** the pipeline runs in whitebox-only mode (`SHANNON_WHITEBOX_ONLY=1`)
- **THEN** the misconfig-vuln agent SHALL execute alongside injection, auth, authz, and ssrf agents

#### Scenario: Whitebox confidence levels
- **WHEN** the misconfig agent identifies a finding without browser verification
- **THEN** the confidence SHALL be capped at Medium unless the code evidence is deterministic (e.g., missing middleware registration)
