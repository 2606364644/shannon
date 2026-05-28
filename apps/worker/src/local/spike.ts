import path from 'node:path';
import { query } from '@anthropic-ai/claude-agent-sdk';

const repoPath = process.argv[2];
if (!repoPath) {
  console.error('Usage: tsx spike.ts <repo-path>');
  process.exit(1);
}

const resolvedPath = path.resolve(repoPath);
console.log(`Running bare-metal spike against: ${resolvedPath}`);

try {
  let result = '';
  for await (const message of query({
    prompt: 'List the top-level directory structure and identify the primary language/framework. Keep it brief.',
    options: {
      model: 'claude-haiku-4-5-20251001',
      maxTurns: 3,
      cwd: resolvedPath,
      permissionMode: 'bypassPermissions',
      allowDangerouslySkipPermissions: true,
    },
  })) {
    console.log(`[msg] type=${message.type} subtype=${'subtype' in message ? message.subtype : 'n/a'}`);
    if (message.type === 'result') {
      const msg = message as { subtype: string; result?: string; error?: string };
      console.log(`[result] subtype=${msg.subtype}`);
      if (msg.subtype === 'success') {
        result = msg.result || '';
      } else {
        console.log(`[result error] ${JSON.stringify(msg).slice(0, 500)}`);
      }
    }
    if (message.type === 'assistant' && 'error' in message) {
      console.log(`[assistant error] ${JSON.stringify(message).slice(0, 500)}`);
    }
  }
  console.log('Spike output:', result.slice(0, 500));
  console.log('Spike passed: Claude Agent SDK works without Docker');
} catch (error) {
  console.error('Spike failed:', error instanceof Error ? error.message : String(error));
  process.exit(1);
}
