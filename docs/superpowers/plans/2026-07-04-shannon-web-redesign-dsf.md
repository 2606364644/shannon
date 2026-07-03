# Shannon Web 前端重设计 · 子项目 1（DSF 设计系统地基）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 shannon web 前端打设计系统地基——双主题 token 层 + Tailwind/shadcn 装配 + 全局 Layout（TopBar/AppShell/ThemeToggle）+ 基础组件库 + dev 预览页，让后续子项目（列表/扫描/详情/Dashboard/Settings）能在统一组件库上增量重做。

**Architecture:** 在现有 React18+Vite SPA 上引入 Tailwind 3（darkMode class strategy）+ shadcn/ui（Radix+Tailwind，源码 copy 进项目）。CSS 变量三层（shadcn token / 语义色 / 字体圆角），深色 `:root`、浅色 `.light`。`<AppShell>` 作 router 根 layout 全局套 TopBar。**增量迁移**：不动现有业务页内部（列表/扫描/详情 5 tab 内部样式保留旧 `events.css`），DSF 只加外壳 + 组件库就绪。

**Tech Stack:** React 18.3 · Vite 5 · TS 5.5 · react-router-dom 6.26 · Tailwind 3.4 · shadcn/ui (Radix) · vitest 2 + @testing-library/react 16 + jsdom · lucide-react · class-variance-authority 0.7 · clsx 2 · tailwind-merge 2 · tailwindcss-animate · @tailwindcss/typography 0.5 · postcss · autoprefixer

## Global Constraints

- **保留 IBM Plex 三族字体**：`index.html` 已 preconnect + load Google Fonts（Mono/Sans/Serif），不动；Tailwind `fontFamily` 注入覆盖 shadcn 默认。
- **保留语义色跨媒介不变量**：`cyan / magenta / green / red / yellow` 深（`:root`）/ 浅（`.light`）各一组，HSL channel 写法；事件专用 `.ev-*` class 保留独立、不并入 shadcn token。
- **旧 `events.css` 保留（迁移期）**：文件头标 `@deprecated`；新组件**禁向其追加规则**；旧 class 名（`.page / .ledger / .form-area`…）不重用于新组件。
- **不动业务页内部**：WorkspaceListPage / ScanNewPage / WorkspaceDetail 5 tab 内部样式保留；`dashboardReducer` / `useEventSource` / `api/client` 逻辑零改。
- **Tailwind `darkMode: ["class"]`；`--radius: 3px`**（operator 克制）。
- **新组件用 `cn()` 合并 className**（`@/lib/utils`）。
- **测试栈**：vitest（globals + jsdom）+ @testing-library/react，模式参考 `src/components/StatusBadge.test.tsx`（`render` + `screen`/`container` + jest-dom matcher）。所有新组件配单测。
- **路径别名 `@/ → src/`**：tsconfig `paths` + vite `resolve.alias`（Task 1 建立）。
- **preflight 顺序**：`index.css` 须 `@tailwind base` 在前，`@import "./events.css"` 在后（旧 element 规则靠加载顺序胜，class 规则不受影响）。
- **防主题 FOUC**：`index.html` 内联脚本（main bundle 前）读 localStorage 写 `<html class>`。
- **shadcn 配置**：`style: "new-york"`、`baseColor: "neutral"`、`cssVariables: true`、`utils: "@/lib/utils"`、`ui: "@/components/ui"`（Task 2 `components.json`）。
- **工作目录**：所有前端命令在 `packages/web/frontend/` 下执行。

---

## File Structure

**Create:**
- `packages/web/frontend/src/lib/utils.ts` — `cn()` 工具（clsx + tailwind-merge）
- `packages/web/frontend/src/lib/theme.ts` — 主题库（`getInitialTheme` / `applyTheme` / `THEME_KEY`）
- `packages/web/frontend/tailwind.config.ts` — Tailwind 配置（darkMode class + content + theme.extend）
- `packages/web/frontend/postcss.config.js` — PostCSS（tailwindcss + autoprefixer）
- `packages/web/frontend/components.json` — shadcn 配置
- `packages/web/frontend/src/styles/index.css` — Tailwind entry（directives + imports + spinner keyframes）
- `packages/web/frontend/src/styles/tokens.css` — 三层 CSS 变量（shadcn token 深/浅 + 语义色 深/浅 + 字体圆角）
- `packages/web/frontend/src/components/ui/*` — shadcn copy 的组件（Task 5 由 CLI 生成）
- `packages/web/frontend/src/components/Empty.tsx` — 空态
- `packages/web/frontend/src/components/Spinner.tsx` — braille spinner（自包含 keyframes）
- `packages/web/frontend/src/components/vuln-badges.tsx` — 双轨徽章包装（MergeSourceBadge / ReachableBadge）
- `packages/web/frontend/src/components/layout/AppShell.tsx` — 根 layout（TopBar + main + Outlet）
- `packages/web/frontend/src/components/layout/TopBar.tsx` — 顶栏（字标 + 主导航 + ThemeToggle）
- `packages/web/frontend/src/components/layout/ThemeToggle.tsx` — 主题切换按钮
- `packages/web/frontend/src/pages/DevComponentsPage.tsx` — dev 预览页（dev-only）
- 各组件对应 `*.test.tsx` / `*.test.ts`

**Modify:**
- `packages/web/frontend/package.json` — 加依赖
- `packages/web/frontend/tsconfig.json` — 加 `baseUrl` + `paths`（`@/*`）
- `packages/web/frontend/vite.config.ts` — 加 `resolve.alias`（`@`）
- `packages/web/frontend/src/main.tsx` — import `./styles/events.css` → `./styles/index.css`
- `packages/web/frontend/src/test-setup.ts` — 加 jsdom `matchMedia` stub
- `packages/web/frontend/index.html` — 加防 FOUC 内联脚本
- `packages/web/frontend/src/router.tsx` — 根包 `<AppShell>` + `/dev/components`（dev-only）
- `packages/web/frontend/src/styles/events.css` — 文件头加 `@deprecated` 注释

---

## Task 1: 工程地基（依赖 + path alias + cn 工具）

**Files:**
- Modify: `packages/web/frontend/package.json`
- Modify: `packages/web/frontend/tsconfig.json`
- Modify: `packages/web/frontend/vite.config.ts`
- Create: `packages/web/frontend/src/lib/utils.ts`
- Test: `packages/web/frontend/src/lib/utils.test.ts`

**Interfaces:**
- Consumes: clsx, tailwind-merge（npm 包，本 task 装）
- Produces: `cn(...inputs: ClassValue[]): string`（路径 `@/lib/utils`，后续所有组件 + shadcn copy 组件消费）

- [ ] **Step 1: 装依赖**

Run（在 `packages/web/frontend/` 下）:
```bash
npm install -D tailwindcss@^3.4 postcss@^8.4 autoprefixer@^10.4 @tailwindcss/typography@^0.5 tailwindcss-animate@^1.0
npm install class-variance-authority@^0.7 clsx@^2.1 tailwind-merge@^2.5 lucide-react@^0.400
```
Expected: `package.json` dependencies / devDependencies 新增上述包；`node_modules` 安装成功。

- [ ] **Step 2: 加路径别名 tsconfig**

Modify `packages/web/frontend/tsconfig.json`，在 `compilerOptions` 内加 `baseUrl` + `paths`（紧接 `"rootDir": "src"` 后）:
```json
    "rootDir": "src",
    "baseUrl": ".",
    "paths": {
      "@/*": ["src/*"]
    }
```

- [ ] **Step 3: 加路径别名 vite**

Modify `packages/web/frontend/vite.config.ts`，替换全文为:
```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:7878",
        changeOrigin: true,
        ws: false,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    globals: true,
  },
});
```

