import { query } from '@anthropic-ai/claude-agent-sdk';

const q = query({
  prompt: 'Say hello in 5 words',
  options: {
    model: 'claude-sonnet-4-6',
    maxTurns: 1,
    cwd: '/mnt/d/code/node_futunn_nnq',
    permissionMode: 'bypassPermissions',
    allowDangerouslySkipPermissions: true,
    env: {
      ANTHROPIC_BASE_URL: process.env.ANTHROPIC_BASE_URL,
      ANTHROPIC_AUTH_TOKEN: process.env.ANTHROPIC_AUTH_TOKEN,
      PATH: process.env.PATH,
      HOME: process.env.HOME,
      CLAUDE_CODE_MAX_OUTPUT_TOKENS: '64000',
    },
  },
});

for await (const msg of q) {
  console.log('TYPE:', msg.type);
  if (msg.type === 'result') {
    console.log('RESULT:', msg.result?.slice(0, 200));
  } else if (msg.type === 'error') {
    console.log('ERROR:', JSON.stringify(msg).slice(0, 500));
  }
}
