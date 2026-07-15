// Copyright (C) 2025 Keygraph, Inc.
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License version 3
// as published by the Free Software Foundation.

import os from 'node:os';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { $, fs, path } from 'zx';
import type { AuditSession } from '../audit/index.js';
import type { ActivityLogger } from '../types/activity-logger.js';
import { ok } from '../types/result.js';
import type { ConfigLoaderService } from './config-loader.js';

// The agent itself is not under test. Stub the SDK executor so the agent
// "succeeds" deterministically and output validation always passes — we are
// verifying the git/checkpoint plumbing, not the LLM.
vi.mock('../ai/claude-executor.js', () => ({
  runClaudePrompt: vi.fn().mockResolvedValue({
    success: true,
    duration: 1,
    cost: 0,
    turns: 5,
    model: 'test-model',
    structuredOutput: { items: [] },
  }),
  validateAgentOutput: vi.fn().mockResolvedValue(true),
}));

vi.mock('./prompt-manager.js', () => ({
  loadPrompt: vi.fn().mockResolvedValue('test-prompt'),
}));

const noopLogger: ActivityLogger = {
  info: () => undefined,
  warn: () => undefined,
  error: () => undefined,
};

describe('AgentExecutionService.execute — renderDeliverables hook', () => {
  let tmpDir: string;

  beforeEach(async () => {
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'shannon-exec-'));
    // Bare repo with an initial commit so checkpoint/success commits have a HEAD.
    await $`cd ${tmpDir} && git init -q`;
    await $`cd ${tmpDir} && git config user.email t@t.t && git config user.name t`;
    await $`cd ${tmpDir} && git commit --allow-empty -m init -q`;
  });

  afterEach(async () => {
    await fs.remove(tmpDir);
  });

  it('commits files written by renderDeliverables into the success checkpoint', async () => {
    const { AgentExecutionService } = await import('./agent-execution.js');

    const configLoader = { loadOptional: vi.fn().mockResolvedValue(ok(undefined)) } as unknown as ConfigLoaderService;
    const service = new AgentExecutionService(configLoader);

    const auditSession = {
      sessionMetadata: { id: 'test-session', repoPath: tmpDir },
      startAgent: vi.fn().mockResolvedValue(undefined),
      endAgent: vi.fn().mockResolvedValue(undefined),
    } as unknown as AuditSession;

    // renderDeliverables writes the agent's analysis markdown. Under the
    // pre-fix ordering it runs AFTER commitGitSuccess, so the file stays
    // untracked and never reaches the checkpoint commit.
    const renderDeliverables = vi.fn(async (deliverablesPath: string) => {
      await fs.writeFile(path.join(deliverablesPath, 'rendered.md'), '# rendered deliverable\n');
    });

    const result = await service.execute(
      'auth-vuln',
      {
        repoPath: tmpDir,
        deliverablesPath: tmpDir,
        attemptNumber: 1,
        renderDeliverables,
      },
      auditSession,
      noopLogger,
    );

    expect(result.ok).toBe(true);
    expect(renderDeliverables).toHaveBeenCalledTimes(1);

    // The rendered deliverable must land in the checkpoint commit (HEAD).
    const headFiles = (await $`cd ${tmpDir} && git show --name-only --pretty=format: HEAD`).stdout
      .trim()
      .split('\n')
      .filter(Boolean);
    expect(headFiles).toContain('rendered.md');
  });
});