- [ ] **Step 4: 写失败测试 `src/lib/utils.test.ts`**

Create `packages/web/frontend/src/lib/utils.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { cn } from "@/lib/utils";

describe("cn()", () => {
  it("合并多个 class", () => {
    expect(cn("a", "b")).toBe("a b");
  });
  it("过滤 false / undefined / null", () => {
    expect(cn("a", false, undefined, null, "b")).toBe("a b");
  });
  it("tailwind-merge 解冲突（后胜）", () => {
    expect(cn("p-1", "p-2")).toBe("p-2");
  });
  it("条件对象", () => {
    expect(cn("a", { b: true, c: false })).toBe("a b");
  });
});
```

- [ ] **Step 5: 跑测试验证失败**

Run: `npx vitest run src/lib/utils.test.ts`
Expected: FAIL（`cn` 未导出 / 模块找不到 `@/lib/utils`）。

- [ ] **Step 6: 写实现 `src/lib/utils.ts`**

Create `packages/web/frontend/src/lib/utils.ts`:
```ts
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
```

- [ ] **Step 7: 跑测试验证通过**

Run: `npx vitest run src/lib/utils.test.ts`
Expected: PASS（4 用例全绿）。

- [ ] **Step 8: 验证 alias 生效（跑现有测试不破）**

Run: `npx vitest run`
Expected: 现有测试全绿（alias 改动不影响相对 import；若有红，回查 vite.config `test` 块保留）。

- [ ] **Step 9: Commit**

```bash
git add packages/web/frontend/package.json packages/web/frontend/package-lock.json packages/web/frontend/tsconfig.json packages/web/frontend/vite.config.ts packages/web/frontend/src/lib/utils.ts packages/web/frontend/src/lib/utils.test.ts
git commit -m "feat(web): DSF Task1 工程地基（依赖 + @ alias + cn 工具）"
```

---

## Task 2: Tailwind 装配（基线 config + postcss + entry CSS + shadcn 配置）

**Files:**
- Create: `packages/web/frontend/tailwind.config.ts`
- Create: `packages/web/frontend/postcss.config.js`
- Create: `packages/web/frontend/components.json`
- Create: `packages/web/frontend/src/styles/index.css`
- Modify: `packages/web/frontend/src/main.tsx`
- Modify: `packages/web/frontend/src/styles/events.css`（头加 `@deprecated`）

**Interfaces:**
- Consumes: tailwindcss / postcss / autoprefixer（Task 1 装的 devDeps）
- Produces: Tailwind 编译管线就绪；`src/styles/index.css` 为新 entry（含 `@tailwind base/components/utilities` + events.css import 占位）；`components.json` 供 Task 5 shadcn add 读。

- [ ] **Step 1: 创建 `tailwind.config.ts`（基线，theme.extend 在 Task 3 填）**

Create `packages/web/frontend/tailwind.config.ts`:
```ts
import type { Config } from "tailwindcss";

export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 1px)",
        sm: "calc(var(--radius) - 2px)",
      },
    },
  },
  plugins: [],
} satisfies Config;
```

- [ ] **Step 2: 创建 `postcss.config.js`**

Create `packages/web/frontend/postcss.config.js`:
```js
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
```

- [ ] **Step 3: 创建 `components.json`（shadcn 配置，Task 5 add 用）**

Create `packages/web/frontend/components.json`:
```json
{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "new-york",
  "rsc": false,
  "tsx": true,
  "tailwind": {
    "config": "tailwind.config.ts",
    "css": "src/styles/index.css",
    "baseColor": "neutral",
    "cssVariables": true,
    "prefix": ""
  },
  "aliases": {
    "components": "@/components",
    "utils": "@/lib/utils",
    "ui": "@/components/ui",
    "lib": "@/lib",
    "hooks": "@/hooks"
  }
}
```

- [ ] **Step 4: 创建 `src/styles/index.css`（Tailwind entry）**

Create `packages/web/frontend/src/styles/index.css`:
```css
/* DSF entry：Tailwind directives + 旧 events.css（迁移期，preflight 之后加载）+ tokens（Task 3 追加 @import）。*/
@tailwind base;
@tailwind components;
@tailwind utilities;
@import "./events.css";
@import "./tokens.css";
```

> 注：`tokens.css` 在 Task 3 创建；本 task 先 `@import`（构建会因文件缺失报警，Step 7 验证前先建空 `src/styles/tokens.css` 占位：`/* Task 3 填充 */`）。

- [ ] **Step 5: 创建占位 `src/styles/tokens.css`（Task 3 填充）**

Create `packages/web/frontend/src/styles/tokens.css`:
```css
/* Task 3 填充三层 token。*/
```

- [ ] **Step 6: 改 `src/main.tsx` import entry**

Modify `packages/web/frontend/src/main.tsx`，把 `import "./styles/events.css";` 改为:
```ts
import "./styles/index.css";
```

- [ ] **Step 7: 旧 `events.css` 头加 `@deprecated` 注释**

Modify `packages/web/frontend/src/styles/events.css`，在文件**第 1 行**前加:
```css
/* @deprecated 迁移期保留——子项目 2-5 逐页消化后清除。新组件禁向此文件追加规则。*/
```

- [ ] **Step 8: 验证构建 + 测试不破**

Run:
```bash
npx vite build
npx vitest run
```
Expected: `vite build` 成功（dist 产出，无 Tailwind 编译错）；现有测试全绿。

- [ ] **Step 9: Commit**

```bash
git add packages/web/frontend/tailwind.config.ts packages/web/frontend/postcss.config.js packages/web/frontend/components.json packages/web/frontend/src/styles/index.css packages/web/frontend/src/styles/tokens.css packages/web/frontend/src/main.tsx packages/web/frontend/src/styles/events.css
git commit -m "feat(web): DSF Task2 Tailwind 装配（基线 config + postcss + entry CSS + shadcn 配置）"
```

---

## Task 3: tokens 三层（shadcn token 深/浅 + 语义色 + 字体圆角）+ 漂移断言

**Files:**
- Modify: `packages/web/frontend/src/styles/tokens.css`（Task 2 占位 → 全文替换）
- Modify: `packages/web/frontend/tailwind.config.ts`（填 theme.extend colors + fontFamily + plugins）
- Test: `packages/web/frontend/src/styles/tokens.test.ts`

**Interfaces:**
- Consumes: 无（CSS 变量是根）
- Produces: 全部 shadcn token（`--background … --ring`）+ 语义色（`--cyan --magenta --green --red --yellow`）深/浅两组 + 字体/圆角变量；Tailwind `colors` / `fontFamily` 映射这些变量。后续所有组件通过 `bg-background` / `text-primary` / `text-cyan` 等 utility 消费。

- [ ] **Step 1: 写失败测试 `src/styles/tokens.test.ts`**

