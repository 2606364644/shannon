import { describe, expect, it } from 'vitest';
import { path } from 'zx';
import type { ActivityLogger } from '../types/activity-logger.js';
import { collectExploitableEntries, renderAppendixMarkdown } from './affected-endpoints-appendix.js';

const noopLogger: ActivityLogger = {
  info: () => undefined,
  warn: () => undefined,
  error: () => undefined,
};

// Real deliverable from a completed whitebox run of paper_trading_frontend.
const REAL_DELIVERABLES = path.resolve(
  import.meta.dirname,
  '../../../../workspaces/paper_trading_frontend_whitebox-1782723841267-deliverables/deliverables',
);

describe('affected-endpoints appendix (real authz queue)', () => {
  it('includes all 91 authz endpoints and the stock-pos-preference finding', async () => {
    const classes = await collectExploitableEntries(REAL_DELIVERABLES, '', noopLogger);
    const authz = classes.find((c) => c.heading === 'Authorization');
    expect(authz).toBeDefined();
    expect(authz?.entries.length).toBe(91);
    const md = renderAppendixMarkdown(classes);
    expect(md).toContain('AUTHZ-VULN-49');
    expect(md).toContain('GET /asset-analysis/stock-pos-preference');
    expect(md).toContain('| Authorization | 91 |');
  });
});
