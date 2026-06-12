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
import { query } from '@anthropic-ai/claude-agent-sdk';

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
  model: 'glm-5.1',
};

// ═══════════════════════════════════════════════════════════════════

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

const TIMEOUT_MS: Record<string, number> = {
  l1: 30_000,
  l2: 30_000,
  l3: 60_000,
};

// bypassPermissions requires a non-root user (Claude Code CLI restriction).
const IS_ROOT = process.getuid?.() === 0;
const PERMISSION_MODE = IS_ROOT ? ('plan' as const) : ('bypassPermissions' as const);

// === Types ===

interface TestResult {
  level: string;
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
    settingSources: [] as const,
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

  // L2 — placeholder for Task 2
  // L3 — placeholder for Task 3

  printSummary(results);
  return results.every((r) => r.passed) ? 0 : 1;
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error('Fatal error:', err);
    process.exit(2);
  });
