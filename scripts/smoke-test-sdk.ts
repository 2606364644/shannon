#!/usr/bin/env node

// SDK smoke test — progressive three-level verification of model + SDK compatibility.
// All config is in the INLINE_CONFIG block below — no .env needed.
//
// Levels:
//   L1 Connectivity  — basic API reachability
//   L2 Tool Use      — model produces tool_use content blocks
//   L3 Multi-Turn    — full tool call → result → reasoning loop
//
// Usage: npx tsx scripts/smoke-test-sdk.ts
//
// Exit codes: 0 = all passed, 1 = at least one failed, 2 = config error

import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createSdkMcpServer, query } from '@anthropic-ai/claude-agent-sdk';
import { z } from 'zod/v4';

// ╔══════════════════════════════════════════════════════════════════╗
// ║  INLINE CONFIG — edit here to test your model provider          ║
// ╚══════════════════════════════════════════════════════════════════╝

const INLINE_CONFIG = {
  /** API endpoint URL */
  baseUrl: 'http://localhost:8080/anthropic',
  /** Auth token or API key */
  authToken: '1234567890abcdef',
  /** "token" (default) or "key" depending on which field you filled */
  authType: 'token' as 'token' | 'key',
  /** Model to test — single model used across all levels */
  model: 'llm-proxy-anthropic/glm-5.1',
};

// ═══════════════════════════════════════════════════════════════════

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

const TIMEOUT_MS = { l1: 30_000, l2: 30_000, l3: 60_000 } as const;

// bypassPermissions requires a non-root user (Claude Code CLI restriction).
const IS_ROOT = process.getuid?.() === 0;
const PERMISSION_MODE = IS_ROOT ? ('plan' as const) : ('bypassPermissions' as const);

// === Types ===

interface TestResult {
  level: 'L1' | 'L2' | 'L3';
  label: string;
  passed: boolean;
  durationMs: number;
  detail: string;
  hint?: string;
}

// === Output Helpers ===

function maskToken(token: string): string {
  if (token.length <= 8) return '****';
  return `${token.slice(0, 4)}...${token.slice(-4)}`;
}

function printHeader(endpoint: string, authSource: string, authToken: string, model: string): void {
  console.log('🔬 Shannon SDK Smoke Test');
  console.log('──────────────────────────────');
  console.log(`Endpoint: ${endpoint}`);
  console.log(`Auth: ${authSource} (${maskToken(authToken)})`);
  console.log(`Model: ${model}`);
  console.log();
}

function printResult(result: TestResult): void {
  const status = result.passed ? '✅ PASS' : '❌ FAIL';
  const dots = '.'.repeat(Math.max(1, 20 - result.label.length));
  const duration = `(${(result.durationMs / 1000).toFixed(1)}s)`;

  console.log(`[${result.level}] ${result.label} ${dots} ${status} ${duration}`);

  if (result.passed) {
    console.log(`     ${result.detail}`);
  } else {
    console.log(`     Error: ${result.detail}`);
    if (result.hint) {
      console.log(`     Hint: ${result.hint}`);
    }
  }
  console.log();
}

function printSummary(results: TestResult[]): void {
  console.log('──────────────────────────────');
  const passed = results.filter((r) => r.passed).length;
  console.log(`Results: ${passed}/${results.length} passed`);
}

// === Shared SDK Config ===

function buildSdkEnv(): Record<string, string> {
  return {
    ANTHROPIC_BASE_URL: INLINE_CONFIG.baseUrl,
    ...(INLINE_CONFIG.authType === 'key'
      ? { ANTHROPIC_API_KEY: INLINE_CONFIG.authToken }
      : { ANTHROPIC_AUTH_TOKEN: INLINE_CONFIG.authToken }),
    HOME: process.env.HOME || '',
    PATH: process.env.PATH || '',
  };
}

function baseOptions(abortController: AbortController) {
  return {
    model: INLINE_CONFIG.model,
    cwd: resolve(SCRIPT_DIR),
    permissionMode: PERMISSION_MODE,
    ...(!IS_ROOT && { allowDangerouslySkipPermissions: true }),
    settingSources: [],
    env: buildSdkEnv(),
    persistSession: false,
    abortController,
  };
}

