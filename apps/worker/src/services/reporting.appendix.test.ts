import { describe, expect, it } from 'vitest';
import { fs, path } from 'zx';
import type { ActivityLogger } from '../types/activity-logger.js';
import { injectAffectedEndpointsAppendix } from './reporting.js';

const noopLogger: ActivityLogger = {
  info: () => undefined,
  warn: () => undefined,
  error: () => undefined,
};

describe('injectAffectedEndpointsAppendix', () => {
  it('appends appendix after existing report content, idempotent', async () => {
    const dir = await fs.mkdtemp(path.join(import.meta.dirname, '.tmp-'));
    try {
      // ground-truth queue
      await fs.writeJson(path.join(dir, 'authz_exploitation_queue.json'), {
        vulnerabilities: [
          {
            ID: 'AUTHZ-VULN-49',
            externally_exploitable: true,
            endpoint: 'GET /asset-analysis/stock-pos-preference',
            minimal_witness: '?account_id=<v>',
            vulnerable_code_location: 'match.ts:71',
          },
        ],
      });
      // existing assembled report
      const reportPath = path.join(dir, 'comprehensive_security_assessment_report.md');
      await fs.writeFile(reportPath, '# Security Assessment Report\n\n## Authorization Findings\n\n...summary...\n');

      await injectAffectedEndpointsAppendix(dir, '', noopLogger);
      let content = await fs.readFile(reportPath, 'utf8');
      expect(content).toContain('## 附录 A:完整可利用端点清单');
      expect(content).toContain('AUTHZ-VULN-49');
      // original content preserved before appendix
      expect(content.indexOf('## Authorization Findings')).toBeLessThan(content.indexOf('## 附录 A'));

      // idempotent: second run does not duplicate
      await injectAffectedEndpointsAppendix(dir, '', noopLogger);
      content = await fs.readFile(reportPath, 'utf8');
      expect(content.match(/## 附录 A:完整可利用端点清单/g)?.length).toBe(1);
    } finally {
      await fs.remove(dir);
    }
  });

  it('skips injection when no exploitable entries', async () => {
    const dir = await fs.mkdtemp(path.join(import.meta.dirname, '.tmp-'));
    try {
      const reportPath = path.join(dir, 'comprehensive_security_assessment_report.md');
      await fs.writeFile(reportPath, '# Report\n');
      await injectAffectedEndpointsAppendix(dir, '', noopLogger);
      const content = await fs.readFile(reportPath, 'utf8');
      expect(content).not.toContain('附录 A');
    } finally {
      await fs.remove(dir);
    }
  });
});