Create `packages/web/frontend/src/styles/tokens.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const tokens = readFileSync(resolve(__dirname, "../src/styles/tokens.css"), "utf8");
const cfg = readFileSync(resolve(__dirname, "../../tailwind.config.ts"), "utf8");

const SHADCN_TOKENS = [
  "--background", "--foreground", "--card", "--card-foreground",
  "--popover", "--popover-foreground", "--primary", "--primary-foreground",
  "--secondary", "--secondary-foreground", "--muted", "--muted-foreground",
  "--accent", "--accent-foreground", "--destructive", "--destructive-foreground",
  "--border", "--input", "--ring",
];
const SEMANTIC = ["--cyan", "--magenta", "--green", "--red", "--yellow"];

describe("tokens.css 漂移护栏", () => {
  it("含全部 shadcn token", () => {
    for (const t of SHADCN_TOKENS) expect(tokens, `missing ${t}`).toContain(t);
  });
  it("含全部语义色 token", () => {
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
  it("colors 映射 shadcn token + 语义色", () => {
    expect(cfg).toContain("hsl(var(--primary))");
    expect(cfg).toContain("hsl(var(--cyan))");
  });
  it("fontFamily 注入 Plex", () => {
    expect(cfg).toContain("IBM Plex Mono");
  });
  it("plugins 含 typography + animate", () => {
    expect(cfg).toMatch(/@tailwindcss\/typography/);
    expect(cfg).toMatch(/tailwindcss-animate/);
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `npx vitest run src/styles/tokens.test.ts`
Expected: FAIL（tokens.css 仅占位、tailwind.config 缺 colors/plugins）。

- [ ] **Step 3: 写 `src/styles/tokens.css` 全文**

Replace `packages/web/frontend/src/styles/tokens.css` 全文为:
```css
/* DSF tokens — 三层：shadcn token（A）+ 语义色（B）+ 字体/圆角（C）。
   深色 = :root 默认；浅色 = .light。HSL channel 写法供 hsl(var(--x)) 消费。
   hex 真值来源见 spec §1.2，AA 校验以 dev 预览页为准。 */

:root {
  /* 层 A · shadcn token（深） */
  --background: 213 33% 6%;
  --foreground: 213 22% 82%;
  --card: 217 26% 11%;
  --card-foreground: 213 22% 82%;
  --popover: 217 26% 11%;
  --popover-foreground: 213 22% 82%;
  --primary: 189 94% 53%;
  --primary-foreground: 213 33% 6%;
  --secondary: 217 26% 16%;
  --secondary-foreground: 213 22% 82%;
  --muted: 217 26% 16%;
  --muted-foreground: 213 12% 47%;
  --accent: 217 26% 16%;
  --accent-foreground: 213 22% 82%;
  --destructive: 0 92% 63%;
  --destructive-foreground: 213 33% 6%;
  --border: 217 26% 16%;
  --input: 217 26% 16%;
  --ring: 189 94% 53%;

  /* 层 B · 语义色（深，跨媒介不变量，对齐终端 STYLE_MAP） */
  --cyan: 189 94% 53%;
  --magenta: 270 100% 80%;
  --green: 137 55% 48%;
  --red: 0 92% 63%;
  --yellow: 40 75% 42%;

  /* 层 C · 字体 / 圆角 */
  --font-mono: "IBM Plex Mono", ui-monospace, monospace;
  --font-sans: "IBM Plex Sans", system-ui, sans-serif;
  --font-serif: "IBM Plex Serif", Georgia, serif;
  --radius: 3px;
}

.light {
  /* 层 A · shadcn token（浅） */
  --background: 0 0% 100%;
  --foreground: 217 26% 16%;
  --card: 0 0% 100%;
  --card-foreground: 217 26% 16%;
  --popover: 0 0% 100%;
  --popover-foreground: 217 26% 16%;
  --primary: 190 92% 36%;
  --primary-foreground: 0 0% 100%;
  --secondary: 220 30% 96%;
  --secondary-foreground: 217 26% 16%;
  --muted: 220 30% 96%;
  --muted-foreground: 213 12% 47%;
  --accent: 213 30% 92%;
  --accent-foreground: 217 26% 16%;
  --destructive: 0 73% 51%;
  --destructive-foreground: 0 0% 100%;
  --border: 213 25% 85%;
  --input: 213 25% 85%;
  --ring: 190 92% 36%;

  /* 层 B · 语义色（浅） */
  --cyan: 190 92% 36%;
  --magenta: 262 83% 58%;
  --green: 137 66% 30%;
  --red: 0 73% 51%;
  --yellow: 40 94% 36%;
}
```

- [ ] **Step 4: 填 `tailwind.config.ts` theme.extend + plugins 全文**

Replace `packages/web/frontend/tailwind.config.ts` 全文为:
```ts
import type { Config } from "tailwindcss";
import typography from "@tailwindcss/typography";
import animate from "tailwindcss-animate";

export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        mono: ['"IBM Plex Mono"', "ui-monospace", "monospace"],
        sans: ['"IBM Plex Sans"', "system-ui", "sans-serif"],
        serif: ['"IBM Plex Serif"', "Georgia", "serif"],
      },
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        secondary: { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--secondary-foreground))" },
        destructive: { DEFAULT: "hsl(var(--destructive))", foreground: "hsl(var(--destructive-foreground))" },
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        popover: { DEFAULT: "hsl(var(--popover))", foreground: "hsl(var(--popover-foreground))" },
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
        /* 语义色：支持 alpha 修饰（bg-cyan/40） */
        cyan: "hsl(var(--cyan) / <alpha-value>)",
        magenta: "hsl(var(--magenta) / <alpha-value>)",
        green: "hsl(var(--green) / <alpha-value>)",
        red: "hsl(var(--red) / <alpha-value>)",
        yellow: "hsl(var(--yellow) / <alpha-value>)",
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 1px)",
        sm: "calc(var(--radius) - 2px)",
      },
    },
  },
  plugins: [typography, animate],
} satisfies Config;
```

- [ ] **Step 5: 跑测试验证通过**

Run: `npx vitest run src/styles/tokens.test.ts`
Expected: PASS（9 用例全绿）。

- [ ] **Step 6: 跑全套测试 + 构建不破**

Run:
```bash
npx vite build
npx vitest run
```
Expected: build 成功；现有测试全绿。

- [ ] **Step 7: Commit**

```bash
git add packages/web/frontend/src/styles/tokens.css packages/web/frontend/src/styles/tokens.test.ts packages/web/frontend/tailwind.config.ts
git commit -m "feat(web): DSF Task3 tokens 三层（shadcn token+语义色 深/浅 + 字体圆角）+ 漂移断言"
```

---

## Task 4: 主题库（theme.ts）+ 防 FOUC + matchMedia stub

**Files:**
- Create: `packages/web/frontend/src/lib/theme.ts`
- Test: `packages/web/frontend/src/lib/theme.test.ts`
- Modify: `packages/web/frontend/index.html`（加防 FOUC 脚本）
- Modify: `packages/web/frontend/src/test-setup.ts`（加 matchMedia stub）

**Interfaces:**
- Consumes: 无（纯 DOM API）
- Produces:
  - `THEME_KEY = "shannon-theme"`
  - `type Theme = "dark" | "light"`
  - `getInitialTheme(): Theme`（localStorage 优先 → `prefers-color-scheme` → 默认 dark）
  - `applyTheme(t: Theme): void`（写 `<html>` class + localStorage）
- 后续 Task 8 ThemeToggle + index.html 防 FOUC 脚本消费。

- [ ] **Step 1: 扩 `src/test-setup.ts` 加 matchMedia stub**

Replace `packages/web/frontend/src/test-setup.ts` 全文为:
```ts
import "@testing-library/jest-dom/vitest";

// jsdom 缺 matchMedia，主题库 / 减少动效检测依赖它
if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList;
}
```

- [ ] **Step 2: 写失败测试 `src/lib/theme.test.ts`**

Create `packages/web/frontend/src/lib/theme.test.ts`:
```ts
import { describe, it, expect, beforeEach } from "vitest";
import { applyTheme, getInitialTheme, THEME_KEY } from "@/lib/theme";