// === Error Classification ===

function classifyError(error: Error): { detail: string; hint: string } {
  if (error.name === 'AbortError') {
    return { detail: 'Timeout', hint: 'Model may be too slow or endpoint unresponsive' };
  }
  if (error.message.includes('401') || error.message.includes('403')) {
    return { detail: error.message, hint: 'Authentication failed — check credentials' };
  }
  if (error.message.includes('ECONNREFUSED') || error.message.includes('ENOTFOUND')) {
    return { detail: error.message, hint: 'Network unreachable — check baseUrl' };
  }
  if (error.message.includes('Content block not found')) {
    return {
      detail: error.message,
      hint: 'Model may not support Anthropic content block format',
    };
  }
  if (error.message.includes('maximum number of turns')) {
    return {
      detail: error.message,
      hint: 'Model exceeded turn limit — may indicate tool use loop or planning mode blocking tool execution',
    };
  }
  return { detail: error.message.slice(0, 300), hint: 'Unexpected error — check model and endpoint configuration' };
}

// === L1: Connectivity ===

async function testConnectivity(): Promise<TestResult> {
  const start = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS.l1);

  try {
    let resultText = '';

    const stream = query({
      prompt: 'Respond with only the word: OK',
      options: {
        ...baseOptions(controller),
        maxTurns: 1,
        tools: [],
      },
    });

    for await (const message of stream) {
      if (message.type === 'result' && message.subtype === 'success') {
        resultText = (message as { result?: string }).result || '';
        break;
      }
    }

    clearTimeout(timer);
    const durationMs = Date.now() - start;
    const isApiError = resultText.includes('API Error:') || resultText.includes('"type":"error"');

    if (resultText && !isApiError) {
      const preview = resultText.trim().split('\n')[0].slice(0, 200);
      return { level: 'L1', label: 'Connectivity', passed: true, durationMs, detail: `Response: ${preview}` };
    }

    const errorDetail = isApiError ? resultText.trim().split('\n')[0].slice(0, 200) : 'Empty response';
    return {
      level: 'L1',
      label: 'Connectivity',
      passed: false,
      durationMs,
      detail: errorDetail,
      hint: isApiError
        ? 'API returned an error — check model name and endpoint'
        : 'Empty response — check model name and endpoint',
    };
  } catch (error) {
    clearTimeout(timer);
    const durationMs = Date.now() - start;
    const { detail, hint } = classifyError(error as Error);
    return { level: 'L1', label: 'Connectivity', passed: false, durationMs, detail, hint };
  }
}

// === Virtual Tool (MCP Server) ===

// createSdkMcpServer registers custom tools as an in-process MCP server.
// This is the only way to add custom tools — the SDK's `tools` option only
// accepts built-in tool names (string[]), not tool definition objects.
const weatherServer = createSdkMcpServer({
  name: 'weather',
  version: '1.0.0',
  tools: [
    {
      name: 'get_weather',
      description: 'Get the current weather for a location',
      inputSchema: {
        location: z.string().describe('City name'),
      },
      handler: async (args) => ({
        content: [{ type: 'text' as const, text: `Weather in ${args.location}: 22°C, Sunny` }],
      }),
    },
  ],
});

// === L2: Tool Use ===

