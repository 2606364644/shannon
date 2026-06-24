#!/usr/bin/env node

// 人工登录 Futunn 并导出 playwright storageState,供 futunn-session.yaml 的预存 session 复用。
// passport.futunn.com 登录页有「拖动滑块完成拼图」的人机验证,自动化过不去;所以需要人工
// 在真实(headed)浏览器里登录一次,把登录态存下来,后续 Shannon 扫描直接 state-load 复用。
//
// 前置依赖(playwright 不在项目依赖里,需本机自行安装):
//   pnpm add -g playwright   或   npm i -g playwright
//   npx playwright install chromium
//   注:headed 模式需要图形环境 —— 本机直接跑,或 WSL2 下启用 WSLg。
//
// Usage:
//   npx tsx scripts/save-futunn-session.ts [output-path] [headless]
//
//   output-path  导出的 auth-state.json 路径,默认见下方 DEFAULT_OUT(与 futunn-session.yaml 一致)
//   headless     'true' / 'false',默认 'false'(必须 headed 才能手动过滑块)
//
// Exit codes: 0 = 成功导出,1 = 登录超时/校验失败,2 = 依赖/参数错误

import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';

// ╔══════════════════════════════════════════════════════════════════╗
// ║  默认输出路径 —— 与 apps/worker/configs/futunn-session.yaml 一致  ║
// ║  (容器内 repo 路径 /root/code/task_center,挂载点同名)            ║
// ╚══════════════════════════════════════════════════════════════════╝
const DEFAULT_OUT = '/root/code/task_center/.shannon-preseed/auth-state.json';
const LOGIN_URL = 'https://passport.futunn.com/?target=https%3A%2F%2Fmobile.futunn.com%2F&type=login&lang=zh-hk';
const LOGIN_TIMEOUT_MS = 600_000; // 10 分钟,留给手动登录 + 滑块
const POLL_INTERVAL_MS = 2_000;

// === Output Helpers ===

function info(msg: string): void {
  console.log(`ℹ️  ${msg}`);
}

function success(msg: string): void {
  console.log(`✅ ${msg}`);
}

function fail(msg: string): void {
  console.error(`❌ ${msg}`);
}

// === Login Detection ===

// 判断当前页面是否已落到已认证的 mobile.futunn.com(而非 passport 登录页)。
// 单纯靠 URL 不够稳(SPA 可能 hash 路由),所以同时要求有 futunn 域的 cookie。
async function isLoggedIn(
  page: { url: () => string },
  cookies: Array<{ domain: string; name: string }>,
): Promise<boolean> {
  const url = page.url();
  if (!url.includes('mobile.futunn.com') || url.includes('passport')) {
    return false;
  }
  return cookies.some((c) => c.domain.includes('futunn.com'));
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// === StorageState 校验 ===

// 与 validate-authentication.ts 的 verifySavedAuthState 一致:必须含 cookies 或 origins,
// 否则 Shannon 会判定「浏览器其实没登录」而拒绝。
function validateStateFile(raw: string, outPath: string): { cookieCount: number; originCount: number } {
  const parsed = JSON.parse(raw) as Record<string, unknown>;
  const cookieCount = Array.isArray(parsed.cookies) ? parsed.cookies.length : 0;
  const originCount = Array.isArray(parsed.origins) ? parsed.origins.length : 0;
  if (cookieCount === 0 && originCount === 0) {
    throw new Error(`${outPath} 里没有 cookies 或 origins —— 浏览器实际未登录,请重试`);
  }
  return { cookieCount, originCount };
}

// === Main ===

async function main(): Promise<number> {
  // 1. 解析参数
  const outPath = process.argv[2] ?? DEFAULT_OUT;
  const headless = (process.argv[3] ?? 'false').toLowerCase() === 'true';

  if (headless) {
    fail('headless=true 时无法手动完成滑块验证码,必须用 headed 模式');
    return 2;
  }

  info(`输出路径: ${outPath}`);

  // 2. 动态加载 playwright(非项目依赖,缺失时给清晰指引)
  let chromium: typeof import('playwright')['chromium'];
  try {
    const pw = await import('playwright');
    chromium = pw.chromium;
  } catch {
    fail('找不到 playwright 模块 —— 它不在项目依赖里,需本机单独安装:');
    console.error('   pnpm add -g playwright   或   npm i -g playwright');
    console.error('   npx playwright install chromium');
    return 2;
  }

  info('启动 headed Chromium(请稍候)...');

  // 3. 启动浏览器并打开登录页
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await context.newPage();

  try {
    await page.goto(LOGIN_URL, { waitUntil: 'domcontentloaded' });

    console.log();
    console.log('─────────────────────────────────────────────────────────');
    console.log('👉 在弹出的浏览器里手动登录 Futunn(账号 / 密码 / 滑块)');
    console.log('   登录成功落到 mobile.futunn.com 后,脚本会自动导出 session');
    console.log(`   超时上限 ${LOGIN_TIMEOUT_MS / 60_000} 分钟`);
    console.log('─────────────────────────────────────────────────────────');
    console.log();

    // 4. 轮询等待登录完成
    const deadline = Date.now() + LOGIN_TIMEOUT_MS;
    let loggedIn = false;
    while (Date.now() < deadline) {
      const cookies = await context.cookies();
      if (await isLoggedIn(page, cookies)) {
        loggedIn = true;
        break;
      }
      await sleep(POLL_INTERVAL_MS);
    }

    if (!loggedIn) {
      fail(`等待登录超时(${LOGIN_TIMEOUT_MS / 60_000} 分钟内未检测到落在 mobile.futunn.com)`);
      return 1;
    }

    // 5. 确保落地后再取一次稳定状态,导出 storageState
    await page.waitForLoadState('networkidle').catch(() => {
      /* networkidle 不一定达到,忽略 */
    });

    await mkdir(dirname(outPath), { recursive: true });
    await context.storageState({ path: outPath });

    // 6. 校验导出文件(与 Shannon 的 preflight 校验一致)
    const raw = await readFile(outPath, 'utf8');
    const { cookieCount, originCount } = validateStateFile(raw, outPath);
    await writeFile(outPath, raw, 'utf8');

    success(`已导出登录态到 ${outPath}`);
    console.log(`   cookies: ${cookieCount}, origins: ${originCount}`);
    console.log();
    info('下一步:用预存 session 配置启动扫描:');
    console.log('   ./shannon start -r /root/code/task_center/ -u https://mobile.futunn.com/ \\');
    console.log('     -w task_center_session -c apps/worker/configs/futunn-session.yaml');
    console.log();
    info('建议把 .shannon-preseed/ 加入 repo 的 .gitignore,避免提交登录态');
    return 0;
  } finally {
    await browser.close();
  }
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error('Fatal error:', err);
    process.exit(2);
  });