describe("theme lib", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark", "light");
  });

  it("THEME_KEY = shannon-theme", () => {
    expect(THEME_KEY).toBe("shannon-theme");
  });

  it("getInitialTheme: localStorage 优先", () => {
    localStorage.setItem(THEME_KEY, "light");
    expect(getInitialTheme()).toBe("light");
  });

  it("getInitialTheme: 无 stored → 读 prefers-color-scheme（stub matches=false → dark）", () => {
    expect(getInitialTheme()).toBe("dark");
  });

  it("getInitialTheme: 非法 stored 值回退", () => {
    localStorage.setItem(THEME_KEY, "purple");
    expect(getInitialTheme()).toBe("dark");
  });

  it("applyTheme(light): 写 <html>.light + localStorage", () => {
    applyTheme("light");
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
  });

  it("applyTheme: 切换时清旧 class", () => {
    applyTheme("light");
    applyTheme("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.classList.contains("light")).toBe(false);
  });
});
```

- [ ] **Step 3: 跑测试验证失败**

Run: `npx vitest run src/lib/theme.test.ts`
Expected: FAIL（`@/lib/theme` 未导出）。

- [ ] **Step 4: 写实现 `src/lib/theme.ts`**

Create `packages/web/frontend/src/lib/theme.ts`:
```ts
export type Theme = "dark" | "light";
export const THEME_KEY = "shannon-theme";

export function getInitialTheme(): Theme {
  if (typeof window === "undefined") return "dark";
  const stored = localStorage.getItem(THEME_KEY);
  if (stored === "dark" || stored === "light") return stored;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function applyTheme(t: Theme): void {
  const root = document.documentElement;
  root.classList.remove("dark", "light");
  root.classList.add(t);
  localStorage.setItem(THEME_KEY, t);
}
```

- [ ] **Step 5: 跑测试验证通过**

Run: `npx vitest run src/lib/theme.test.ts`
Expected: PASS（6 用例全绿）。

- [ ] **Step 6: 加防 FOUC 脚本到 `index.html`**

Modify `packages/web/frontend/index.html`，在 `<head>` 内、`<title>` 后加:
```html
    <script>
      (function () {
        try {
          var t = localStorage.getItem("shannon-theme");
          if (t !== "dark" && t !== "light") {
            t = window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
          }
          document.documentElement.classList.add(t);
        } catch (e) {
          document.documentElement.classList.add("dark");
        }
      })();
    </script>
```

- [ ] **Step 7: 跑全套测试不破**

Run: `npx vitest run`
Expected: 全绿（含 theme 6 用例 + 现有）。

- [ ] **Step 8: Commit**

```bash
git add packages/web/frontend/src/lib/theme.ts packages/web/frontend/src/lib/theme.test.ts packages/web/frontend/src/test-setup.ts packages/web/frontend/index.html
git commit -m "feat(web): DSF Task4 主题库（getInitialTheme/applyTheme）+ 防 FOUC + matchMedia stub"
```

---

## Task 5: shadcn 首批组件 copy + 代表渲染测试

**Files:**
- Create: `packages/web/frontend/src/components/ui/{button,input,textarea,label,select,checkbox,switch,card,badge,dialog,tabs,tooltip,sonner,skeleton,table}.tsx`（CLI 生成）
- Create: `packages/web/frontend/src/hooks/use-toast.ts`（若 toast 需要；sonner 不需要）
- Test: `packages/web/frontend/src/components/ui/ui.test.tsx`

**Interfaces:**
- Consumes: `cn` from `@/lib/utils`（shadcn copy 组件内置 import）；Radix 各 primitive（CLI 自动 npm install）
- Produces: 一批 shadcn 组件 from `@/components/ui/*`——`Button / Input / Textarea / Label / Select / Checkbox / Switch / Card(*Carte) / Badge / Dialog(*) / Tabs(*) / Tooltip(*) / Toaster(Sonner) / Skeleton / Table(*)`（带 `*` 的含多个子组件）。后续 Task 6-9 + 子项目 2-5 消费。

> a11y 说明：shadcn 组件底层 Radix 已 WAI-ARIA；本 task 只做集成渲染测试（Button 变体代表 + 一组渲染不炸），全组件视觉/a11y 在 Task 11 dev 预览页 + Radix 上游保证。

- [ ] **Step 1: 跑 shadcn add（CLI 生成组件源码）**

Run（在 `packages/web/frontend/` 下）:
```bash
npx shadcn@latest add button input textarea label select checkbox switch card badge dialog tabs tooltip sonner skeleton table --yes
```
Expected: `src/components/ui/` 下生成上述组件文件；自动 `npm install` 对应 `@radix-ui/*` 依赖；`@radix-ui/*` 写入 `package.json` dependencies。

> 若 CLI 提示找不到 `tailwind.config` 或 css，确认 `components.json`（Task 2）的 `tailwind.config` / `tailwind.css` 路径正确；`utils` alias 指 `@/lib/utils`（与 Task 1 cn 一致）。

- [ ] **Step 2: 验证文件生成**

Run:
```bash
ls packages/web/frontend/src/components/ui/
```
Expected: 含 `button.tsx input.tsx textarea.tsx label.tsx select.tsx checkbox.tsx switch.tsx card.tsx badge.tsx dialog.tsx tabs.tsx tooltip.tsx sonner.tsx skeleton.tsx table.tsx`。

- [ ] **Step 3: 写渲染测试 `src/components/ui/ui.test.tsx`**

Create `packages/web/frontend/src/components/ui/ui.test.tsx`:
```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

describe("shadcn ui 组件集成", () => {
  it("Button 各 variant 渲染不炸", () => {
    const { container } = render(
      <>
        <Button>default</Button>
        <Button variant="secondary">sec</Button>
        <Button variant="ghost">ghost</Button>
        <Button variant="destructive">destructive</Button>
        <Button variant="outline">outline</Button>
      </>
    );
    expect(screen.getByRole("button", { name: "default" })).toBeInTheDocument();
    expect(container.querySelectorAll("button")).toHaveLength(5);
  });

  it("Button size=icon 是方形（a11y：aria-label）", () => {
    render(<Button size="icon" aria-label="操作" />);
    expect(screen.getByRole("button", { name: "操作" })).toBeInTheDocument();
  });

  it("Input 渲染 + placeholder", () => {
    render(<Input placeholder="输入" />);
    expect(screen.getByPlaceholderText("输入")).toBeInTheDocument();
  });

  it("Badge 渲染", () => {
    render(<Badge>badge</Badge>);
    expect(screen.getByText("badge")).toBeInTheDocument();
  });

  it("Card 含 header/title/content 子组件", () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>title</CardTitle>
        </CardHeader>
        <CardContent>content</CardContent>
      </Card>
    );
    expect(screen.getByText("title")).toBeInTheDocument();
    expect(screen.getByText("content")).toBeInTheDocument();
  });
});
```

- [ ] **Step 4: 跑测试验证通过**

Run: `npx vitest run src/components/ui/ui.test.tsx`
Expected: PASS（5 用例全绿）。若红，回查 shadcn copy 是否完整、`cn` alias 是否生效。

- [ ] **Step 5: 跑构建确认 TS strict 不报（shadcn 组件多余 import 会被 noUnusedLocals 卡）**

Run: `npx tsc -b`
Expected: 0 错误。若 shadcn copy 组件有未用 import，删除该 import。

- [ ] **Step 6: 跑全套测试不破**

Run: `npx vitest run`
Expected: 全绿。

- [ ] **Step 7: Commit**

```bash
git add packages/web/frontend/src/components/ui packages/web/frontend/src/hooks 2>/dev/null; git add packages/web/frontend/package.json packages/web/frontend/package-lock.json packages/web/frontend/src/components/ui/ui.test.tsx
git commit -m "feat(web): DSF Task5 shadcn 首批组件 copy（button/input/dialog/.../table）+ 集成渲染测试"
```

---

## Task 6: 自写组件 Empty + Spinner

**Files:**
- Create: `packages/web/frontend/src/components/Empty.tsx`
- Create: `packages/web/frontend/src/components/Spinner.tsx`
- Modify: `packages/web/frontend/src/styles/index.css`（加 spinner keyframes）
- Test: `packages/web/frontend/src/components/Empty.test.tsx`
- Test: `packages/web/frontend/src/components/Spinner.test.tsx`

**Interfaces:**
- Consumes: 无（纯 React + Tailwind class；Spinner 用 `.shannon-spinner` class，keyframes 在 index.css）
- Produces:
  - `Empty({ icon?, title, hint?, children? })` — 空态（icon 默认 `∅`）
  - `Spinner({ label? })` — braille spinner（`role="status"`）

- [ ] **Step 1: 加 spinner keyframes 到 `src/styles/index.css`**

Modify `packages/web/frontend/src/styles/index.css`，在文件**末尾**追加:
```css

/* braille spinner（DSF 自包含，不依赖 events.css） */
@keyframes shannon-spin {
  0% { content: "⠋"; } 10% { content: "⠙"; } 20% { content: "⠹"; } 30% { content: "⠸"; }
  40% { content: "⠼"; } 50% { content: "⠴"; } 60% { content: "⠦"; } 70% { content: "⠧"; }
  80% { content: "⠇"; } 90% { content: "⠏"; }
}
.shannon-spinner::before {
  content: "⠋";
  animation: shannon-spin 1.05s steps(1) infinite;
  display: inline-block;
  width: 1ch;
  color: hsl(var(--primary));
}
@media (prefers-reduced-motion: reduce) {
  .shannon-spinner::before { animation: none; content: "•"; }
}
```

- [ ] **Step 2: 写失败测试 `src/components/Empty.test.tsx`**

Create `packages/web/frontend/src/components/Empty.test.tsx`:
```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Empty } from "./Empty";

