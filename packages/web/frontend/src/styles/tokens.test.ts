import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const tokens = readFileSync(resolve(__dirname, "tokens.css"), "utf8");
const cfg = readFileSync(resolve(__dirname, "../../tailwind.config.ts"), "utf8");

const SHADCN_TOKENS = [
  "--background", "--foreground", "--card", "--card-foreground",
  "--popover", "--popover-foreground", "--primary", "--primary-foreground",
  "--secondary", "--secondary-foreground", "--muted", "--muted-foreground",
  "--accent", "--accent-foreground", "--destructive", "--destructive-foreground",
  "--border", "--input", "--ring",
];
const SEMANTIC = ["--c-cyan", "--c-magenta", "--c-green", "--c-red", "--c-orange", "--c-yellow", "--c-amber"];

describe("tokens.css 漂移护栏", () => {
  it("含全部 shadcn token", () => {
    for (const t of SHADCN_TOKENS) expect(tokens, `missing ${t}`).toContain(t);
  });
  it("含全部语义色 token（--c- 前缀避开 events.css hex 同名变量）", () => {
    for (const t of SEMANTIC) expect(tokens, `missing ${t}`).toContain(t);
  });
  it("含 :root（深）与 .light（浅）两组", () => {
    expect(tokens).toMatch(/:root\s*\{/);
    expect(tokens).toMatch(/\.light\s*\{/);
  });
  it("radius = 12px", () => {
    expect(tokens).toContain("--radius: 12px;");
  });
  it("Plex 三族字体保留", () => {
    expect(tokens).toContain("IBM Plex Mono");
    expect(tokens).toContain("IBM Plex Sans");
    expect(tokens).toContain("IBM Plex Serif");
  });
});

describe("tailwind.config 消费 token", () => {
  it("darkMode = class", () => {
    expect(cfg).toMatch(/darkMode:\s*\["class"\]/);
  });
  it("colors 映射 shadcn token + 语义色（--c- 前缀）", () => {
    expect(cfg).toContain("hsl(var(--primary))");
    expect(cfg).toContain("hsl(var(--c-cyan) / <alpha-value>)");
  });
  it("fontFamily 注入 Plex", () => {
    expect(cfg).toContain("IBM Plex Mono");
  });
  it("plugins 含 typography + animate", () => {
    expect(cfg).toMatch(/@tailwindcss\/typography/);
    expect(cfg).toMatch(/tailwindcss-animate/);
  });
});

describe("amber 语义色（must_change 提醒用，对齐 --c-orange/yellow 暖梯度）", () => {
  it("tokens.css :root（深）定义 --c-amber（HSL channel）", () => {
    expect(tokens).toMatch(/:root[\s\S]*?--c-amber:\s*\d+\s+\d+%\s+\d+%/);
  });
  it("tokens.css .light（浅）定义 --c-amber（HSL channel）", () => {
    const lightBlock = tokens.match(/\.light\s*\{([\s\S]*?)\}/);
    expect(lightBlock, ".light 块应存在").not.toBeNull();
    expect(lightBlock![1]).toMatch(/--c-amber:\s*\d+\s+\d+%\s+\d+%/);
  });
  it("tailwind 映射 amber → hsl(var(--c-amber))，支持 alpha 修饰", () => {
    expect(cfg).toMatch(/amber:\s*"hsl\(var\(--c-amber\) \/ <alpha-value>\)"/);
  });
});

describe("扩展主题（2026-08-25 OpenDesign 六主题移植）", () => {
  // 断言均先提取主题块体（kami 式 [\s\S]*?\n\} 懒惰匹配止于本块闭合），
  // 再对块体断言——防止懒惰跨块匹配拿到后续主题的同名 token 掩盖本块笔误。

  it("sentry 块：紫黑双层表面 + Sentry 紫主色 + 玻璃浮层 token", () => {
    const m = tokens.match(/\.dark\.theme-sentry\s*\{([\s\S]*?)\n\}/);
    expect(m).not.toBeNull();
    const sentryBlock = m![1];
    expect(sentryBlock).toMatch(/--background:\s*258 40% 10%;/);
    expect(sentryBlock).toMatch(/--primary:\s*247 44% 56%;/);
    expect(sentryBlock).toMatch(/--radius:\s*8px;/);
    expect(sentryBlock).toMatch(/--backdrop-float:\s*blur\(18px\) saturate\(180%\);/);
    // 浮层有玻璃但卡片不做玻璃：不定义 --backdrop-card
    expect(sentryBlock).not.toContain("--backdrop-card");
    // severity hue 锁定
    expect(sentryBlock).toMatch(/--c-red:\s*5\s/);
    expect(sentryBlock).toMatch(/--c-orange:\s*24\s/);
    expect(sentryBlock).toMatch(/--c-yellow:\s*38\s/);
  });

  it("arc 块：深色半透玻璃表面 + coral 主色 + 磨砂三件套 + 环境光层", () => {
    const m = tokens.match(/\.dark\.theme-arc\s*\{([\s\S]*?)\n\}/);
    expect(m).not.toBeNull();
    const arcBlock = m![1];
    expect(arcBlock).toMatch(/--card:\s*240 10% 12% \/ 0\.6;/);
    expect(arcBlock).toMatch(/--primary:\s*15 60% 56%;/);
    expect(arcBlock).toMatch(/--radius:\s*16px;/);
    expect(arcBlock).toMatch(/--backdrop-card:\s*saturate\(180%\) blur\(24px\);/);
    expect(arcBlock).toMatch(/--backdrop-float:\s*saturate\(180%\) blur\(36px\);/);
    // 环境光层是独立选择器 .dark.theme-arc body，单独提取断言（勿并入块体）
    const mBody = tokens.match(/\.dark\.theme-arc body\s*\{([\s\S]*?)\n\}/);
    expect(mBody).not.toBeNull();
    const arcBody = mBody![1];
    expect(arcBody).toMatch(/background-attachment:\s*fixed;/);
  });

  it("mission 块：深空海军蓝 + 琥珀遥测主色 + 4px 硬朗圆角 + 深投影", () => {
    const m = tokens.match(/\.dark\.theme-mission\s*\{([\s\S]*?)\n\}/);
    expect(m).not.toBeNull();
    const missionBlock = m![1];
    expect(missionBlock).toMatch(/--background:\s*223 49% 8%;/);
    expect(missionBlock).toMatch(/--primary:\s*43 100% 50%;/);
    expect(missionBlock).toMatch(/--radius:\s*4px;/);
    expect(missionBlock).toMatch(/--c-cyan:\s*190 100% 50%;/);
    expect(missionBlock).toMatch(/--c-red:\s*5\s/);
    expect(missionBlock).toMatch(/--c-orange:\s*24\s/);
    expect(missionBlock).toMatch(/--c-yellow:\s*38\s/);
    // 禁玻璃：mission 不定义 --backdrop-*
    expect(missionBlock).not.toContain("--backdrop-");
  });

  it("github 块：蓝白精准 + 实色细线边框 + Primer 蓝", () => {
    const m = tokens.match(/\.light\.theme-github\s*\{([\s\S]*?)\n\}/);
    expect(m).not.toBeNull();
    const githubBlock = m![1];
    expect(githubBlock).toMatch(/--border:\s*210 18% 84%;/);
    expect(githubBlock).toMatch(/--primary:\s*212 92% 45%;/);
    expect(githubBlock).toMatch(/--radius:\s*6px;/);
    expect(githubBlock).toMatch(/--c-red:\s*5\s/);
    expect(githubBlock).toMatch(/--c-yellow:\s*38\s/);
    // 禁玻璃：github 不定义 --backdrop-*
    expect(githubBlock).not.toContain("--backdrop-");
  });

  it("notion 块：暖灰交替面 + 低语边框 + Notion 蓝 + 多层低透明阴影", () => {
    const m = tokens.match(/\.light\.theme-notion\s*\{([\s\S]*?)\n\}/);
    expect(m).not.toBeNull();
    const notionBlock = m![1];
    expect(notionBlock).toMatch(/--secondary:\s*30 10% 96%;/);
    expect(notionBlock).toMatch(/--border:\s*0 0% 0% \/ 0\.1;/);
    expect(notionBlock).toMatch(/--primary:\s*208 100% 44%;/);
    expect(notionBlock).toMatch(/--radius:\s*6px;/);
    expect(notionBlock).toMatch(/--c-orange:\s*24\s/);
    // 禁玻璃：notion 不定义 --backdrop-*
    expect(notionBlock).not.toContain("--backdrop-");
  });

  it("kami 块：羊皮纸底 + 墨蓝主色 + 衬线字体覆盖（唯一例外）+ whisper 阴影", () => {
    const m = tokens.match(/\.light\.theme-kami\s*\{([\s\S]*?)\n\}/);
    expect(m).not.toBeNull();
    const kamiBlock = m![1];
    expect(kamiBlock).toMatch(/--background:\s*53 29% 95%;/);
    expect(kamiBlock).toMatch(/--primary:\s*215 55% 24%;/);
    expect(kamiBlock).toMatch(/--radius:\s*6px;/);
    expect(kamiBlock).toMatch(/--font-sans:\s*"Charter", Georgia, Palatino, "Songti SC", "Source Han Serif SC", serif;/);
    expect(kamiBlock).toMatch(/--c-red:\s*5\s/);
    // 禁玻璃：kami 不定义 --backdrop-*
    expect(kamiBlock).not.toContain("--backdrop-");
  });
});
