import fs from 'node:fs/promises';
import path from 'node:path';
import { writeUserSettingsForCodePathAvoids } from '../ai/settings-writer.js';
import { AuditSession } from '../audit/index.js';
import type { SessionMetadata } from '../audit/utils.js';
import { deliverablesDir, WORKSPACES_DIR } from '../paths.js';
import { AgentExecutionService } from '../services/agent-execution.js';
import { ConfigLoaderService } from '../services/config-loader.js';
import { renderFindingsFromQueues } from '../services/findings-renderer.js';
import { executeGitCommandWithRetry } from '../services/git-manager.js';
import { runPreflightChecks } from '../services/preflight.js';
import { assembleFinalReport, injectModelIntoReport } from '../services/reporting.js';
import type { AgentName } from '../types/agents.js';
import { ALL_VULN_CLASSES, type DistributedConfig, type ProviderConfig } from '../types/config.js';
import { ConsoleActivityLogger } from './console-logger.js';
import { Semaphore } from './semaphore.js';

const MAX_RETRIES = 3;
const BASE_DELAY_MS = 30_000;
const MAX_DELAY_MS = 300_000;

const WHITEBOX_VULN_AGENTS = ALL_VULN_CLASSES.map(
  (cls) => `${cls}-vuln` as AgentName,
);

interface RunnerArgs {
  repoPath: string;
  configPath?: string;
  workspace?: string;
  concurrency: number;
  pipelineTestingMode: boolean;
  apiKey?: string;
  promptDir?: string;
  providerConfig?: ProviderConfig;
  sessionId?: string;
}

function parseArgs(argv: string[]): RunnerArgs {
  let repoPath = '';
  let configPath: string | undefined;
  let workspace: string | undefined;
  let concurrency = 3;
  let pipelineTestingMode = false;
  let apiKey: string | undefined;
  let promptDir: string | undefined;
  let sessionId: string | undefined;

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i] as string;
    const next = argv[i + 1];

    switch (arg) {
      case '--repo':
      case '-r':
        if (next) {
          repoPath = next;
          i++;
        }
        break;
      case '--config':
      case '-c':
        if (next) {
          configPath = next;
          i++;
        }
        break;
      case '--workspace':
      case '-w':
        if (next) {
          workspace = next;
          i++;
        }
        break;
      case '--concurrency':
        if (next) {
          concurrency = parseInt(next, 10);
          i++;
        }
        break;
      case '--pipeline-testing':
        pipelineTestingMode = true;
        break;
      case '--api-key':
        if (next) {
          apiKey = next;
          i++;
        }
        break;
      case '--prompt-dir':
        if (next) {
          promptDir = next;
          i++;
        }
        break;
      case '--session-id':
        if (next) {
          sessionId = next;
          i++;
        }
        break;
    }
  }

  if (!repoPath) {
    console.error('[ERROR] --repo is required');
    process.exit(1);
  }

  return {
    repoPath: path.resolve(repoPath),
    concurrency,
    pipelineTestingMode,
    ...(configPath && { configPath: path.resolve(configPath) }),
    ...(workspace && { workspace }),
    ...(apiKey && { apiKey }),
    ...(promptDir && { promptDir }),
    ...(sessionId && { sessionId }),
  };
}

async function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function getRetryDelay(attempt: number): number {
  return Math.min(BASE_DELAY_MS * 2 ** (attempt - 1), MAX_DELAY_MS);
}

interface AgentResult {
  agentName: AgentName;
  success: boolean;
  attempts: number;
  durationMs: number;
  costUsd: number;
  error?: string;
}

async function runAgentWithRetry(
  agentName: AgentName,
  args: RunnerArgs,
  auditSession: AuditSession,
  logger: ConsoleActivityLogger,
  configLoader: ConfigLoaderService,
  deliverablesPath: string,
  distributedConfig: DistributedConfig | null,
  attemptOffset: number = 0,
): Promise<AgentResult> {
  const agentService = new AgentExecutionService(configLoader);
  const startTime = Date.now();
  let lastError: string | undefined;
  let totalCost = 0;

  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    const globalAttempt = attempt + attemptOffset;

    logger.info(`[${agentName}] Attempt ${attempt}/${MAX_RETRIES} (global: ${globalAttempt})`);

    const result = await agentService.execute(
      agentName,
      {
        repoPath: args.repoPath,
        deliverablesPath,
        configPath: args.configPath,
        configData: distributedConfig ?? undefined,
        pipelineTestingMode: args.pipelineTestingMode,
        attemptNumber: globalAttempt,
        apiKey: args.apiKey,
        promptDir: args.promptDir,
        providerConfig: args.providerConfig,
        promptOverride: agentName === 'recon' ? 'recon-static' : undefined,
      },
      auditSession,
      logger,
    );

    if (result.ok) {
      totalCost += result.value.cost_usd;
      return {
        agentName,
        success: true,
        attempts: attempt,
        durationMs: Date.now() - startTime,
        costUsd: totalCost,
      };
    }

    const error = result.error;
    totalCost += (error.context?.cost_usd as number) ?? 0;
    lastError = error.message;

    if (!error.retryable) {
      logger.error(`[${agentName}] Non-retryable error: ${error.message}`);
      break;
    }

    if (attempt < MAX_RETRIES) {
      const delay = getRetryDelay(attempt);
      logger.warn(`[${agentName}] Retryable error (attempt ${attempt}): ${error.message}`);
      logger.info(`[${agentName}] Waiting ${delay / 1000}s before retry...`);
      await sleep(delay);
    }
  }

  return {
    agentName,
    success: false,
    attempts: MAX_RETRIES,
    durationMs: Date.now() - startTime,
    costUsd: totalCost,
    ...(lastError && { error: lastError }),
  };
}

