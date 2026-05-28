import { execFileSync, fork } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { ensureImage, randomSuffix } from '../docker.js';
import { buildEnvFlags, loadEnv, validateCredentials } from '../env.js';
import { getWorkspacesDir, initHome } from '../home.js';
import { isLocal } from '../mode.js';
import { resolveConfig, resolveRepo } from '../paths.js';

export interface LocalStartArgs {
  url?: string;
  repo: string;
  config?: string;
  workspace?: string;
  output?: string;
  pipelineTesting: boolean;
  debug: boolean;
  version: string;
  whiteboxOnly: boolean;
  blackboxOnly: boolean;
}

function parseConcurrency(): number | undefined {
  const argv = process.argv;
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--concurrency' && argv[i + 1]) {
      return parseInt(argv[i + 1] as string, 10);
    }
  }
  return undefined;
}

function ensureScriptWrappers(): string {
  const scriptDistDir = path.resolve('apps/worker/dist/scripts');
  const wrappersDir = path.join(os.tmpdir(), 'shannon-scripts');
  fs.mkdirSync(wrappersDir, { recursive: true });

  const scripts = ['save-deliverable', 'generate-totp'];
  for (const name of scripts) {
    const jsPath = path.join(scriptDistDir, `${name}.js`);
    const wrapperPath = path.join(wrappersDir, name);
    const content = `#!/bin/sh\nexec node "${jsPath}" "$@"\n`;
    fs.writeFileSync(wrapperPath, content, { mode: 0o755 });
  }

  return wrappersDir;
}

export async function localStart(args: LocalStartArgs): Promise<void> {
  // 1. Initialize state directories and load env
  initHome();
  loadEnv();

  // 2. Validate credentials
  const creds = validateCredentials();
  if (!creds.valid) {
    console.error(`ERROR: ${creds.error}`);
    process.exit(1);
  }

  // 3. Parse --concurrency from raw CLI args (not parsed by parseStartArgs in index.ts)
  const concurrency = parseConcurrency() ?? 3;

  if (isLocal()) {
    await localStartBare(args, concurrency);
  } else {
    await localStartNpx(args, concurrency);
  }
}

async function localStartBare(args: LocalStartArgs, concurrency: number): Promise<void> {
  const repo = resolveRepo(args.repo);
  const runnerDistPath = path.resolve('apps/worker/dist/local/runner.js');

  if (!fs.existsSync(runnerDistPath)) {
    console.error('ERROR: Worker not compiled. Run `pnpm run build` first.');
    process.exit(1);
  }

  const scriptDistDir = path.resolve('apps/worker/dist/scripts');
  const saveDeliverableJs = path.join(scriptDistDir, 'save-deliverable.js');
  if (!fs.existsSync(saveDeliverableJs)) {
    console.error('ERROR: CLI scripts not compiled. Run `pnpm run build` first.');
    process.exit(1);
  }

  const workspacesDir = getWorkspacesDir();
  fs.mkdirSync(workspacesDir, { recursive: true });

  const workspace = args.workspace ?? `${path.basename(repo.hostPath)}_whitebox-${Date.now()}`;

  console.log('');
  console.log('  Shannon — Local Whitebox Scan');
  console.log(`  Repository:  ${repo.hostPath}`);
  console.log(`  Workspace:   ${workspace}`);
  console.log(`  Concurrency: ${concurrency}`);
  if (args.config) {
    console.log(`  Config:      ${path.resolve(args.config)}`);
  }
  console.log('');

  // Build runner arguments
  const runnerArgs = ['--repo', repo.hostPath, '--workspace', workspace, '--concurrency', String(concurrency)];
  if (args.config) {
    const configResolved = resolveConfig(args.config);
    runnerArgs.push('--config', configResolved.hostPath);
  }
  if (args.pipelineTesting) {
    runnerArgs.push('--pipeline-testing');
  }

  const wrappersDir = ensureScriptWrappers();

  const proc = fork(runnerDistPath, runnerArgs, {
    stdio: 'inherit',
    env: {
      ...process.env,
      SHANNON_LOCAL: '1',
      PATH: `${wrappersDir}${path.delimiter}${process.env.PATH}`,
    },
  });

  const exitCode = await new Promise<number>((resolve) => {
    proc.once('exit', (code) => resolve(code ?? 1));
    proc.once('error', (err) => {
      console.error(`Failed to start runner: ${err.message}`);
      resolve(1);
    });
  });

  process.exit(exitCode);
}

