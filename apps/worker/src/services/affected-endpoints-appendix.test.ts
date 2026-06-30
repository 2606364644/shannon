import { describe, expect, it } from 'vitest';
import { fs, path } from 'zx';
import type { ActivityLogger } from '../types/activity-logger.js';
import {
  buildAffectedEndpointsAppendix,
  collectExploitableEntries,
  renderAppendixMarkdown,
} from './affected-endpoints-appendix.js';

const noopLogger: ActivityLogger = {
  info: () => undefined,
  warn: () => undefined,
  error: () => undefined,
};

describe('renderAppendixMarkdown', () => {
  it('renders overview counts + per-class tables', () => {
    const md = renderAppendixMarkdown([
      {
        heading: 'Authorization',
        entries: [
          {
            id: 'AUTHZ-VULN-49',
            endpoint: 'GET /asset-analysis/stock-pos-preference',
            witness: '?account_id=<victim>&target_uid=<victim>',
            location: 'match.ts:71-94',
          },
          {
            id: 'AUTHZ-VULN-50',
            endpoint: 'GET /asset-analysis/option-pos-preference',
            witness: '?account_id=<victim>',
            location: 'match.ts:71-94',
          },
        ],
      },
    ]);
    expect(md).toContain('## 附录 A:完整可利用端点清单');
    expect(md).toContain('| 漏洞类 | 可利用端点数 |');
    expect(md).toContain('| Authorization | 2 |');
    expect(md).toContain('AUTHZ-VULN-49');
    expect(md).toContain('GET /asset-analysis/stock-pos-preference');
    expect(md).toContain('?account_id=<victim>&target_uid=<victim>');
  });

  it('escapes pipe characters in cells', () => {
    const md = renderAppendixMarkdown([
      { heading: 'Injection', entries: [{ id: 'INJ-1', endpoint: 'GET /x', witness: 'a|b', location: 's.ts:1' }] },
    ]);
    expect(md).toContain('a\\|b');
  });

  it('returns the "none found" message when empty', () => {
    const md = renderAppendixMarkdown([]);
    expect(md).toContain('未识别到 externally_exploitable');
  });
});

describe('collectExploitableEntries', () => {
  it('filters externally_exploitable=false and maps per-class fields', async () => {
    const dir = await fs.mkdtemp(path.join(import.meta.dirname, '.tmp-'));
    try {
      await fs.writeJson(path.join(dir, 'authz_exploitation_queue.json'), {
        vulnerabilities: [
          {
            ID: 'AUTHZ-VULN-49',
            externally_exploitable: true,
            endpoint: 'GET /a',
            minimal_witness: '?id=<v>',
            vulnerable_code_location: 'match.ts:71',
          },
          {
            ID: 'AUTHZ-VULN-99',
            externally_exploitable: false,
            endpoint: 'GET /b',
            minimal_witness: '?id=<v>',
            vulnerable_code_location: 'match.ts:71',
          },
        ],
      });
      await fs.writeJson(path.join(dir, 'injection_exploitation_queue.json'), {
        vulnerabilities: [
          { ID: 'INJ-1', externally_exploitable: true, path: 'GET /i', witness_payload: 'p=<v>', sink_call: 'eval' },
        ],
      });
      const classes = await collectExploitableEntries(dir, '', noopLogger);
      const authz = classes.find((c) => c.heading === 'Authorization');
      const inj = classes.find((c) => c.heading === 'Injection');
      expect(authz?.entries.map((e) => e.id)).toEqual(['AUTHZ-VULN-49']); // false 被过滤
      expect(inj?.entries[0]).toMatchObject({ id: 'INJ-1', endpoint: 'GET /i', witness: 'p=<v>', location: 'eval' });
    } finally {
      await fs.remove(dir);
    }
  });

  it('skips missing queue files (out-of-scope class)', async () => {
    const dir = await fs.mkdtemp(path.join(import.meta.dirname, '.tmp-'));
    try {
      const classes = await collectExploitableEntries(dir, '', noopLogger);
      expect(classes).toEqual([]);
    } finally {
      await fs.remove(dir);
    }
  });
});

describe('buildAffectedEndpointsAppendix', () => {
  it('returns null when no exploitable entries', async () => {
    const dir = await fs.mkdtemp(path.join(import.meta.dirname, '.tmp-'));
    try {
      const out = await buildAffectedEndpointsAppendix(dir, '', noopLogger);
      expect(out).toBeNull();
    } finally {
      await fs.remove(dir);
    }
  });
});
