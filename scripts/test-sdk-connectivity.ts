#!/usr/bin/env node

// SDK connectivity test — verifies model provider reachability for all three tiers.
// All config is in the INLINE_CONFIG block below — no .env needed.
//
// Usage: npx tsx scripts/test-sdk-connectivity.ts
//
// Exit codes: 0 = all passed, 1 = at least one failed, 2 = config error

import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { query } from '@anthropic-ai/claude-agent-sdk';

// ╔══════════════════════════════════════════════════════════════════╗
// ║  INLINE CONFIG — edit here to test your model provider          ║
// ╚══════════════════════════════════════════════════════════════════╝

const INLINE_CONFIG = {
  /** API endpoint URL, e.g. "https://open.bigmodel.cn/api/anthropic" */
  baseUrl: 'http://localhost:8080/anthropic',
  /** Auth token or API key */
  authToken: '1234567890abcdef',
  /** Set to "token" (default) or "key" depending on which field you filled */
  authType: 'token' as 'token' | 'key',
  /** Model overrides — leave empty string to use defaults */
  smallModel: 'glm-5.1',
  mediumModel: 'glm-5.1',
  largeModel: 'glm-5.1',
};

// ═══════════════════════════════════════════════════════════════════

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const MODEL_TIERS = ['small', 'medium', 'large'] as const;
const TIMEOUT_MS = 30_000;

// bypassPermissions requires a non-root user (Claude Code CLI restriction).
// Fall back to plan mode when running as root — still verifies API connectivity.
const IS_ROOT = process.getuid?.() === 0;
const PERMISSION_MODE = IS_ROOT ? ('plan' as const) : ('bypassPermissions' as const);

// === Output Helpers ===

function maskToken(token: string): string {
  if (token.length <= 8) return '****';
  return `${token.slice(0, 4)}...${token.slice(-4)}`;
}

function printHeader(endpoint: string, authSource: string, authToken: string, permissionMode: string): void {
  console.log('🔗 Shannon SDK Connectivity Test');
  console.log('─────────────────────────────────');
  console.log(`Endpoint: ${endpoint}`);
  console.log(`Auth: ${authSource} (${maskToken(authToken)})`);
  console.log(`Mode: ${permissionMode}${IS_ROOT ? ' (root detected — bypassPermissions unavailable)' : ''}`);
  console.log();
}

function printFooter(passed: number, total: number): void {
  console.log('─────────────────────────────────');
  console.log(`Results: ${passed}/${total} passed`);
}

// === Model Test ===

interface TestResult {
  tier: string;
  model: string;
  passed: boolean;
  durationMs: number;
  error?: string;
}

async function testModel(tier: string, model: string, sdkEnv: Record<string, string>): Promise<TestResult> {
  const start = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);

  console.log(`📡 Testing ${tier} model (${model})...`);

  try {
    let resultText = '';

    const stream = query({
      prompt: 'Respond with only the word: OK',
      options: {
        model,
        maxTurns: 1,
        cwd: resolve(SCRIPT_DIR),
        permissionMode: PERMISSION_MODE,
        ...(!IS_ROOT && { allowDangerouslySkipPermissions: true }),
        tools: [],
        // IMPORTANT: Empty settingSources enters SDK isolation mode — skip loading
        // ~/.claude/settings.json (and project/local settings). Without this, the user's
        // global settings.json `env` block overrides INLINE_CONFIG below, so the test
        // silently hits a different endpoint/token than the one printed in the header.
        settingSources: [],
        env: sdkEnv,
        persistSession: false,
        abortController: controller,
      },
    });

    for await (const message of stream) {
      if (message.type === 'result' && message.subtype === 'success') {
        resultText = message.result || '';
        break;
      }
    }

    const durationMs = Date.now() - start;
    clearTimeout(timer);

    const isApiError = resultText.includes('API Error:') || resultText.includes('"type":"error"');

    if (resultText && !isApiError) {
      console.log(`  ✅ OK (${(durationMs / 1000).toFixed(1)}s)`);
      console.log(`  📝 ${resultText.trim().split('\n')[0].slice(0, 200)}`);
      console.log();
      return { tier, model, passed: true, durationMs };
    }

    const errorDetail = isApiError ? resultText.trim().split('\n')[0].slice(0, 200) : 'Empty response';
    console.log(`  ❌ FAILED: ${isApiError ? 'API error' : 'Empty response'}`);
    console.log(`  📝 ${errorDetail}`);
    console.log();
    return { tier, model, passed: false, durationMs, error: errorDetail };
  } catch (error) {
    clearTimeout(timer);
    const durationMs = Date.now() - start;
    const err = error as Error;
    let errorMessage = err.message;

    if (err.name === 'AbortError') {
      errorMessage = `Timeout after ${TIMEOUT_MS / 1000}s`;
    } else if (err.message.includes('401') || err.message.includes('403')) {
      errorMessage = 'Authentication failed (check credentials)';
    } else if (err.message.includes('ECONNREFUSED') || err.message.includes('ENOTFOUND')) {
      errorMessage = `Network error: ${err.message}`;
    }

    console.log(`  ❌ FAILED: ${errorMessage}`);
    console.log();
    return { tier, model, passed: false, durationMs, error: errorMessage };
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

  const authSource = INLINE_CONFIG.authType === 'key' ? 'API_KEY' : 'AUTH_TOKEN';

  // 2. Resolve model names
  const models: Record<string, string> = {
    small: INLINE_CONFIG.smallModel,
    medium: INLINE_CONFIG.mediumModel,
    large: INLINE_CONFIG.largeModel,
  };

  // 3. Build SDK env
  const sdkEnv: Record<string, string> = {
    ANTHROPIC_BASE_URL: INLINE_CONFIG.baseUrl,
    ...(INLINE_CONFIG.authType === 'key'
      ? { ANTHROPIC_API_KEY: INLINE_CONFIG.authToken }
      : { ANTHROPIC_AUTH_TOKEN: INLINE_CONFIG.authToken }),
    HOME: process.env.HOME || '',
    PATH: process.env.PATH || '',
  };

  // 4. Print header
  printHeader(INLINE_CONFIG.baseUrl, authSource, INLINE_CONFIG.authToken, PERMISSION_MODE);

  // 5. Test each model tier sequentially
  const results: TestResult[] = [];
  for (const tier of MODEL_TIERS) {
    const result = await testModel(tier, models[tier], sdkEnv);
    results.push(result);
  }

  // 6. Summary
  const passed = results.filter((r) => r.passed).length;
  printFooter(passed, results.length);

  return passed === results.length ? 0 : 1;
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error('Fatal error:', err);
    process.exit(2);
  });