async function localStartNpx(args: LocalStartArgs, concurrency: number): Promise<void> {
  const repo = resolveRepo(args.repo);
  ensureImage(args.version);

  const workspacesDir = getWorkspacesDir();
  fs.mkdirSync(workspacesDir, { recursive: true });
  fs.chmodSync(workspacesDir, 0o777);

  const suffix = randomSuffix();
  const containerName = `shannon-worker-${suffix}`;
  const workspace = args.workspace ?? `${path.basename(repo.hostPath)}_whitebox-${Date.now()}`;

  const workspacePath = path.join(workspacesDir, workspace);
  fs.mkdirSync(workspacePath, { recursive: true });
  fs.chmodSync(workspacePath, 0o777);
  for (const dir of ['deliverables', 'scratchpad', '.playwright-cli', '.playwright']) {
    const dirPath = path.join(workspacePath, dir);
    fs.mkdirSync(dirPath, { recursive: true });
    fs.chmodSync(dirPath, 0o777);
  }

  const shannonDir = path.join(repo.hostPath, '.shannon');
  for (const dir of ['deliverables', 'scratchpad', '.playwright-cli']) {
    fs.mkdirSync(path.join(shannonDir, dir), { recursive: true });
  }
  fs.mkdirSync(path.join(repo.hostPath, '.playwright'), { recursive: true });

  console.log('');
  console.log('  Shannon — Whitebox Scan (Docker, no Temporal)');
  console.log(`  Repository:  ${repo.hostPath}`);
  console.log(`  Workspace:   ${workspace}`);
  console.log(`  Concurrency: ${concurrency}`);
  if (args.config) {
    console.log(`  Config:      ${path.resolve(args.config)}`);
  }
  console.log('');

  // Build docker run args — no Temporal network needed
  const dockerArgs = ['run', '--rm', '--name', containerName];

  if (os.platform() === 'linux' && process.getuid && process.getgid) {
    dockerArgs.push('-e', `SHANNON_HOST_UID=${process.getuid()}`, '-e', `SHANNON_HOST_GID=${process.getgid()}`);
  }

  dockerArgs.push('-v', `${workspacesDir}:/app/workspaces`);
  dockerArgs.push('-v', `${repo.hostPath}:${repo.containerPath}:ro`);

  dockerArgs.push('-v', `${path.join(workspacePath, 'deliverables')}:${repo.containerPath}/.shannon/deliverables`);
  dockerArgs.push('-v', `${path.join(workspacePath, 'scratchpad')}:${repo.containerPath}/.shannon/scratchpad`);
  dockerArgs.push(
    '-v',
    `${path.join(workspacePath, '.playwright-cli')}:${repo.containerPath}/.shannon/.playwright-cli`,
  );
  dockerArgs.push('-v', `${path.join(workspacePath, '.playwright')}:${repo.containerPath}/.playwright`);

  if (args.config) {
    const configResolved = resolveConfig(args.config);
    dockerArgs.push('-v', `${configResolved.hostPath}:${configResolved.containerPath}:ro`);
  }

  dockerArgs.push(...buildEnvFlags());

  dockerArgs.push('--shm-size', '2gb', '--security-opt', 'seccomp=unconfined');

  const image = `keygraph/shannon:${args.version}`;
  dockerArgs.push(image);

  // Run local runner instead of Temporal worker
  dockerArgs.push('node', 'apps/worker/dist/local/runner.js');
  dockerArgs.push('--repo', repo.containerPath);
  dockerArgs.push('--concurrency', String(concurrency));
  dockerArgs.push('--workspace', workspace);

  if (args.config) {
    const configResolved = resolveConfig(args.config);
    dockerArgs.push('--config', configResolved.containerPath);
  }
  if (args.pipelineTesting) {
    dockerArgs.push('--pipeline-testing');
  }

  try {
    execFileSync('docker', dockerArgs, { stdio: 'inherit' });
    process.exit(0);
  } catch (error) {
    const exitCode = (error as { status?: number }).status ?? 1;
    process.exit(exitCode);
  }
}
