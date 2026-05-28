#!/bin/bash
set -e
cd /root/shannon
source .env
export ANTHROPIC_API_KEY

echo "Testing claude CLI directly..."
claude --model claude-haiku-4-5-20251001 --max-turns 1 --permission-mode bypassPermissions -p 'Say hi' 2>&1 | head -20
echo "---"
echo "CLI test done, exit=$?"

echo ""
echo "Testing via SDK..."
npx tsx apps/worker/src/local/spike.ts repos/lollms-webui 2>&1