async function testToolUse(): Promise<TestResult> {
  const start = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS.l2);

  let toolCallsFound = 0;
  const toolCallNames: string[] = [];
  const toolCallInputs: string[] = [];
  const contentBlockTypes = new Set<string>();
  let resultText = '';

  try {
    const stream = query({
      prompt: 'What is the weather in Tokyo? You MUST use the get_weather tool to check. Do not guess.',
      options: {
        ...baseOptions(controller),
        maxTurns: 2,
        tools: [],
        mcpServers: { weather: weatherServer },
      },
    });

    for await (const message of stream) {
      // Parse assistant messages for content blocks
      if (message.type === 'assistant') {
        const content = (message as { message?: { content?: unknown[] } }).message?.content;
        if (Array.isArray(content)) {
          for (const block of content) {
            const typedBlock = block as { type?: string; name?: string; input?: unknown; text?: string };
            if (typedBlock.type) {
              contentBlockTypes.add(typedBlock.type);
            }
            if (typedBlock.type === 'tool_use') {
              toolCallsFound++;
              if (typedBlock.name) {
                toolCallNames.push(typedBlock.name);
              }
              toolCallInputs.push(JSON.stringify(typedBlock.input || {}));
            }
          }
        }
      }

      // Capture final result
      if (message.type === 'result' && message.subtype === 'success') {
        resultText = (message as { result?: string }).result || '';
        break;
      }
    }

    clearTimeout(timer);
    const durationMs = Date.now() - start;

    // Check for API errors in result text
    const isApiError = resultText.includes('API Error:') || resultText.includes('"type":"error"');

    if (isApiError) {
      const errorDetail = resultText.trim().split('\n')[0].slice(0, 200);
      return {
        level: 'L2',
        label: 'Tool Use',
        passed: false,
        durationMs,
        detail: errorDetail,
        hint: 'API returned an error during tool use — model may not support tool_use format',
      };
    }

    // Verify tool_use content block was produced
    if (toolCallsFound > 0) {
      const callSummary = toolCallNames.map((n, i) => `${n}(${toolCallInputs[i]})`).join(', ');
      const blockTypes = [...contentBlockTypes].sort().join(', ');
      return {
        level: 'L2',
        label: 'Tool Use',
        passed: true,
        durationMs,
        detail: `Tool call: ${callSummary} | Content blocks: ${blockTypes}`,
      };
    }

    // No tool call found — model may not support tool use
    const blockTypes = contentBlockTypes.size > 0 ? [...contentBlockTypes].sort().join(', ') : 'none';
    return {
      level: 'L2',
      label: 'Tool Use',
      passed: false,
      durationMs,
      detail: `No tool_use content block found. Content blocks: ${blockTypes}`,
      hint: 'Model did not invoke any tools — may lack Anthropic tool_use support',
    };
  } catch (error) {
    clearTimeout(timer);
    const durationMs = Date.now() - start;

    // If tool_use blocks were found before the error, L2 passes — the core
    // test (does the model produce tool_use?) succeeded even if the SDK hit
    // max turns or another non-fatal error.
    if (toolCallsFound > 0) {
      const callSummary = toolCallNames.map((n, i) => `${n}(${toolCallInputs[i]})`).join(', ');
      const blockTypes = [...contentBlockTypes].sort().join(', ');
      return {
        level: 'L2',
        label: 'Tool Use',
        passed: true,
        durationMs,
        detail: `Tool call: ${callSummary} | Content blocks: ${blockTypes}`,
      };
    }

    const { detail, hint } = classifyError(error as Error);
    return { level: 'L2', label: 'Tool Use', passed: false, durationMs, detail, hint };
  }
}

// === L3: Multi-Turn ===