async function initDeliverableGit(deliverablesPath: string): Promise<void> {
  await fs.mkdir(deliverablesPath, { recursive: true });

  const dotGitPath = path.join(deliverablesPath, '.git');
  try {
    await fs.stat(dotGitPath);
    return;
  } catch {
    // .git doesn't exist, proceed with init
  }

  await executeGitCommandWithRetry(['git', 'init'], deliverablesPath, 'init deliverables repo');
  await executeGitCommandWithRetry(
    ['git', 'commit', '--allow-empty', '-m', 'Initial deliverables checkpoint'],
    deliverablesPath,
    'initial checkpoint',
  );
}

async function syncCodePathDenyRules(
  args: RunnerArgs,
  configLoader: ConfigLoaderService,
  logger: ConsoleActivityLogger,
): Promise<void> {
  const configResult = await configLoader.loadOptional(args.configPath);
  if (!configResult.ok) {
    logger.warn(`syncCodePathDenyRules: skipping (config load failed: ${configResult.error.message})`);
    return;
  }
  const config = configResult.value;
  const denyCount = (config?.avoid ?? []).filter((r) => r.type === 'code_path').length;
  await writeUserSettingsForCodePathAvoids(config);
  logger.info(`Synced code_path deny rules to user settings (${denyCount} entries)`);
}

