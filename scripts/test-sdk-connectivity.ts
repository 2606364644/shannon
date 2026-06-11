#!/usr/bin/env node

// SDK connectivity test — verifies model provider reachability for all three tiers.
// Reads config from the INLINE_CONFIG block below, falls back to .env if unset.
//
// Usage:
//   npx tsx scripts/test-sdk-connectivity.ts
//
// Exit codes: 0 = all passed, 1 = at least one failed, 2 = config error

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { query } from '@anthropic-ai/claude-agent-sdk';

// ╔══════════════════════════════════════════════════════════════════╗
// ║  INLINE CONFIG — edit here, leave empty to read from .env      ║
// ╚══════════════════════════════════════════════════════════════════╝

const INLINE_CONFIG = {
  /** API endpoint URL, e.g. "https://open.bigmodel.cn/api/anthropic" */
  baseUrl: '',
  /** Auth token or API key */
  authToken: '',
  /** Set to "token" (default) or "key" depending on which field you filled */
  authType: 'token' as 'token' | 'key',
  /** Model overrides — leave empty string to use defaults */
  smallModel: '',
  mediumModel: '',
  largeModel: '',
};

// ═══════════════════════════════════════════════════════════════════

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = resolve(SCRIPT_DIR, '..');
const ENV_FILE = resolve(PROJECT_ROOT, '.env');
const MODEL_TIERS = ['small', 'medium', 'large'] as const;
const TIMEOUT_MS = 30_000;

// bypassPermissions requires a non-root user (Claude Code CLI restriction).
// Fall back to plan mode when running as root — still verifies API connectivity.
const IS_ROOT = process.getuid?.() === 0;
const PERMISSION_MODE = IS_ROOT ? ('plan' as const) : ('bypassPermissions' as const);

const DEFAULT_MODELS: Record<string, string> = {
  small: 'claude-haiku-4-5-20251001',
  medium: 'claude-sonnet-4-6',
  large: 'claude-opus-4-7',
};

const ENV_VAR_MAP: Record<string, string> = {
  small: 'ANTHROPIC_SMALL_MODEL',
  medium: 'ANTHROPIC_MEDIUM_MODEL',
  large: 'ANTHROPIC_LARGE_MODEL',
};

// === .env Parser ===

/** Minimal .env parser — handles KEY=VALUE lines, ignores comments and blanks. */
function parseEnvFile(filePath: string): Record<string, string> {
  const content = readFileSync(filePath, 'utf-8');
  const env: Record<string, string> = {};
  for (const line of content.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eqIndex = trimmed.indexOf('=');
    if (eqIndex === -1) continue;
    const key = trimmed.slice(0, eqIndex).trim();
    let value = trimmed.slice(eqIndex + 1).trim();
    // Strip surrounding quotes
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    env[key] = value;
  }
  return env;
}

// === Output Helpers ===

function maskToken(token: string): string {
  if (token.length <= 8) return '****';
  return `${token.slice(0, 4)}...${token.slice(-4)}`;
}

function printHeader(
  endpoint: string,
  authSource: string,
  authToken: string,
  permissionMode: string,
  configSource: string,
): void {
  console.log('🔗 Shannon SDK Connectivity Test');
  console.log('─────────────────────────────────');
  console.log(`Endpoint: ${endpoint}`);
  console.log(`Auth: ${authSource} (${maskToken(authToken)})`);
  console.log(`Config: ${configSource}`);
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

    if (resultText) {
      console.log(`  ✅ OK (${(durationMs / 1000).toFixed(1)}s)`);
      console.log();
      return { tier, model, passed: true, durationMs };
    }

    console.log(`  ❌ FAILED: Empty response`);
    console.log();
    return { tier, model, passed: false, durationMs, error: 'Empty response' };
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
  // 1. Load config: INLINE_CONFIG takes precedence, then fall back to .env
  let env: Record<string, string> = {};
  try {
    env = parseEnvFile(ENV_FILE);
  } catch {
    // .env missing is OK if INLINE_CONFIG is filled
  }

  const baseUrl = INLINE_CONFIG.baseUrl || env.ANTHROPIC_BASE_URL;
  const authToken =
    INLINE_CONFIG.authType === 'token' ? INLINE_CONFIG.authToken || env.ANTHROPIC_AUTH_TOKEN : env.ANTHROPIC_AUTH_TOKEN;
  const apiKey =
    INLINE_CONFIG.authType === 'key' ? INLINE_CONFIG.authToken || env.ANTHROPIC_API_KEY : env.ANTHROPIC_API_KEY;

  // 2. Validate required config
  if (!baseUrl) {
    console.error('❌ No endpoint configured. Set INLINE_CONFIG.baseUrl or ANTHROPIC_BASE_URL in .env');
    return 2;
  }

  const effectiveToken = authToken || apiKey;
  if (!effectiveToken) {
    console.error(
      '❌ No auth configured. Set INLINE_CONFIG.authToken or ANTHROPIC_AUTH_TOKEN/ANTHROPIC_API_KEY in .env',
    );
    return 2;
  }

  const configSource = INLINE_CONFIG.baseUrl ? 'inline' : '.env';
  const authSource = INLINE_CONFIG.authToken
    ? INLINE_CONFIG.authType === 'key'
      ? 'INLINE (ANTHROPIC_API_KEY)'
      : 'INLINE (ANTHROPIC_AUTH_TOKEN)'
    : authToken
      ? 'ANTHROPIC_AUTH_TOKEN'
      : 'ANTHROPIC_API_KEY';

  // 3. Resolve model names
  const models: Record<string, string> = {};
  const inlineModels = {
    small: INLINE_CONFIG.smallModel,
    medium: INLINE_CONFIG.mediumModel,
    large: INLINE_CONFIG.largeModel,
  };
  for (const tier of MODEL_TIERS) {
    models[tier] = inlineModels[tier] || env[ENV_VAR_MAP[tier]] || DEFAULT_MODELS[tier];
  }

  // 4. Build SDK env (only pass what the SDK needs)
  const sdkEnv: Record<string, string> = {
    ANTHROPIC_BASE_URL: baseUrl,
    ...(authToken && { ANTHROPIC_AUTH_TOKEN: authToken }),
    ...(apiKey && !authToken && { ANTHROPIC_API_KEY: apiKey }),
    HOME: process.env.HOME || '',
    PATH: process.env.PATH || '',
  };

  // 5. Print header
  printHeader(baseUrl, authSource, effectiveToken, PERMISSION_MODE, configSource);

  // 6. Test each model tier sequentially
  const results: TestResult[] = [];
  for (const tier of MODEL_TIERS) {
    const result = await testModel(tier, models[tier], sdkEnv);
    results.push(result);
  }

  // 7. Summary
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