describe("Empty", () => {
  it("渲染 icon + title + hint", () => {
    render(<Empty icon="∅" title="no workspaces" hint="新建一个扫描" />);
    expect(screen.getByText("no workspaces")).toBeInTheDocument();
    expect(screen.getByText("新建一个扫描")).toBeInTheDocument();
    expect(screen.getByText("∅")).toBeInTheDocument();
  });
  it("无 icon 用默认 ∅", () => {
    render(<Empty title="empty" />);
    expect(screen.getByText("∅")).toBeInTheDocument();
  });
  it("渲染 children CTA slot", () => {
    render(<Empty title="empty"><button>CTA</button></Empty>);
    expect(screen.getByRole("button", { name: "CTA" })).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: 写失败测试 `src/components/Spinner.test.tsx`**

Create `packages/web/frontend/src/components/Spinner.test.tsx`:
```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Spinner } from "./Spinner";

describe("Spinner", () => {
  it("渲染 label + role=status（a11y）", () => {
    render(<Spinner label="loading" />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByText(/loading/)).toBeInTheDocument();
  });
  it("无 label 也渲染（aria-live polite）", () => {
    const { container } = render(<Spinner />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(container.querySelector(".shannon-spinner")).toBeTruthy();
  });
});
```

- [ ] **Step 4: 跑测试验证失败**

Run: `npx vitest run src/components/Empty.test.tsx src/components/Spinner.test.tsx`
Expected: FAIL（组件未定义）。

- [ ] **Step 5: 写 `src/components/Empty.tsx`**

Create `packages/web/frontend/src/components/Empty.tsx`:
```tsx
import type { ReactNode } from "react";

export function Empty({
  icon = "∅",
  title,
  hint,
  children,
}: {
  icon?: ReactNode;
  title: string;
  hint?: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-16 text-muted-foreground">
      <div className="text-3xl">{icon}</div>
      <div className="text-base text-foreground">{title}</div>
      {hint && <div className="text-sm">{hint}</div>}
      {children && <div className="mt-2">{children}</div>}
    </div>
  );
}
```

- [ ] **Step 6: 写 `src/components/Spinner.tsx`**

Create `packages/web/frontend/src/components/Spinner.tsx`:
```tsx
export function Spinner({ label }: { label?: string }) {
  return (
    <span
      role="status"
      aria-live="polite"
      className="inline-flex items-center gap-1.5 text-sm text-primary"
    >
      <span className="shannon-spinner" aria-hidden="true" />
      {label}
    </span>
  );
}
```

- [ ] **Step 7: 跑测试验证通过**

Run: `npx vitest run src/components/Empty.test.tsx src/components/Spinner.test.tsx`
Expected: PASS（6 用例全绿）。

- [ ] **Step 8: Commit**

```bash
git add packages/web/frontend/src/styles/index.css packages/web/frontend/src/components/Empty.tsx packages/web/frontend/src/components/Empty.test.tsx packages/web/frontend/src/components/Spinner.tsx packages/web/frontend/src/components/Spinner.test.tsx
git commit -m "feat(web): DSF Task6 自写 Empty + Spinner（braille 自包含 keyframes）"
```

---

## Task 7: 业务徽章包装（MergeSourceBadge + ReachableBadge）+ 可达性边框 utility

**Files:**
- Create: `packages/web/frontend/src/components/vuln-badges.tsx`
- Modify: `packages/web/frontend/src/styles/index.css`（加 `.card-reachable` utility，对齐 spec §4 Card reachable 变体）
- Test: `packages/web/frontend/src/components/vuln-badges.test.tsx`

**Interfaces:**
- Consumes: `Badge` from `@/components/ui/badge`（Task 5）；`cn` from `@/lib/utils`
- Produces:
  - `type MergeSource = "llm-only" | "gitnexus-only" | "both"`
  - `MergeSourceBadge({ source })` — 双轨徽章（LLM 💭 magenta / GN 🔍 cyan / both ✓ green）
  - `ReachableBadge({ reachable })` — 可达性徽章（● 可达 red / ○ 内部 muted）
  - `.card-reachable` CSS utility（`border-left: 3px solid hsl(var(--red))`，供子项目 VulnCard 复用 `<Card className="card-reachable">`）

- [ ] **Step 1: 写失败测试 `src/components/vuln-badges.test.tsx`**

Create `packages/web/frontend/src/components/vuln-badges.test.tsx`:
```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { MergeSourceBadge, ReachableBadge } from "./vuln-badges";

describe("MergeSourceBadge", () => {
  it("llm-only → 💭 LLM轨 + magenta", () => {
    const { container } = render(<MergeSourceBadge source="llm-only" />);
    expect(screen.getByText(/LLM轨/)).toBeInTheDocument();
    expect(container.querySelector(".text-magenta")).toBeTruthy();
  });
  it("gitnexus-only → 🔍 GN轨 + cyan", () => {
    const { container } = render(<MergeSourceBadge source="gitnexus-only" />);
    expect(screen.getByText(/GN轨/)).toBeInTheDocument();
    expect(container.querySelector(".text-cyan")).toBeTruthy();
  });
  it("both → ✓ 双轨确认 + green", () => {
    const { container } = render(<MergeSourceBadge source="both" />);
    expect(screen.getByText(/双轨确认/)).toBeInTheDocument();
    expect(container.querySelector(".text-green")).toBeTruthy();
  });
});

describe("ReachableBadge", () => {
  it("reachable=true → ● 可达 + red", () => {
    const { container } = render(<ReachableBadge reachable={true} />);
    expect(container.textContent).toMatch(/可达/);
    expect(container.querySelector(".text-red")).toBeTruthy();
  });
  it("reachable=false → ○ 内部 + muted", () => {
    render(<ReachableBadge reachable={false} />);
    expect(screen.getByText(/内部/)).toBeInTheDocument();
  });
});

describe("card-reachable utility（spec §4 Card 可达性变体）", () => {
  it("index.css 含 .card-reachable 规则消费 --red", () => {
    const css = readFileSync(resolve(__dirname, "../styles/index.css"), "utf8");
    expect(css).toContain(".card-reachable");
    expect(css).toContain("hsl(var(--red))");
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `npx vitest run src/components/vuln-badges.test.tsx`
Expected: FAIL（模块未定义）。

- [ ] **Step 3: 写实现（utility + 组件）**

3a. 加 `.card-reachable` utility 到 `src/styles/index.css`，在文件**末尾**追加:

```css

/* Card 可达性变体（spec §4，供子项目 VulnCard：<Card className="card-reachable">） */
.card-reachable {
  border-left: 3px solid hsl(var(--red));
}
```

3b. Create `packages/web/frontend/src/components/vuln-badges.tsx`:
```tsx
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export type MergeSource = "llm-only" | "gitnexus-only" | "both";

const MERGE_MAP: Record<MergeSource, { label: string; cls: string }> = {
  "llm-only": { label: "💭 LLM轨", cls: "text-magenta border-magenta/40" },
  "gitnexus-only": { label: "🔍 GN轨", cls: "text-cyan border-cyan/40" },
  "both": { label: "✓ 双轨确认", cls: "text-green border-green/40" },
};

export function MergeSourceBadge({ source }: { source: MergeSource }) {
  const m = MERGE_MAP[source];
  return (
    <Badge variant="outline" className={cn("font-mono", m.cls)}>
      {m.label}
    </Badge>
  );
}

export function ReachableBadge({ reachable }: { reachable: boolean }) {
  if (!reachable) {
    return (
      <Badge variant="outline" className="font-mono text-muted-foreground">
        ○ 内部
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="font-mono text-red border-red/40">
      ● 可达
    </Badge>
  );
}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `npx vitest run src/components/vuln-badges.test.tsx`
Expected: PASS（5 用例全绿）。

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/styles/index.css packages/web/frontend/src/components/vuln-badges.tsx packages/web/frontend/src/components/vuln-badges.test.tsx
git commit -m "feat(web): DSF Task7 业务徽章包装（双轨+可达性）+ Card reachable utility"
```

---

## Task 8: ThemeToggle 组件

**Files:**
- Create: `packages/web/frontend/src/components/layout/ThemeToggle.tsx`
- Test: `packages/web/frontend/src/components/layout/ThemeToggle.test.tsx`

**Interfaces:**
- Consumes: `Button` from `@/components/ui/button`（Task 5）；`applyTheme, getInitialTheme, Theme` from `@/lib/theme`（Task 4）
- Produces: `ThemeToggle()` — 切换 dark↔light，写 `<html>` class + localStorage，图标随当前主题切（dark 显 ☀️、light 显 🌙）。

- [ ] **Step 1: 写失败测试 `src/components/layout/ThemeToggle.test.tsx`**

Create `packages/web/frontend/src/components/layout/ThemeToggle.test.tsx`:
```tsx
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeToggle } from "./ThemeToggle";
import { THEME_KEY } from "@/lib/theme";

describe("ThemeToggle", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark", "light");
  });

  it("渲染按钮 + a11y label", () => {
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: /切换主题/ })).toBeInTheDocument();
  });

  it("dark 状态下显 ☀️（提示切到浅色）", () => {
    document.documentElement.classList.add("dark");
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: /切换主题/ }).textContent).toContain("☀️");
  });

  it("点击切换 dark→light 并持久化", () => {
    document.documentElement.classList.add("dark");
    render(<ThemeToggle />);
    fireEvent.click(screen.getByRole("button", { name: /切换主题/ }));
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
  });

  it("点击切换 light→dark", () => {
    document.documentElement.classList.add("light");
    render(<ThemeToggle />);
    fireEvent.click(screen.getByRole("button", { name: /切换主题/ }));
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run: `npx vitest run src/components/layout/ThemeToggle.test.tsx`
Expected: FAIL（组件未定义）。

- [ ] **Step 3: 写 `src/components/layout/ThemeToggle.tsx`**

Create `packages/web/frontend/src/components/layout/ThemeToggle.tsx`:
```tsx
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { applyTheme, getInitialTheme, type Theme } from "@/lib/theme";

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    applyTheme(next);
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggle}
      aria-label="切换主题"
      title={theme === "dark" ? "切换到浅色" : "切换到深色"}
    >
      {theme === "dark" ? "☀️" : "🌙"}
    </Button>
  );
}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `npx vitest run src/components/layout/ThemeToggle.test.tsx`
Expected: PASS（4 用例全绿）。

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/components/layout/ThemeToggle.tsx packages/web/frontend/src/components/layout/ThemeToggle.test.tsx
git commit -m "feat(web): DSF Task8 ThemeToggle（dark↔light 切换 + 持久化）"
```

---

## Task 9: TopBar + AppShell

**Files:**
- Create: `packages/web/frontend/src/components/layout/TopBar.tsx`
- Create: `packages/web/frontend/src/components/layout/AppShell.tsx`
- Test: `packages/web/frontend/src/components/layout/TopBar.test.tsx`
- Test: `packages/web/frontend/src/components/layout/AppShell.test.tsx`

**Interfaces:**
- Consumes: `ThemeToggle`（Task 8）；`Link / NavLink / Outlet` from `react-router-dom`
- Produces:
  - `TopBar()` — 字标 + 主导航（Workspaces/Scan 启用；Dashboard/Settings DSF 阶段 disabled）+ ThemeToggle
  - `AppShell()` — `<div min-h-screen>` + `<TopBar />` + `<main><Outlet /></main>`，作 router 根 layout（Task 10 接）

- [ ] **Step 1: 写失败测试 `src/components/layout/TopBar.test.tsx`**

Create `packages/web/frontend/src/components/layout/TopBar.test.tsx`:
```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { TopBar } from "./TopBar";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="*" element={<TopBar />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("TopBar", () => {
  it("品牌字标 Shannon + 主导航", () => {
    renderAt("/");
    expect(screen.getByText(/Shannon/)).toBeInTheDocument();
    expect(screen.getByText("Workspaces")).toBeInTheDocument();
    expect(screen.getByText("Scan")).toBeInTheDocument();
  });

  it("Dashboard / Settings DSF 阶段 disabled（非 <a>）", () => {
    renderAt("/");
    const dash = screen.getByText("Dashboard");
    const settings = screen.getByText("Settings");
    expect(dash.tagName).not.toBe("A");
    expect(settings.tagName).not.toBe("A");
    expect(dash.closest("[aria-disabled='true']") ?? dash.parentElement?.closest("[aria-disabled='true']") ?? dash).toBeTruthy();
  });

  it("当前 /scan/new → Scan NavLink data-active=true", () => {
    renderAt("/scan/new");
    expect(screen.getByText("Scan").getAttribute("data-active")).toBe("true");
  });

  it("非当前路由 NavLink data-active=false", () => {
    renderAt("/");
    expect(screen.getByText("Scan").getAttribute("data-active")).toBe("false");
  });

  it("含主题切换入口", () => {
    renderAt("/");
    expect(screen.getByRole("button", { name: /切换主题/ })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 写失败测试 `src/components/layout/AppShell.test.tsx`**

Create `packages/web/frontend/src/components/layout/AppShell.test.tsx`:
```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "./AppShell";

describe("AppShell", () => {
  it("渲染 TopBar + Outlet 内容", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<div>page-content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    );
    expect(screen.getByText(/Shannon/)).toBeInTheDocument();
    expect(screen.getByText("page-content")).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: 跑测试验证失败**

Run: `npx vitest run src/components/layout/TopBar.test.tsx src/components/layout/AppShell.test.tsx`
Expected: FAIL（组件未定义）。

- [ ] **Step 4: 写 `src/components/layout/TopBar.tsx`**

Create `packages/web/frontend/src/components/layout/TopBar.tsx`:
```tsx
import { Link, NavLink } from "react-router-dom";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "./ThemeToggle";

interface NavItem {
  label: string;
  to: string;
  disabled?: boolean;
  end?: boolean;
}

/*
 * DSF 阶段导航项（迁移期）：
 * - Workspaces → 现有路由 /（WorkspaceListPage 当前位置；子项目 2 改 /workspaces 后同步改 to）
 * - Scan → /scan/new（启用）
 * - Dashboard → 未来 /（disabled，子项目 5 启用）
 * - Settings → 未来 /settings（disabled，子项目 5 启用）
 */
const NAV: NavItem[] = [
  { label: "Dashboard", to: "/", disabled: true, end: true },
  { label: "Workspaces", to: "/", end: true },
  { label: "Scan", to: "/scan/new" },
  { label: "Settings", to: "/settings", disabled: true },
];

export function TopBar() {
  return (
    <header className="border-b border-border bg-card">
      <div className="mx-auto flex h-12 max-w-[1400px] items-center gap-6 px-7">
        <Link to="/" className="flex items-center gap-1.5 font-serif text-base">
          <span style={{ color: "hsl(var(--cyan))" }}>⬡</span>
          <span>Shannon</span>
        </Link>
        <nav className="flex items-center gap-1" aria-label="主导航">
          {NAV.map((n) =>
            n.disabled ? (
              <span
                key={n.label}
                aria-disabled="true"
                className="cursor-not-allowed border-b-2 border-transparent px-3 py-1.5 text-sm text-muted-foreground/50"
              >
                {n.label}
              </span>
            ) : (
              <NavLink key={n.label} to={n.to} end={n.end} className="inline-flex">
                {({ isActive }) => (
                  <span
                    data-active={isActive}
                    className={cn(
                      "border-b-2 px-3 py-1.5 text-sm transition-colors",
                      isActive
                        ? "border-primary text-primary"
                        : "border-transparent text-muted-foreground hover:text-foreground"
                    )}
                  >
                    {n.label}
                  </span>
                )}
              </NavLink>
            )
          )}
        </nav>
        <div className="ml-auto flex items-center gap-1">
          {/* 运行中扫描指示器 slot（子项目 5 接 SSE） */}
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
```

- [ ] **Step 5: 写 `src/components/layout/AppShell.tsx`**

Create `packages/web/frontend/src/components/layout/AppShell.tsx`:
```tsx
import { Outlet } from "react-router-dom";
import { TopBar } from "./TopBar";

export function AppShell() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <TopBar />
      <main className="mx-auto max-w-[1400px] px-7 py-5">
        <Outlet />
      </main>
    </div>
  );
}
```

- [ ] **Step 6: 跑测试验证通过**

Run: `npx vitest run src/components/layout/TopBar.test.tsx src/components/layout/AppShell.test.tsx`
Expected: PASS（6 用例全绿）。

- [ ] **Step 7: Commit**

```bash
git add packages/web/frontend/src/components/layout/TopBar.tsx packages/web/frontend/src/components/layout/TopBar.test.tsx packages/web/frontend/src/components/layout/AppShell.tsx packages/web/frontend/src/components/layout/AppShell.test.tsx
git commit -m "feat(web): DSF Task9 TopBar（字标+主导航+disabled 项）+ AppShell（根 layout）"
```

---

## Task 10: 路由壳改造（根包 AppShell + dev 预览页 dev-only 守卫）

**Files:**
- Modify: `packages/web/frontend/src/router.tsx`
- Create: `packages/web/frontend/src/pages/DevComponentsPage.tsx`（Task 11 填内容，本 task 先占位）
- Test: `packages/web/frontend/src/router.test.ts`

**Interfaces:**
- Consumes: `AppShell`（Task 9）；现有 `WorkspaceListPage / ScanNewPage / WorkspaceDetail` 及其子路由（不动）
- Produces: `router`（createBrowserRouter）根元素 `<AppShell />`，业务路由作 children；`/dev/components` 仅 `import.meta.env.DEV` 时注册。

- [ ] **Step 1: 建 `DevComponentsPage` 占位（Task 11 填内容）**

Create `packages/web/frontend/src/pages/DevComponentsPage.tsx`:
```tsx
export function DevComponentsPage() {
  return (
    <div className="space-y-4">
      <h1 className="font-serif text-2xl">Component Preview (dev-only)</h1>
      <p className="text-muted-foreground">Task 11 填充组件清单。</p>
    </div>
  );
}
```

- [ ] **Step 2: 改 `src/router.tsx` 全文（根包 AppShell + dev 路由守卫）**

Replace `packages/web/frontend/src/router.tsx` 全文为:
```tsx
import { createBrowserRouter, useNavigate, useParams } from "react-router-dom";
import { useEffect } from "react";
import { WorkspaceListPage } from "./pages/WorkspaceListPage";
import { ScanNewPage } from "./pages/ScanNewPage";
import WorkspaceDetail from "./routes/WorkspaceDetail";
import { OverviewTab } from "./routes/WorkspaceDetail/OverviewTab";
import { ReportTab } from "./routes/WorkspaceDetail/ReportTab";
import { DeliverablesTab } from "./routes/WorkspaceDetail/DeliverablesTab";
import { LogsTab } from "./routes/WorkspaceDetail/LogsTab";
import { LiveTab } from "./routes/WorkspaceDetail/LiveTab";
import { apiGet } from "./api/client";
import type { SessionData } from "./api/types";
import { AppShell } from "./components/layout/AppShell";
import { DevComponentsPage } from "./pages/DevComponentsPage";

// 默认 tab：进行中 → live，完成 → report。fetch status 后 navigate（replace 避免占历史栈）。
function DefaultTab() {
  const { workspace } = useParams<{ workspace: string }>();
  const nav = useNavigate();
  useEffect(() => {
    apiGet<SessionData>(`/workspaces/${workspace}`).then((s) => {
      const st = s.status ?? s.session?.status ?? "running";
      nav(st === "completed" || st === "done" ? "report" : "live", { replace: true });
    }).catch(() => nav("live", { replace: true }));
  }, [workspace, nav]);
  return null;
}

const devRoutes = import.meta.env.DEV
  ? [{ path: "/dev/components", element: <DevComponentsPage /> }]
  : [];

export const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { path: "/", element: <WorkspaceListPage /> },
      { path: "/scan/new", element: <ScanNewPage /> },
      {
        path: "/p/:workspace",
        element: <WorkspaceDetail />,
        children: [
          { index: true, element: <DefaultTab /> },
          { path: "overview", element: <OverviewTab /> },
          { path: "report", element: <ReportTab /> },
          { path: "deliverables", element: <DeliverablesTab /> },
          { path: "logs", element: <LogsTab /> },
          { path: "live", element: <LiveTab /> },
        ],
      },
      ...devRoutes,
    ],
  },
]);
```

- [ ] **Step 3: 写结构断言测试 `src/router.test.ts`**

Create `packages/web/frontend/src/router.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const router = readFileSync(resolve(__dirname, "../src/router.tsx"), "utf8");

describe("router.tsx 结构", () => {
  it("根包 <AppShell />", () => {
    expect(router).toContain("AppShell");
    expect(router).toMatch(/element:\s*<AppShell/);
  });
  it("dev 预览页 dev-only 守卫（import.meta.env.DEV）", () => {
    expect(router).toContain("import.meta.env.DEV");
    expect(router).toContain("DevComponentsPage");
    expect(router).toContain("/dev/components");
  });
  it("保留现有业务路由", () => {
    expect(router).toContain("WorkspaceListPage");
    expect(router).toContain("ScanNewPage");
    expect(router).toContain("WorkspaceDetail");
    expect(router).toContain("DefaultTab");
  });
});
```

- [ ] **Step 4: 跑测试验证通过**

Run: `npx vitest run src/router.test.ts`
Expected: PASS（3 用例全绿）。

- [ ] **Step 5: 跑全套测试 + 构建不破（AppShell 套到现有路由，业务页内部未动）**

Run:
```bash
npx vitest run
npx tsc -b
```
Expected: 全套测试绿；TS 0 错。

> 注：现有 `App.test.tsx` 若 render `<App />`（RouterProvider），AppShell 会一并渲染；只要其断言不冲突即绿。若 `App.test.tsx` 因 AppShell 包裹后断言失败，**不改 AppShell/TopBar**，而是调整 `App.test.tsx` 的断言以适配新外壳（属本 task 范畴）。

- [ ] **Step 6: Commit**

```bash
git add packages/web/frontend/src/router.tsx packages/web/frontend/src/router.test.ts packages/web/frontend/src/pages/DevComponentsPage.tsx
git commit -m "feat(web): DSF Task10 路由壳根包 AppShell + dev 预览页 dev-only 守卫"
```

---

## Task 11: dev 预览页内容 + 冒烟回归

**Files:**
- Modify: `packages/web/frontend/src/pages/DevComponentsPage.tsx`（占位 → 完整组件清单）
- 无新测试（本 task 是内容填充 + 人工冒烟；结构由 Task 10 `router.test.ts` 锁定）

**Interfaces:**
- Consumes: 全部 DSF 组件（Button/Input/Badge/Card/Spinner/Empty/MergeSourceBadge/ReachableBadge/ThemeToggle + Task 5 copy 的其余 shadcn 组件）
- Produces: `/dev/components` 页（dev-only）罗列全部组件 × 状态，供人工冒烟双主题 + 风格漂移把关。

- [ ] **Step 1: 写 `DevComponentsPage.tsx` 全文**

Replace `packages/web/frontend/src/pages/DevComponentsPage.tsx` 全文为:
```tsx
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Spinner } from "@/components/Spinner";
import { Empty } from "@/components/Empty";
import { MergeSourceBadge, ReachableBadge } from "@/components/vuln-badges";
import { ThemeToggle } from "@/components/layout/ThemeToggle";

export function DevComponentsPage() {
  return (
    <div className="space-y-8">
      <h1 className="font-serif text-2xl">Component Preview (dev-only)</h1>

      <Section title="Theme">
        <ThemeToggle />
        <span className="text-sm text-muted-foreground">点切换深/浅，刷新验持久化</span>
      </Section>

      <Section title="Buttons">
        <Button>default</Button>
        <Button variant="secondary">secondary</Button>
        <Button variant="ghost">ghost</Button>
        <Button variant="outline">outline</Button>
        <Button variant="destructive">destructive</Button>
        <Button size="sm">small</Button>
        <Button size="icon" aria-label="op">⏵</Button>
      </Section>

      <Section title="Inputs">
        <Label htmlFor="i1">文本</Label>
        <Input id="i1" placeholder="type..." />
        <Textarea placeholder="多行" />
      </Section>

      <Section title="Selection">
        <Checkbox id="c1" defaultChecked />
        <Label htmlFor="c1">勾选</Label>
        <Switch defaultChecked aria-label="开关" />
      </Section>

      <Section title="Badges">
        <Badge>default</Badge>
        <MergeSourceBadge source="llm-only" />
        <MergeSourceBadge source="gitnexus-only" />
        <MergeSourceBadge source="both" />
        <ReachableBadge reachable={true} />
        <ReachableBadge reachable={false} />
      </Section>

      <Section title="Spinner">
        <Spinner label="running" />
        <Spinner />
      </Section>

      <Section title="Empty">
        <div className="w-full">
          <Empty title="no workspaces" hint="新建一个扫描开始">
            <Button>+ new scan</Button>
          </Empty>
        </div>
      </Section>

      <Section title="Skeleton">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-4 w-64" />
      </Section>

      <Section title="Tabs">
        <Tabs defaultValue="a">
          <TabsList>
            <TabsTrigger value="a">Tab A</TabsTrigger>
            <TabsTrigger value="b">Tab B</TabsTrigger>
          </TabsList>
          <TabsContent value="a">content a</TabsContent>
          <TabsContent value="b">content b</TabsContent>
        </Tabs>
      </Section>

      <Section title="Card">
        <Card className="max-w-sm">
          <CardHeader>
            <CardTitle>title</CardTitle>
          </CardHeader>
          <CardContent>content</CardContent>
        </Card>
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h2 className="font-serif text-lg text-muted-foreground">{title}</h2>
      <div className="flex flex-wrap items-center gap-3 rounded-md border border-border bg-card p-4">
        {children}
      </div>
    </section>
  );
}
```

- [ ] **Step 2: 跑构建 + 全套测试**

Run:
```bash
npx tsc -b
npx vite build
npx vitest run
```
Expected: TS 0 错；build 成功；全套测试绿。

- [ ] **Step 3: 人工冒烟（dev server）**

Run: `npm run dev`
人工验证（浏览器）:
1. 访问 `/` → 现有 WorkspaceListPage 套上新 TopBar（字标 + Workspaces/Scan 启用，Dashboard/Settings 灰显 disabled），页面内部内容仍是旧样式（增量迁移，预期）。
2. 访问 `/dev/components` → 全部组件渲染正常；点 ThemeToggle 切深/浅，所有组件双主题渲染；刷新页面主题持久化（验防 FOUC 脚本：刷新瞬间无白闪）。
3. 浅色下检查语义色对比度（cyan/magenta/red 在浅底是否可读；若某色 AA 不达，调 `tokens.css` 浅色组对应 channel 值，重跑 Step 2）。
4. 访问 `/scan/new` / `/p/:ws/*`（若已有 workspace）→ TopBar 套上，内部内容旧样式不破（preflight 不破坏 `.page/.ledger/.form-area`）。

Expected: 上述全通过；preflight 副作用（如有）记录到 `docs/superpowers/specs/2026-07-04-shannon-web-redesign-dsf-design.md` §9 风险跟进，DSF 不修业务页内部（留给子项目 2-5）。

- [ ] **Step 4: Commit**

```bash
git add packages/web/frontend/src/pages/DevComponentsPage.tsx
git commit -m "feat(web): DSF Task11 dev 预览页（组件清单 × 双主题）+ 冒烟回归"
```

---

## Definition of Done

- 所有 11 task commit 落地，每个 task 测试绿、commit 独立。
- `npx vitest run` 全绿（含 DSF 新增 + 现有 dashboardReducer / useEventSource / 各组件测试）。
- `npx tsc -b && npx vite build` 0 错。
- 访问 `/` / `/scan/new` / `/p/:ws/*` 均见新 TopBar 外壳；业务页内部旧样式保留（增量迁移）。
- `/dev/components`（dev-only）罗列全部组件，双主题切换正常 + 持久化 + 无 FOUC。
- 旧 `events.css` 文件头 `@deprecated` 就位；新组件无任何规则写入 `events.css`。
- 四条不变量约束（语义色映射 / 事件日志Markdown 自定义层 / Plex / operator 调校）未破坏。

## 后续（非本 plan 范围）

子项目 2-5 各自 brainstorm + spec + plan：
- 子项目 2：WorkspaceListPage 重做 + 文件浏览器（`GET /api/fs/browse`）。
- 子项目 3：ScanNewPage 重做（集成文件浏览器）。
- 子项目 4：详情 5 tab 重做。
- 子项目 5：Dashboard 首页 + 设置页。

每个子项目随迁移逐页清除 `events.css` 中对应旧规则；最后一个子项目清空 `events.css`。