async function run(): Promise<void> {
  const args = parseArgs(process.argv.slice(2));
  const logger = new ConsoleActivityLogger();

  console.log('');
  console.log('  Shannon — Local Whitebox Runner');
  console.log(`  Repository:  ${args.repoPath}`);
  console.log(`  Concurrency: ${args.concurrency}`);
  if (args.configPath) {
    console.log(`  Config:      ${args.configPath}`);
  }
  console.log('');

  // 1. Preflight checks (skip target URL validation for whitebox)
  logger.info('Running preflight checks...');
  const preflightResult = await runPreflightChecks(
    undefined,
    args.repoPath,
    args.configPath,
    logger,
    false,
    args.apiKey,
    args.providerConfig,
  );
  if (!preflightResult.ok) {
    console.error(`[ERROR] Preflight failed: ${preflightResult.error.message}`);
    process.exit(1);
  }
  logger.info('Preflight checks passed');

  // 2. Load config for pipeline control
  const configLoader = new ConfigLoaderService();
  const configResult = await configLoader.loadOptional(args.configPath);
  if (!configResult.ok) {
    console.error(`[ERROR] Config load failed: ${configResult.error.message}`);
    process.exit(1);
  }
  const distributedConfig = configResult.value;

  // 3. Session and audit initialization
  // Use workspace name as session ID so ./shannon logs <workspace> can find it
  const workspaceName = args.workspace ?? `${path.basename(args.repoPath)}_whitebox-${Date.now()}`;
  const sessionId = workspaceName;

  const sessionMetadata: SessionMetadata = {
    id: sessionId,
    repoPath: args.repoPath,
    outputPath: WORKSPACES_DIR,
  };

  const auditSession = new AuditSession(sessionMetadata);
  await auditSession.initialize(sessionId);
  await auditSession.updateSessionStatus('in-progress');
  logger.info(`Session initialized: ${sessionId}`);
  logger.info(`Workspace: ${workspaceName}`);

  // 4. Initialize deliverables git repo
  const deliverablesPath = deliverablesDir(args.repoPath);
  logger.info('Initializing deliverables repository...');
  await initDeliverableGit(deliverablesPath);

  // 5. Sync code_path deny rules
  logger.info('Syncing code_path deny rules...');
  await syncCodePathDenyRules(args, configLoader, logger);

  // 6. Execute pipeline phases
  const pipelineStart = Date.now();
  const results: AgentResult[] = [];
  let aborted = false;

  const handleSignal = (): void => {
    if (aborted) return;
    aborted = true;
    logger.warn('Received shutdown signal, aborting...');
  };
  process.on('SIGINT', handleSignal);
  process.on('SIGTERM', handleSignal);

  try {
    // Phase 1: Pre-recon
    if (!aborted) {
      logger.info('=== Phase 1: Pre-recon ===');
      const result = await runAgentWithRetry(
        'pre-recon',
        args,
        auditSession,
        logger,
        configLoader,
        deliverablesPath,
        distributedConfig,
      );
      results.push(result);
      if (!result.success) {
        console.error(`[ERROR] Pre-recon agent failed after ${result.attempts} attempts: ${result.error}`);
        process.exit(1);
      }
    }

    // Phase 2: Recon (static)
    if (!aborted) {
      logger.info('=== Phase 2: Static Recon ===');
      const result = await runAgentWithRetry(
        'recon',
        args,
        auditSession,
        logger,
        configLoader,
        deliverablesPath,
        distributedConfig,
      );
      results.push(result);
      if (!result.success) {
        console.error(`[ERROR] Recon agent failed after ${result.attempts} attempts: ${result.error}`);
        process.exit(1);
      }
    }

    // Phase 3: Vulnerability analysis (bounded parallel)
    if (!aborted) {
      logger.info(`=== Phase 3: Vulnerability Analysis (concurrency=${args.concurrency}) ===`);
      const semaphore = new Semaphore(args.concurrency);

      const vulnPromises = WHITEBOX_VULN_AGENTS.map((agentName) =>
        semaphore.with(async () => {
          if (aborted) {
            return { agentName, success: false, attempts: 0, durationMs: 0, costUsd: 0, error: 'Aborted' };
          }
          // Each vuln agent needs its own AuditSession for parallel safety
          const vulnAuditSession = new AuditSession(sessionMetadata);
          await vulnAuditSession.initialize(sessionId);
          return runAgentWithRetry(
            agentName,
            args,
            vulnAuditSession,
            logger,
            configLoader,
            deliverablesPath,
            distributedConfig,
          );
        }),
      );

      const vulnResults = await Promise.all(vulnPromises);
      results.push(...vulnResults);
    }

    // Phase 4: Findings rendering (no exploit agents in whitebox mode)
    if (!aborted) {
      logger.info('=== Phase 4: Findings Rendering ===');
      try {
        await renderFindingsFromQueues(args.repoPath, undefined, logger);
      } catch (error) {
        logger.warn(`Findings rendering had issues: ${error instanceof Error ? error.message : String(error)}`);
      }

      logger.info('=== Phase 5: Report Assembly ===');
      try {
        await assembleFinalReport(args.repoPath, undefined, logger);
      } catch (error) {
        logger.warn(`Report assembly had issues: ${error instanceof Error ? error.message : String(error)}`);
      }

      try {
        await injectModelIntoReport(args.repoPath, undefined, path.join(WORKSPACES_DIR, sessionId), logger);
      } catch (error) {
        logger.warn(`Model injection had issues: ${error instanceof Error ? error.message : String(error)}`);
      }
    }
  } finally {
    process.removeListener('SIGINT', handleSignal);
    process.removeListener('SIGTERM', handleSignal);
  }

  // 7. Final summary
  const totalDuration = Date.now() - pipelineStart;
  const totalCost = results.reduce((sum, r) => sum + r.costUsd, 0);
  const succeeded = results.filter((r) => r.success).length;
  const failed = results.filter((r) => !r.success).length;

  await auditSession.updateSessionStatus(failed === 0 ? 'completed' : 'failed');

  console.log('');
  console.log('=== Pipeline Complete ===');
  console.log(`  Duration:     ${(totalDuration / 1000).toFixed(1)}s`);
  console.log(`  Total cost:   $${totalCost.toFixed(4)}`);
  console.log(`  Agents:       ${succeeded} succeeded, ${failed} failed`);
  for (const r of results) {
    const status = r.success ? 'OK' : 'FAILED';
    console.log(
      `    [${status}] ${r.agentName} (${(r.durationMs / 1000).toFixed(1)}s, $${r.costUsd.toFixed(4)}, ${r.attempts} attempt(s))`,
    );
    if (r.error) {
      console.log(`             Error: ${r.error.slice(0, 100)}`);
    }
  }
  console.log(`  Deliverables: ${deliverablesPath}`);
  console.log(`  Workspace:    ${path.join(WORKSPACES_DIR, sessionId)}`);
  console.log('');

  process.exit(failed > 0 ? 1 : 0);
}

run().catch((error) => {
  console.error('[FATAL]', error instanceof Error ? error.message : String(error));
  process.exit(1);
});
