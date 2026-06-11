# SDK Connectivity Test Script — Design Spec

## Problem

Users need to verify that their model provider endpoint is reachable and correctly configured before running a full pentest pipeline. Currently, configuration issues (wrong endpoint, bad credentials, unavailable model) only surface deep into the workflow, wasting time and tokens.

## Solution

A standalone TypeScript script (`scripts/test-sdk-connectivity.ts`) that exercises the same `query()` code path as the production pipeline against all three configured model tiers (small, medium, large). It reports pass/fail per model with timing, and exits with a meaningful code.

## File

`scripts/test-sdk-connectivity.ts` — single file, no new dependencies.

Run via: `pnpm tsx scripts/test-sdk-connectivity.ts`

## Configuration

Reads from the project root `.env` file (auto-loaded via `dotenv`). Supports these variables:

| Variable | Purpose | Required |
|---|---|---|
| `ANTHROPIC_BASE_URL` | API endpoint URL | Yes |
| `ANTHROPIC_AUTH_TOKEN` | Auth token (for custom endpoints) | Yes (or `ANTHROPIC_API_KEY`) |
| `ANTHROPIC_API_KEY` | Anthropic API key | Yes (or `ANTHROPIC_AUTH_TOKEN`) |
| `ANTHROPIC_SMALL_MODEL` | Small tier model name | No (falls back to default) |
| `ANTHROPIC_MEDIUM_MODEL` | Medium tier model name | No (falls back to default) |
| `ANTHROPIC_LARGE_MODEL` | Large tier model name | No (falls back to default) |

Default model names come from `apps/worker/src/ai/models.ts` defaults (haiku-4-5, sonnet-4-6, opus-4-7).

If required variables are missing, the script prints a clear error and exits with code 2.

## Test Logic

For each of the three model tiers:

1. Call `query()` from `@anthropic-ai/claude-agent-sdk` with:
   - `model`: the resolved model name for this tier
   - `prompt`: `"Respond with only the word: OK"`
   - `maxTurns: 1`
   - `permissionMode: 'bypassPermissions'`
   - `allowDangerouslySkipPermissions: true`
   - `cwd`: OS temp directory (no repo context needed)
2. Wrap the call in a 30-second timeout
3. Record wall-clock elapsed time
4. Check that the response contains text (no structured output validation needed — any non-error response means connectivity works)

## Output

Human-readable terminal output:

```
🔗 Shannon SDK Connectivity Test
─────────────────────────────────
Endpoint: https://open.bigmodel.cn/api/anthropic
Auth: ANTHROPIC_AUTH_TOKEN (cb85...4edb)

📡 Testing small model (glm-4.5-air)...
  ✅ OK (1.2s)

📡 Testing medium model (glm-5-turbo)...
  ✅ OK (2.3s)

📡 Testing large model (glm-5.1)...
  ❌ FAILED: Connection timeout after 30s

─────────────────────────────────
Results: 2/3 passed
```

- Endpoint and auth source displayed upfront so the user can verify they're hitting the right server
- Auth token is masked (first 4 / last 4 chars)
- Each model tested sequentially (not parallel) for clear, readable output

## Exit Codes

| Code | Meaning |
|---|---|
| 0 | All models passed |
| 1 | At least one model failed |
| 2 | Configuration error (missing required env vars) |

## Error Handling

- **Timeout**: 30s per model. Reports timeout with the model name.
- **Auth failure**: Catches 401/403 errors, reports as auth issue.
- **Network error**: Catches connection refused, DNS failures, etc.
- **SDK error**: Catches any error thrown by `query()`, prints the message.
- All errors are non-fatal for the overall script — remaining models still get tested.

## Out of Scope

- Performance benchmarking or token counting
- Testing Bedrock or Vertex provider configurations
- Parallel model testing
- Structured output validation
- Any integration with Temporal or the pipeline
