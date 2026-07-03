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
const SEMANTIC = ["--c-cyan", "--c-magenta", "--c-green", "--c-red", "--c-yellow"];

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
  it("radius = 3px（operator 克制约束）", () => {
    expect(tokens).toContain("--radius: 3px;");
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