async function testMultiTurn(): Promise<TestResult> {
  const start = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS.l3);

  let toolCallsFound = 0;
  const toolCallNames: string[] = [];
  let resultText = '';
  let assistantTurns = 0;

  try {
    const stream = query({
      prompt:
        'What is the weather in Tokyo and Beijing? Use the get_weather tool for BOTH cities, then provide a brief comparison.',
      options: {
        ...baseOptions(controller),
        maxTurns: 3,
        tools: [],
        mcpServers: { weather: weatherServer },
      },
    });

    for await (const message of stream) {
      // Count assistant turns
      if (message.type === 'assistant') {
        assistantTurns++;
        const content = (message as { message?: { content?: unknown[] } }).message?.content;
        if (Array.isArray(content)) {
          for (const block of content) {
            const typedBlock = block as { type?: string; name?: string };
            if (typedBlock.type === 'tool_use') {
              toolCallsFound++;
              if (typedBlock.name) {
                toolCallNames.push(typedBlock.name);
              }
            }
          }
        }
      }

      // Capture final result
      if (message.type === 'result' && message.subtype === 'success') {
        resultText = (message as { result?: string }).result || '';
        break;
      }
    }

    clearTimeout(timer);
    const durationMs = Date.now() - start;

    // Check for API errors
    const isApiError = resultText.includes('API Error:') || resultText.includes('"type":"error"');

    if (isApiError) {
      const errorDetail = resultText.trim().split('\n')[0].slice(0, 200);
      return {
        level: 'L3',
        label: 'Multi-Turn',
        passed: false,
        durationMs,
        detail: errorDetail,
        hint: 'API error during multi-turn — model may not support tool_result round-trips',
      };
    }

    // Verify we got tool calls AND a final text response
    if (toolCallsFound > 0 && resultText.trim().length > 0) {
      const toolSummary =
        toolCallNames.length > 0
          ? `${toolCallsFound} (${[...new Set(toolCallNames)].map((n) => `${n}`).join(', ')} × ${toolCallNames.length})`
          : `${toolCallsFound}`;
      return {
        level: 'L3',
        label: 'Multi-Turn',
        passed: true,
        durationMs,
        detail: `Tool calls: ${toolSummary} | Turns: ${assistantTurns} | Final response: ${resultText.trim().length} chars`,
      };
    }

    // Partial success or failure
    if (toolCallsFound === 0) {
      return {
        level: 'L3',
        label: 'Multi-Turn',
        passed: false,
        durationMs,
        detail: 'No tool calls found across multi-turn conversation',
        hint: 'Model may not support sustained tool use across turns',
      };
    }

    return {
      level: 'L3',
      label: 'Multi-Turn',
      passed: false,
      durationMs,
      detail: `Tool calls: ${toolCallsFound} but empty final response`,
      hint: 'Model produced tool calls but failed to generate final summary',
    };
  } catch (error) {
    clearTimeout(timer);
    const durationMs = Date.now() - start;

    // If tool_use blocks were found but no final text, report partial success
    // with a clearer message than a generic error.
    if (toolCallsFound > 0 && !resultText?.trim()) {
      return {
        level: 'L3',
        label: 'Multi-Turn',
        passed: false,
        durationMs,
        detail: `Tool calls: ${toolCallsFound} but empty final response`,
        hint: 'Model produced tool calls but failed to generate final summary',
      };
    }

    const { detail, hint } = classifyError(error as Error);
    return { level: 'L3', label: 'Multi-Turn', passed: false, durationMs, detail, hint };
  }
}

// === Main ===

async function main(): Promise<number> {
  // 1. Validate config
  if (!INLINE_CONFIG.baseUrl) {
    console.error('❌ INLINE_CONFIG.baseUrl is empty');
    return 2;
  }
  if (!INLINE_CONFIG.authToken) {
    console.error('❌ INLINE_CONFIG.authToken is empty');
    return 2;
  }
  if (!INLINE_CONFIG.model) {
    console.error('❌ INLINE_CONFIG.model is empty');
    return 2;
  }

  const authSource = INLINE_CONFIG.authType === 'key' ? 'API_KEY' : 'AUTH_TOKEN';

  // 2. Print header
  printHeader(INLINE_CONFIG.baseUrl, authSource, INLINE_CONFIG.authToken, INLINE_CONFIG.model);

  if (IS_ROOT) {
    console.log(`⚠️  Running as root — using '${PERMISSION_MODE}' mode (bypassPermissions unavailable)`);
    console.log();
  }

  // 3. Run tests
  const results: TestResult[] = [];

  // L1
  const l1 = await testConnectivity();
  printResult(l1);
  results.push(l1);

  if (!l1.passed) {
    printSummary(results);
    return 1;
  }

  // L2
  const l2 = await testToolUse();
  printResult(l2);
  results.push(l2);

  if (!l2.passed) {
    printSummary(results);
    return 1;
  }

  // L3
  const l3 = await testMultiTurn();
  printResult(l3);
  results.push(l3);

  printSummary(results);
  return results.every((r) => r.passed) ? 0 : 1;
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error('Fatal error:', err);
    process.exit(2);
  });
