import { query } from '@anthropic-ai/claude-agent-sdk';
import path from 'node:path';

const repoPath = path.resolve('repos/lollms-webui');
console.log(`Testing SDK against: ${repoPath}`);

try {
  for await (const msg of query({
    prompt: 'Say hello in one word.',
    options: {
      model: 'claude-haiku-4-5-20251001',
      maxTurns: 1,
      cwd: repoPath,
      permissionMode: 'bypassPermissions',
      allowDangerouslySkipPermissions: true,
    },
  })) {
    if (msg.type === 'assistant') {
      console.log(`[assistant] turn`);
    }
    if (msg.type === 'result') {
      console.log(`[result] subtype=${msg.subtype}`);
      if (msg.subtype === 'success') {
        console.log('SDK WORKS without Docker!');
      } else {
        console.log('Result error:', JSON.stringify(msg).slice(0, 500));
      }
    }
  }
} catch (e) {
  console.error('FAILED:', e.message);
  process.exit(1);
}
