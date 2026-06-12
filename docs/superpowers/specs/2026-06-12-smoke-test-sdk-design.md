# Smoke Test SDK — Design Spec

## Problem

`test-sdk-connectivity.ts` only verifies basic API reachability with a trivial `maxTurns: 1, tools: []` prompt. Non-Claude models (e.g. GLM) pass this test but fail during real pipeline execution with "Content block not found" errors because they don't produce Anthropic-format `tool_use` content blocks.

## Goal

Create a new standalone smoke test script that catches model incompatibilities before a full pipeline run, while also verifying core SDK functionality (tool use, multi-turn conversation).

- **Fast**: completes in under 2 minutes
- **Isolated**: no dependency on pipeline code or real file system
- **Zero new dependencies**: uses only `@anthropic-ai/claude-agent-sdk` and Node built-ins

## Location

`scripts/smoke-test-sdk.ts` — alongside the existing `scripts/test-sdk-connectivity.ts`.

Run with: `npx tsx scripts/smoke-test-sdk.ts`

## Architecture

Three progressive test levels. L1 failure stops the run — subsequent levels cannot succeed if basic connectivity is broken.

```
L1 Connectivity → L2 Tool Use → L3 Multi-Turn → Summary
```

### L1 — Connectivity

Basic API reachability. Reuses existing logic from `test-sdk-connectivity.ts`.

- **Prompt**: `"Respond with only the word: OK"`
- **Config**: `maxTurns: 1`, `tools: []`
- **Validation**: non-empty text response without `API Error:`
- **Timeout**: 30s

### L2 — Tool Use

Registers a virtual tool and verifies the model produces a properly structured `tool_use` content block. This is the key test that exposes non-Claude model incompatibility.

- **Virtual tool**:
  ```json
  {
    "name": "get_weather",
    "description": "Get the current weather for a location",
    "input_schema": {
      "type": "object",
      "properties": { "location": { "type": "string" } },
      "required": ["location"]
    }
  }
  ```
- **Prompt**: `"What's the weather in Tokyo? Use the get_weather tool to check."`
- **Config**: `maxTurns: 1`, `tools: [get_weather]`
- **Validation**: stream contains a `tool_use` content block with parseable `name` and `input`
- **Timeout**: 30s

### L3 — Multi-Turn

Verifies the full tool call → tool result → continued reasoning loop.

- **Virtual tool**: same `get_weather` as L2
- **Prompt**: `"What's the weather in Tokyo and Beijing? Use the get_weather tool for both cities, then compare them."`
- **Config**: `maxTurns: 3`
- **Validation**:
  1. Model issues tool call(s)
  2. SDK constructs `tool_result`
  3. Model receives tool result and continues reasoning
  4. Final text summary is non-empty
- **Timeout**: 60s

## Configuration

Same `INLINE_CONFIG` format as the existing test, simplified to a single model:

```typescript
const INLINE_CONFIG = {
  baseUrl: 'http://localhost:8080/anthropic',
  authToken: '1234567890abcdef',
  authType: 'token' as 'token' | 'key',
  model: 'glm-5.1',
};
```

## Output Format

### Success

```
🔬 Shannon SDK Smoke Test
──────────────────────────────
Endpoint: http://localhost:8080/anthropic
Auth: token (1234...cdef)
Model: glm-5.1

[L1] Connectivity ............ ✅ PASS (2.1s)
     Response: OK

[L2] Tool Use ................ ✅ PASS (4.8s)
     Tool call: get_weather({"location":"Tokyo"})
     Content blocks parsed: tool_use, text

[L3] Multi-Turn .............. ✅ PASS (12.3s)
     Tool calls: 2 (get_weather × 2)
     Final response: 85 chars

──────────────────────────────
Results: 3/3 passed
```

### Failure

```
[L2] Tool Use ................ ❌ FAIL (5.2s)
     Error: Content block not found
     Detail: Expected tool_use content block but response contained:
       {"type": "unknown", "content": "..."}
     Hint: Model may not support Anthropic tool_use format
```

## Error Classification

| Error | Hint |
|---|---|
| Auth failure (401/403) | `Authentication failed — check credentials` |
| Network unreachable (ECONNREFUSED) | `Network unreachable — check baseUrl` |
| Content block format mismatch | `Model may not support Anthropic content block format` |
| No tool_use in response | `Model did not invoke any tools — may lack tool use support` |
| Timeout | `Timeout — model may be too slow or endpoint unresponsive` |
| Empty response | `Empty response — check model name and endpoint` |

## Exit Codes

- `0`: all levels passed
- `1`: at least one level failed
- `2`: configuration error (empty baseUrl or authToken)

## Technical Details

- All levels use `@anthropic-ai/claude-agent-sdk` `query()` — same entry point as the pipeline
- `settingSources: []` for isolation (no user global settings override)
- `persistSession: false` — no session data written
- `cwd` points to `scripts/` directory
- Root user handling: detect `process.getuid() === 0`, fall back to `plan` permission mode. Tool use tests remain valid in `plan` mode because virtual tools don't touch the real filesystem.

## Relationship to Existing Test

`test-sdk-connectivity.ts` remains unchanged. It continues serving as a quick connectivity check. `smoke-test-sdk.ts` is a deeper verification that should be run when setting up a new provider or troubleshooting model compatibility issues.
