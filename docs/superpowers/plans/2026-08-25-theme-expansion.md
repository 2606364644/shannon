# 主题库扩展（OpenDesign 六主题移植）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从 OpenDesign 库移植 6 个新主题（深色 sentry/arc/mission + 浅色 github/notion/kami），主题库 5 → 11，主色分层纪律 + kami 衬线例外落地。

**Architecture:** 零新架构——每主题在 `tokens.css` 追加一个双类选择器 palette 块（`.dark.theme-*` / `.light.theme-*`，特异性 (0,2,0) 覆盖 `:root`/`.light` 基础层），`theme.ts` 注册 id/mode/paletteClass，组件经 CSS var 消费自动换肤。Arc 深色玻璃复用 mac 的 vibrancy 三件套机制（`--backdrop-*` token，未定义主题回落 `none`，组件零改动）。

**Tech Stack:** React 19 + Tailwind (shadcn token `hsl(var(--x))` 消费) + Vitest（`tokens.test.ts` 为 CSS 文本断言、`theme.test.ts` 为 jsdom 行为断言）。

**Spec:** `docs/superpowers/specs/2026-08-25-theme-expansion-design.md`（真值表、材质语言、纪律修订的完整依据——本计划的 CSS 值全部出自 spec §4，已从 DESIGN.md hex 换算为 HSL channel）

## Global Constraints

- severity 红橙黄 **hue 锁定**：`--c-red` hue=5、`--c-orange` hue=24、`--c-yellow` hue=38（只调 lightness/sat 保 AA）；cyan=GitNexus / magenta=LLM 双轨语义。
- 主色分层：品牌层（charcoal/warm-paper/midnight/graphite）coral 不动；新主题用本色 primary；**arc 例外保持 coral** `15 60% 56%`。
- 字体默认共享 IBM Plex；**仅 kami** 覆盖 `--font-sans`。
- 层 F 磨砂：仅 arc 定义 card+float、sentry 仅 float，其余不定义（组件 `var(--backdrop-*,none)` 回落）。
- 圆角：sentry 8 / arc 16 / mission 4 / github 6 / notion 6 / kami 6（px）。
- 测试只跑改动相关文件（CLAUDE.md 测试陷阱约定）：`pnpm --dir packages/web/frontend test -- <file>`（或 `cd packages/web/frontend && npx vitest run <file>`）。
- `index.html` FOAC 脚本、`defaultThemeFor`、`oppositeBaseTheme` 一律不动。
- 工作目录：`packages/web/frontend/`（除注明外所有相对路径基于此）。

---

### Task 1: sentry 深色主题（Sentry 错误监控）

**Files:**
- Modify: `src/styles/tokens.css`（扩展主题段追加 `.dark.theme-sentry` 块 + 头部/扩展段纪律注释修订）
- Test: `src/styles/tokens.test.ts`

**Interfaces:**
- Produces: CSS 类 `.dark.theme-sentry`（挂载由 Task 7 的 `paletteClass: "theme-sentry"` 驱动）；层 F token `--backdrop-float`（sentry 深色版）。

- [ ] **Step 1: 写失败断言（tokens.test.ts 末尾追加）**

```ts
describe("扩展主题（2026-08-25 OpenDesign 六主题移植）", () => {
  it("sentry 块：紫黑双层表面 + Sentry 紫主色 + 玻璃浮层 token", () => {
    expect(tokens).toMatch(/\.dark\.theme-sentry\s*\{[\s\S]*?--background:\s*258 40% 10%;/);
    expect(tokens).toMatch(/\.dark\.theme-sentry\s*\{[\s\S]*?--primary:\s*247 44% 56%;/);
    expect(tokens).toMatch(/\.dark\.theme-sentry\s*\{[\s\S]*?--radius:\s*8px;/);
    expect(tokens).toMatch(/\.dark\.theme-sentry\s*\{[\s\S]*?--backdrop-float:\s*blur\(18px\) saturate\(180%\);/);
    // severity hue 锁定
    expect(tokens).toMatch(/\.dark\.theme-sentry\s*\{[\s\S]*?--c-red:\s*5\s/);
    expect(tokens).toMatch(/\.dark\.theme-sentry\s*\{[\s\S]*?--c-orange:\s*24\s/);
    expect(tokens).toMatch(/\.dark\.theme-sentry\s*\{[\s\S]*?--c-yellow:\s*38\s/);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/web/frontend && npx vitest run src/styles/tokens.test.ts`
Expected: FAIL（sentry 块不存在）

- [ ] **Step 3: 修订纪律注释 + 实现 sentry 块**

3a. tokens.css 头部注释（第 16 行 `扩展主题块见下方…` 一行）改为：

```css
   扩展主题块见下方（mac=Apple HIG / midnight=Linear / graphite=Geist /
   sentry=Sentry / arc=Arc Browser / mission=Mission Control /
   github=GitHub Primer / notion=Notion / kami=kami 纸质，2026-08-25 后六者
   移植自 OpenDesign DESIGN.md）。 */
```

3b. 扩展主题段纪律注释（原「三条设计纪律」段，约 144-147 行）改为：

```css
/* —— 扩展主题（2026-08-24 新增并材质升级，只增不删：charcoal/warm-paper 基础主题即上方 :root/.light 原样保留）——
   双类选择器 .dark.theme-* / .light.theme-*（特异性 (0,2,0)）严格高于 :root/.light，覆盖不依赖源顺序。
   设计纪律（2026-08-25 修订为分层制）：主色分层——品牌层（charcoal/warm-paper/midnight/graphite）coral
   保留；系统对齐层（mac/github/sentry/mission/notion/kami）用参考系统本色 primary；arc 以玻璃透质感为
   身份、primary 保持 coral（Arc 品牌珊瑚与 coral 同族不构成第二主色）。severity red/orange/yellow
   hue 锁定 5°/24°/38°只调 lightness/sat 保 AA；cyan=GitNexus 轨 / magenta=LLM 轨双轨语义色逐主题单独校对比。
   材质语言各主题独立：边框用 alpha 或实色随真值、阴影语言逐主题、圆角随主题气质覆盖（mac 12 / midnight 12 /
   graphite 8 / sentry 8 / arc 16 / mission 4 / github 6 / notion 6 / kami 6）；字体默认共享，仅 kami
   （衬线主导）覆盖 --font-sans。 */
```

3c. 在 `.dark.theme-graphite` 块后追加：

```css
/* Sentry（深 · 错误监控仪表盘，2026-08-25 移植自 OpenDesign design-system-sentry）：
   深紫黑「暗 IDE」——永不纯黑，紫调暖黑是底色灵魂；inset 按钮触感 + 紫调环境光晕 +
   浮层白玻璃（blur 18 saturate 180 的深色版，层 F 仅 float）。primary=Sentry 紫，
   magenta 用品牌粉。severity hue 锁定只调值。 */
.dark.theme-sentry {
  /* 层 A · shadcn token（bg #150f23 / card #1f1633 / popover #241d3d 推导抬升面 / border #362d59） */
  --background: 258 40% 10%;
  --foreground: 220 13% 91%;
  --card: 259 40% 14%;
  --card-foreground: 220 13% 91%;
  --popover: 253 36% 18%;
  --popover-foreground: 220 13% 91%;
  --primary: 247 44% 56%;
  --primary-foreground: 0 0% 100%;
  --secondary: 259 35% 16%;
  --secondary-foreground: 220 13% 91%;
  --muted: 259 35% 16%;
  --muted-foreground: 254 16% 65%;
  --accent: 253 32% 22%;
  --accent-foreground: 220 13% 91%;
  --destructive: 5 72% 55%;
  --destructive-foreground: 258 40% 10%;
  --border: 252 33% 26%;
  --input: 252 33% 26%;
  --ring: 247 44% 56%;

  /* 层 B · 语义色（magenta=品牌粉 #fa7faa；green=lime #c2ef4e 气质；severity hue 锁定） */
  --c-cyan: 190 70% 62%;
  --c-magenta: 339 92% 74%;
  --c-green: 77 83% 62%;
  --c-red: 5 72% 55%;
  --c-orange: 24 100% 63%;
  --c-yellow: 38 85% 58%;
  --c-amber: 36 78% 58%;

  /* 层 C · 圆角（Sentry 按钮/卡 8px） */
  --radius: 8px;

  /* 层 D · prose（紫底白文 + Sentry 紫链接） */
  --prose-body: 252 20% 90%;
  --prose-headings: 250 25% 94%;
  --prose-links: 247 55% 70%;
  --prose-bold: 250 25% 94%;
  --prose-code: 190 85% 64%;
  --prose-code-bg: 259 35% 16%;
  --prose-quotes: 252 12% 62%;
  --prose-bullets: 252 12% 62%;
  --prose-hr: 250 30% 90% / 0.12;

  /* 层 E · elevation（inset 按钮触感 + 紫调环境光晕 + 卡浮层标准影） */
  --shadow-card: inset 0 1px 0 hsl(252 40% 100% / 0.05), 0 0 0 1px hsl(252 33% 60% / 0.08), 0 10px 15px -3px hsl(258 60% 3% / 0.5), 0 4px 9px hsl(258 60% 3% / 0.9);
  --shadow-cta: inset hsl(0 0% 0% / 0.1) 0 1px 3px, 0 1px 2px hsl(258 60% 3% / 0.4), 0 6px 18px -6px hsl(247 60% 40% / 0.4);
  --shadow-cta-hover: inset hsl(0 0% 0% / 0.1) 0 1px 3px, 0 2px 4px hsl(258 60% 3% / 0.4), 0 12px 26px -6px hsl(247 65% 45% / 0.5);
  --shadow-toolbar: 0 1px 2px hsl(258 60% 3% / 0.4);
  --shadow-toolbar-hover: inset hsl(0 0% 0% / 0.08) 0 1px 3px, 0 2px 4px hsl(258 60% 3% / 0.45), 0 10px 24px -8px hsl(258 60% 3% / 0.6);

  /* 层 F · 玻璃浮层（DESIGN.md frosted glass 深色版；仅 float，卡片保持实底） */
  --backdrop-float: blur(18px) saturate(180%);
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd packages/web/frontend && npx vitest run src/styles/tokens.test.ts`
Expected: PASS（全绿，含新增 sentry 断言）

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/styles/tokens.css packages/web/frontend/src/styles/tokens.test.ts
git commit -m "feat(web-theme): sentry 深色主题——Sentry 紫黑仪表盘(inset 触感+紫调光晕+浮层玻璃),纪律注释修订为分层制"
```

---

### Task 2: arc 深色玻璃主题（Arc Browser）

**Files:**
- Modify: `src/styles/tokens.css`（`.dark.theme-arc` 块 + `.dark.theme-arc body` 环境光层）
- Test: `src/styles/tokens.test.ts`

**Interfaces:**
- Produces: CSS 类 `.dark.theme-arc`；层 F token `--backdrop-card`/`--backdrop-float`（深色玻璃对，组件 `var(--backdrop-*,none)` 现成消费）；与 mac 成「浅=Mac / 深=Arc」玻璃对。

- [ ] **Step 1: 写失败断言（tokens.test.ts 扩展主题 describe 内追加）**

```ts
  it("arc 块：深色半透玻璃表面 + coral 主色 + 磨砂三件套 + 环境光层", () => {
    expect(tokens).toMatch(/\.dark\.theme-arc\s*\{[\s\S]*?--card:\s*240 10% 12% \/ 0\.6;/);
    expect(tokens).toMatch(/\.dark\.theme-arc\s*\{[\s\S]*?--primary:\s*15 60% 56%;/);
    expect(tokens).toMatch(/\.dark\.theme-arc\s*\{[\s\S]*?--radius:\s*16px;/);
    expect(tokens).toMatch(/\.dark\.theme-arc\s*\{[\s\S]*?--backdrop-card:\s*saturate\(180%\) blur\(24px\);/);
    expect(tokens).toMatch(/\.dark\.theme-arc\s*\{[\s\S]*?--backdrop-float:\s*saturate\(180%\) blur\(36px\);/);
    expect(tokens).toMatch(/\.dark\.theme-arc body\s*\{[\s\S]*?background-attachment:\s*fixed;/);
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/web/frontend && npx vitest run src/styles/tokens.test.ts`
Expected: FAIL（arc 块不存在）

- [ ] **Step 3: 实现 arc 块（`.dark.theme-sentry` 块后追加）**

```css
/* Arc（深 · Arc Browser 玻璃，2026-08-25 移植自 OpenDesign design-system-arc）：
   mac 的深色玻璃对——表面 alpha 化 + 磨砂三件套 + body 环境光层（Arc 渐变温度暗版）。
   primary 保持 coral（Arc 品牌珊瑚 #ff5f5f 与 coral 同族，玻璃是身份）；squircle 16px 最软；
   浮起靠玻璃不靠投影（阴影极柔）。 */
.dark.theme-arc {
  /* 层 A · shadcn token（bg=#14141a Glass Dark 底实底化；card/popover 半透玻璃，带 alpha） */
  --background: 240 13% 9%;
  --foreground: 0 0% 98%;
  --card: 240 10% 12% / 0.6;
  --card-foreground: 0 0% 98%;
  --popover: 240 10% 14% / 0.75;
  --popover-foreground: 0 0% 98%;
  --primary: 15 60% 56%;
  --primary-foreground: 0 0% 100%;
  --secondary: 240 8% 16% / 0.65;
  --secondary-foreground: 0 0% 98%;
  --muted: 240 8% 16% / 0.65;
  --muted-foreground: 240 3% 56%;
  --accent: 240 8% 20% / 0.72;
  --accent-foreground: 0 0% 98%;
  --destructive: 5 62% 60%;
  --destructive-foreground: 240 13% 9%;
  --border: 0 0% 100% / 0.20;
  --input: 0 0% 100% / 0.20;
  --ring: 15 60% 56%;

  /* 层 B · 语义色（中性紫黑底，语义色沿用品牌深色值） */
  --c-cyan: 190 70% 60%;
  --c-magenta: 280 70% 78%;
  --c-green: 142 50% 56%;
  --c-red: 5 62% 60%;
  --c-orange: 24 75% 62%;
  --c-yellow: 38 75% 60%;
  --c-amber: 36 78% 58%;

  /* 层 C · 圆角（squircle 最软） */
  --radius: 16px;

  /* 层 D · prose（玻璃底近中性，prose 同品牌深色微调） */
  --prose-body: 240 8% 90%;
  --prose-headings: 240 10% 94%;
  --prose-links: 15 70% 65%;
  --prose-bold: 240 10% 94%;
  --prose-code: 192 90% 60%;
  --prose-code-bg: 240 8% 14%;
  --prose-quotes: 240 5% 62%;
  --prose-bullets: 240 5% 62%;
  --prose-hr: 240 10% 90% / 0.12;

  /* 层 E · elevation（Arc 极柔：浮起靠玻璃不靠投影） */
  --shadow-card: 0 0 0 1px hsl(0 0% 100% / 0.06), 0 8px 32px hsl(240 30% 2% / 0.08);
  --shadow-cta: 0 1px 2px hsl(240 30% 2% / 0.3), 0 4px 16px hsl(15 70% 45% / 0.3);
  --shadow-cta-hover: 0 2px 4px hsl(240 30% 2% / 0.3), 0 8px 24px hsl(15 75% 50% / 0.4);
  --shadow-toolbar: 0 1px 2px hsl(240 30% 2% / 0.25);
  --shadow-toolbar-hover: 0 2px 4px hsl(240 30% 2% / 0.3), 0 8px 32px hsl(240 30% 2% / 0.35);

  /* 层 F · 磨砂三件套（卡 24 / 浮层 36，对齐 Arc DESIGN.md 卡 blur 24 配方） */
  --backdrop-card: saturate(180%) blur(24px);
  --backdrop-float: saturate(180%) blur(36px);
}

/* arc 环境色光层：Arc 渐变温度暗版（sunset 暗桃 / 暗珊瑚 / twilight 暗紫三团 radial，
   模拟 Arc 主题色渐变透进玻璃窗）。饱和度刻意压低——安全工具的克制。 */
.dark.theme-arc body {
  background-image:
    radial-gradient(52rem 34rem at 8% -6%, hsl(11 80% 45% / 0.16), transparent 60%),
    radial-gradient(46rem 30rem at 96% 12%, hsl(350 75% 50% / 0.12), transparent 62%),
    radial-gradient(50rem 36rem at 55% 108%, hsl(258 70% 55% / 0.14), transparent 65%);
  background-attachment: fixed;
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd packages/web/frontend && npx vitest run src/styles/tokens.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/styles/tokens.css packages/web/frontend/src/styles/tokens.test.ts
git commit -m "feat(web-theme): arc 深色玻璃主题——半透表面+磨砂三件套+Arc 渐变暗版环境光层,与 mac 成玻璃对,primary 保持 coral"
```

---

### Task 3: mission 深色主题（Mission Control 指挥中心）

**Files:**
- Modify: `src/styles/tokens.css`（`.dark.theme-mission` 块）
- Test: `src/styles/tokens.test.ts`

**Interfaces:**
- Produces: CSS 类 `.dark.theme-mission`；无层 F。

- [ ] **Step 1: 写失败断言（扩展主题 describe 内追加）**

```ts
  it("mission 块：深空海军蓝 + 琥珀遥测主色 + 4px 硬朗圆角 + 深投影", () => {
    expect(tokens).toMatch(/\.dark\.theme-mission\s*\{[\s\S]*?--background:\s*223 49% 8%;/);
    expect(tokens).toMatch(/\.dark\.theme-mission\s*\{[\s\S]*?--primary:\s*43 100% 50%;/);
    expect(tokens).toMatch(/\.dark\.theme-mission\s*\{[\s\S]*?--radius:\s*4px;/);
    expect(tokens).toMatch(/\.dark\.theme-mission\s*\{[\s\S]*?--c-cyan:\s*190 100% 50%;/);
    expect(tokens).toMatch(/\.dark\.theme-mission\s*\{[\s\S]*?--c-red:\s*5\s/);
    expect(tokens).toMatch(/\.dark\.theme-mission\s*\{[\s\S]*?--c-orange:\s*24\s/);
    expect(tokens).toMatch(/\.dark\.theme-mission\s*\{[\s\S]*?--c-yellow:\s*38\s/);
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/web/frontend && npx vitest run src/styles/tokens.test.ts`
Expected: FAIL

- [ ] **Step 3: 实现 mission 块（`.dark.theme-arc body` 规则后追加）**

```css
/* Mission Control（深 · 太空任务指挥中心，2026-08-25 移植自 OpenDesign design-system-mission-control）：
   深空海军蓝 + 琥珀遥测「amber on navy」；面板分隔线哲学 + 深投影；圆角 4px 封顶
   （DESIGN.md 反模式条款：>4px 禁用）——全主题最硬朗。3 米外低光可读（AA 全过）。
   cyan 用 Accent 青 #00D4FF（GitNexus 语义天然贴）。等宽气质由现有 mono 消费点自然获得。 */
.dark.theme-mission {
  /* 层 A · shadcn token（bg #0B1120 / card #111827 / popover #1A2535 hover 面 / accent #1E3A5F active 面） */
  --background: 223 49% 8%;
  --foreground: 218 92% 95%;
  --card: 221 39% 11%;
  --card-foreground: 218 92% 95%;
  --popover: 216 34% 15%;
  --popover-foreground: 218 92% 95%;
  --primary: 43 100% 50%;
  --primary-foreground: 214 66% 7%;
  --secondary: 221 41% 15%;
  --secondary-foreground: 218 92% 95%;
  --muted: 221 41% 15%;
  --muted-foreground: 216 35% 66%;
  --accent: 214 52% 25%;
  --accent-foreground: 218 92% 95%;
  --destructive: 5 100% 64%;
  --destructive-foreground: 214 66% 7%;
  --border: 214 52% 25%;
  --input: 214 52% 25%;
  --ring: 43 100% 50%;

  /* 层 B · 语义色（cyan=Accent 青 #00D4FF；green #26DE81；severity hue 锁定） */
  --c-cyan: 190 100% 50%;
  --c-magenta: 300 60% 75%;
  --c-green: 150 74% 51%;
  --c-red: 5 100% 64%;
  --c-orange: 24 100% 63%;
  --c-yellow: 38 90% 58%;
  --c-amber: 40 95% 58%;

  /* 层 C · 圆角（功能性不友好，硬朗封顶） */
  --radius: 4px;

  /* 层 D · prose（海军蓝底 + 琥珀链接 + 青代码色） */
  --prose-body: 218 60% 90%;
  --prose-headings: 218 80% 94%;
  --prose-links: 43 100% 55%;
  --prose-bold: 218 80% 94%;
  --prose-code: 190 100% 55%;
  --prose-code-bg: 221 41% 15%;
  --prose-quotes: 216 25% 62%;
  --prose-bullets: 216 25% 62%;
  --prose-hr: 218 60% 90% / 0.12;

  /* 层 E · elevation（深投影 0 24px 72px / 0.42 + 面板分隔线内衬） */
  --shadow-card: 0 0 0 1px hsl(214 52% 40% / 0.25), 0 24px 72px hsl(223 60% 2% / 0.42);
  --shadow-cta: 0 1px 2px hsl(223 60% 2% / 0.4), 0 6px 18px -6px hsl(43 100% 40% / 0.45);
  --shadow-cta-hover: 0 2px 4px hsl(223 60% 2% / 0.4), 0 12px 26px -6px hsl(43 100% 45% / 0.55);
  --shadow-toolbar: 0 1px 2px hsl(223 60% 2% / 0.35);
  --shadow-toolbar-hover: 0 0 0 1px hsl(214 52% 40% / 0.3), 0 2px 4px hsl(223 60% 2% / 0.4), 0 24px 72px hsl(223 60% 2% / 0.42);
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd packages/web/frontend && npx vitest run src/styles/tokens.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/styles/tokens.css packages/web/frontend/src/styles/tokens.test.ts
git commit -m "feat(web-theme): mission 深色主题——Mission Control 琥珀遥测指挥中心(amber on navy/4px 硬朗/深投影)"
```

---

### Task 4: github 浅色主题（GitHub Primer）

**Files:**
- Modify: `src/styles/tokens.css`（`.light.theme-github` 块）
- Test: `src/styles/tokens.test.ts`

**Interfaces:**
- Produces: CSS 类 `.light.theme-github`。

- [ ] **Step 1: 写失败断言（扩展主题 describe 内追加）**

```ts
  it("github 块：蓝白精准 + 实色细线边框 + Primer 蓝", () => {
    expect(tokens).toMatch(/\.light\.theme-github\s*\{[\s\S]*?--border:\s*210 18% 84%;/);
    expect(tokens).toMatch(/\.light\.theme-github\s*\{[\s\S]*?--primary:\s*212 92% 45%;/);
    expect(tokens).toMatch(/\.light\.theme-github\s*\{[\s\S]*?--radius:\s*6px;/);
    expect(tokens).toMatch(/\.light\.theme-github\s*\{[\s\S]*?--c-red:\s*5\s/);
    expect(tokens).toMatch(/\.light\.theme-github\s*\{[\s\S]*?--c-yellow:\s*38\s/);
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/web/frontend && npx vitest run src/styles/tokens.test.ts`
Expected: FAIL

- [ ] **Step 3: 实现 github 块（`.light.theme-mac body` 规则后、剪枝树段前追加）**

```css
/* GitHub（浅 · Primer，2026-08-25 移植自 OpenDesign design-system-github）：
   代码优先蓝白精准——实色细线边框哲学（非 alpha hairline）、信息密度即品牌、
   阴影极轻（ring + 微影）。primary=Primer 蓝 #0969da（开发者最熟悉的交互色）。
   字体共享（system-ui 是 GitHub 哲学，气质靠色/线/密度承载）。 */
.light.theme-github {
  /* 层 A · shadcn token（纯白画布 + 白卡抬升靠边框 + subtle #f6f8fa / inset #eaeef2） */
  --background: 0 0% 100%;
  --foreground: 213 13% 14%;
  --card: 0 0% 100%;
  --card-foreground: 213 13% 14%;
  --popover: 0 0% 100%;
  --popover-foreground: 213 13% 14%;
  --primary: 212 92% 45%;
  --primary-foreground: 0 0% 100%;
  --secondary: 210 29% 97%;
  --secondary-foreground: 213 13% 14%;
  --muted: 210 29% 97%;
  --muted-foreground: 212 8% 43%;
  --accent: 210 24% 93%;
  --accent-foreground: 213 13% 14%;
  --destructive: 5 72% 47%;
  --destructive-foreground: 0 0% 100%;
  --border: 210 18% 84%;
  --input: 210 18% 84%;
  --ring: 212 92% 45%;

  /* 层 B · 语义色（green=GitHub 绿 #1a7f37；yellow=#9a6700 黄棕气质 hue 锁 38；severity hue 锁定） */
  --c-cyan: 190 65% 32%;
  --c-magenta: 278 55% 50%;
  --c-green: 137 66% 30%;
  --c-red: 5 72% 47%;
  --c-orange: 24 75% 37%;
  --c-yellow: 38 100% 30%;
  --c-amber: 36 73% 34%;

  /* 层 C · 圆角（GitHub 按钮/卡 6px） */
  --radius: 6px;

  /* 层 D · prose（GitHub 文档阅读气质：蓝链接 + 灰底代码块） */
  --prose-body: 213 13% 17%;
  --prose-headings: 213 14% 12%;
  --prose-links: 212 92% 38%;
  --prose-bold: 213 14% 12%;
  --prose-code: 190 65% 30%;
  --prose-code-bg: 210 29% 94%;
  --prose-quotes: 212 8% 40%;
  --prose-bullets: 212 8% 40%;
  --prose-hr: 213 13% 17% / 0.10;

  /* 层 E · elevation（ring + 1px 微影 + 远距柔影，无玻璃无大阴影） */
  --shadow-card: 0 0 0 1px hsl(210 18% 84%), 0 1px 3px hsl(213 13% 14% / 0.12), 0 8px 24px hsl(212 12% 32% / 0.12);
  --shadow-cta: 0 1px 0 hsl(213 13% 14% / 0.1), 0 1px 2px hsl(212 60% 25% / 0.2);
  --shadow-cta-hover: 0 1px 0 hsl(213 13% 14% / 0.1), 0 2px 6px hsl(212 60% 25% / 0.3);
  --shadow-toolbar: 0 1px 0 hsl(213 13% 14% / 0.08);
  --shadow-toolbar-hover: 0 0 0 1px hsl(210 18% 84%), 0 1px 3px hsl(213 13% 14% / 0.12), 0 8px 24px hsl(212 12% 32% / 0.16);
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd packages/web/frontend && npx vitest run src/styles/tokens.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/styles/tokens.css packages/web/frontend/src/styles/tokens.test.ts
git commit -m "feat(web-theme): github 浅色主题——Primer 蓝白精准(实色细线边框/ring 微影/信息密度气质)"
```

---

### Task 5: notion 浅色主题（Notion 暖灰）

**Files:**
- Modify: `src/styles/tokens.css`（`.light.theme-notion` 块）
- Test: `src/styles/tokens.test.ts`

**Interfaces:**
- Produces: CSS 类 `.light.theme-notion`。

- [ ] **Step 1: 写失败断言（扩展主题 describe 内追加）**

```ts
  it("notion 块：暖灰交替面 + 低语边框 + Notion 蓝 + 多层低透明阴影", () => {
    expect(tokens).toMatch(/\.light\.theme-notion\s*\{[\s\S]*?--secondary:\s*30 10% 96%;/);
    expect(tokens).toMatch(/\.light\.theme-notion\s*\{[\s\S]*?--border:\s*0 0% 0% \/ 0\.1;/);
    expect(tokens).toMatch(/\.light\.theme-notion\s*\{[\s\S]*?--primary:\s*208 100% 44%;/);
    expect(tokens).toMatch(/\.light\.theme-notion\s*\{[\s\S]*?--radius:\s*6px;/);
    expect(tokens).toMatch(/\.light\.theme-notion\s*\{[\s\S]*?--c-orange:\s*24\s/);
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/web/frontend && npx vitest run src/styles/tokens.test.ts`
Expected: FAIL

- [ ] **Step 3: 实现 notion 块（`.light.theme-github` 块后追加）**

```css
/* Notion（浅 · 暖灰极简，2026-08-25 移植自 OpenDesign design-system-notion）：
   暖中性灰（黄棕 undertone，无冷灰）+ 低语边框 rgba(0,0,0,0.1) + 多层极低透明度
   阴影叠加（每层 ≤0.05，「感受到而非看到」）。primary=Notion 蓝 #0075de。
   字体共享（真值 NotionInter 本就无衬线——衬线例外归 kami）。 */
.light.theme-notion {
  /* 层 A · shadcn token（白画布 + 暖白 #f6f5f4 交替面 + 近黑 95% 文本四级暖灰） */
  --background: 0 0% 100%;
  --foreground: 0 0% 5%;
  --card: 0 0% 100%;
  --card-foreground: 0 0% 5%;
  --popover: 0 0% 100%;
  --popover-foreground: 0 0% 5%;
  --primary: 208 100% 44%;
  --primary-foreground: 0 0% 100%;
  --secondary: 30 10% 96%;
  --secondary-foreground: 40 3% 19%;
  --muted: 30 10% 96%;
  --muted-foreground: 30 4% 36%;
  --accent: 30 8% 94%;
  --accent-foreground: 0 0% 5%;
  --destructive: 5 72% 51%;
  --destructive-foreground: 0 0% 100%;
  --border: 0 0% 0% / 0.1;
  --input: 0 0% 0% / 0.1;
  --ring: 208 100% 44%;

  /* 层 B · 语义色（green #1aae39；orange #dd5b00 25°≈24 天然合；severity hue 锁定） */
  --c-cyan: 190 65% 32%;
  --c-magenta: 278 55% 50%;
  --c-green: 133 74% 39%;
  --c-red: 5 72% 51%;
  --c-orange: 24 100% 43%;
  --c-yellow: 38 70% 29%;
  --c-amber: 36 73% 34%;

  /* 层 C · 圆角（按钮 4 / 卡 12 的折中） */
  --radius: 6px;

  /* 层 D · prose（暖墨文字 + Notion 蓝链接） */
  --prose-body: 40 3% 17%;
  --prose-headings: 40 4% 12%;
  --prose-links: 208 100% 38%;
  --prose-bold: 40 4% 12%;
  --prose-code: 190 65% 30%;
  --prose-code-bg: 30 10% 94%;
  --prose-quotes: 30 4% 40%;
  --prose-bullets: 30 4% 40%;
  --prose-hr: 0 0% 0% / 0.08;

  /* 层 E · elevation（4 层低透明度叠加 0.04/0.027/0.02/0.01，18px→1px 模糊梯度） */
  --shadow-card: 0 0 0 1px hsl(0 0% 0% / 0.06), 0 4px 18px hsl(0 0% 0% / 0.04), 0 2px 7.85px hsl(0 0% 0% / 0.027), 0 0.8px 2.93px hsl(0 0% 0% / 0.02);
  --shadow-cta: 0 1px 3px hsl(0 0% 0% / 0.04), 0 0.8px 2.93px hsl(0 0% 0% / 0.02), 0 6px 18px hsl(208 100% 30% / 0.18);
  --shadow-cta-hover: 0 1px 3px hsl(0 0% 0% / 0.04), 0 2px 7px hsl(0 0% 0% / 0.04), 0 10px 24px hsl(208 100% 32% / 0.24);
  --shadow-toolbar: 0 0.8px 2.93px hsl(0 0% 0% / 0.027);
  --shadow-toolbar-hover: 0 0 0 1px hsl(0 0% 0% / 0.06), 0 1px 3px hsl(0 0% 0% / 0.04), 0 4px 18px hsl(0 0% 0% / 0.05);
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd packages/web/frontend && npx vitest run src/styles/tokens.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/styles/tokens.css packages/web/frontend/src/styles/tokens.test.ts
git commit -m "feat(web-theme): notion 浅色主题——暖灰极简(低语边框+多层低透明度阴影+Notion 蓝)"
```

---

### Task 6: kami 浅色衬线主题（kami 纸质报告）

**Files:**
- Modify: `src/styles/tokens.css`（`.light.theme-kami` 块）
- Test: `src/styles/tokens.test.ts`

**Interfaces:**
- Produces: CSS 类 `.light.theme-kami`；**唯一**的 `--font-sans` 覆盖（衬线栈，层 C 内）。

- [ ] **Step 1: 写失败断言（扩展主题 describe 内追加）**

```ts
  it("kami 块：羊皮纸底 + 墨蓝主色 + 衬线字体覆盖（唯一例外）+ whisper 阴影", () => {
    expect(tokens).toMatch(/\.light\.theme-kami\s*\{[\s\S]*?--background:\s*53 29% 95%;/);
    expect(tokens).toMatch(/\.light\.theme-kami\s*\{[\s\S]*?--primary:\s*215 55% 24%;/);
    expect(tokens).toMatch(/\.light\.theme-kami\s*\{[\s\S]*?--radius:\s*6px;/);
    expect(tokens).toMatch(/\.light\.theme-kami\s*\{[\s\S]*?--font-sans:\s*"Charter", Georgia, Palatino, "Songti SC", "Source Han Serif SC", serif;/);
    expect(tokens).toMatch(/\.light\.theme-kami\s*\{[\s\S]*?--c-red:\s*5\s/);
    // 禁玻璃：kami 不定义 --backdrop-*
    const kamiBlock = tokens.match(/\.light\.theme-kami\s*\{([\s\S]*?)\n\}/)![1];
    expect(kamiBlock).not.toContain("--backdrop-");
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/web/frontend && npx vitest run src/styles/tokens.test.ts`
Expected: FAIL

- [ ] **Step 3: 实现 kami 块（`.light.theme-notion` 块后追加）**

```css
/* kami（浅 · 纸质报告，2026-08-25 移植自 OpenDesign design-system-kami）：
   羊皮纸 #f5f4ed 画布（绝不用纯白）+ 墨蓝 #1B365D 唯一彩色（≤5% 面积）+
   全暖灰四级文本——「高质量印刷品」而非 UI 面板，报告页白皮书阅读场景。
   材质 ring + whisper（0 4px 24px / 0.05），禁硬阴影/玻璃/渐变（DESIGN.md 反模式）。
   唯一字体覆盖主题：--font-sans 衬线栈（EN Charter/Georgia/Palatino + CN 宋体回退）。 */
.light.theme-kami {
  /* 层 A · shadcn token（parchment #f5f4ed / ivory 卡 #faf9f5 / warm sand #e8e6dc） */
  --background: 53 29% 95%;
  --foreground: 60 3% 8%;
  --card: 48 33% 97%;
  --card-foreground: 60 3% 8%;
  --popover: 48 33% 97%;
  --popover-foreground: 60 3% 8%;
  --primary: 215 55% 24%;
  --primary-foreground: 48 33% 97%;
  --secondary: 50 21% 89%;
  --secondary-foreground: 60 3% 23%;
  --muted: 50 21% 89%;
  --muted-foreground: 43 5% 30%;
  --accent: 50 18% 92%;
  --accent-foreground: 60 3% 8%;
  --destructive: 5 48% 36%;
  --destructive-foreground: 48 33% 97%;
  --border: 50 21% 89%;
  --input: 50 21% 89%;
  --ring: 215 55% 24%;

  /* 层 B · 语义色（降饱和印刷色，danger #8a3a30 hue 7°→5 微调天然合） */
  --c-cyan: 192 60% 30%;
  --c-magenta: 300 35% 42%;
  --c-green: 100 30% 32%;
  --c-red: 5 48% 36%;
  --c-orange: 24 55% 35%;
  --c-yellow: 38 63% 33%;
  --c-amber: 40 60% 34%;

  /* 层 C · 圆角 + 字体（衬线主导——唯一覆盖 --font-sans 的主题） */
  --radius: 6px;
  --font-sans: "Charter", Georgia, Palatino, "Songti SC", "Source Han Serif SC", serif;

  /* 层 D · prose（重头戏：衬线正文 + 墨蓝链接 + 暖灰引文） */
  --prose-body: 60 3% 14%;
  --prose-headings: 60 3% 10%;
  --prose-links: 215 55% 30%;
  --prose-bold: 60 3% 10%;
  --prose-code: 192 60% 28%;
  --prose-code-bg: 50 18% 92%;
  --prose-quotes: 43 5% 30%;
  --prose-bullets: 43 5% 30%;
  --prose-hr: 60 3% 8% / 0.10;

  /* 层 E · elevation（ring + whisper；禁硬阴影/玻璃/渐变） */
  --shadow-card: 0 0 0 1px hsl(50 21% 89%), 0 4px 24px hsl(0 0% 0% / 0.05);
  --shadow-cta: 0 0 0 1px hsl(215 55% 24%), 0 4px 24px hsl(0 0% 0% / 0.05);
  --shadow-cta-hover: 0 0 0 1px hsl(215 55% 24%), 0 6px 28px hsl(0 0% 0% / 0.07);
  --shadow-toolbar: 0 0 0 1px hsl(50 21% 89%);
  --shadow-toolbar-hover: 0 0 0 1px hsl(50 20% 80%), 0 4px 24px hsl(0 0% 0% / 0.06);
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd packages/web/frontend && npx vitest run src/styles/tokens.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/styles/tokens.css packages/web/frontend/src/styles/tokens.test.ts
git commit -m "feat(web-theme): kami 浅色衬线主题——羊皮纸+墨蓝唯一彩色+衬线字体覆盖(纸质报告质感)"
```

---

### Task 7: theme.ts 注册六主题

**Files:**
- Modify: `src/lib/theme.ts`（`ThemeId` 类型、`THEMES` 数组、`normalizeStored`）
- Test: `src/lib/theme.test.ts`

**Interfaces:**
- Consumes: Task 1-6 的 CSS 类 `theme-sentry/arc/mission/github/notion/kami`。
- Produces: `THEMES` 含 11 项（深组序 `charcoal,midnight,graphite,sentry,arc,mission`；浅组序 `mac,warm-paper,github,notion,kami`）；`getThemeDef("sentry"|"arc"|"mission"|"github"|"notion"|"kami")` 有效。

- [ ] **Step 1: 写失败断言（theme.test.ts 修改/追加）**

1a. 修改「THEMES 覆盖 5 主题」测试（theme.test.ts:84-89）为：

```ts
  it("THEMES 覆盖 11 主题；palette id 与 paletteClass 一一对应；浅色组默认 Mac 在前", () => {
    expect(THEMES.map((t) => t.id)).toEqual([
      "charcoal", "midnight", "graphite", "sentry", "arc", "mission",
      "mac", "warm-paper", "github", "notion", "kami",
    ]);
    expect(getThemeDef("charcoal")?.paletteClass).toBeNull();
    expect(getThemeDef("mac")?.paletteClass).toBe("theme-mac");
    expect(getThemeDef("sentry")?.paletteClass).toBe("theme-sentry");
    expect(getThemeDef("arc")?.paletteClass).toBe("theme-arc");
    expect(getThemeDef("mission")?.paletteClass).toBe("theme-mission");
    expect(getThemeDef("github")?.paletteClass).toBe("theme-github");
    expect(getThemeDef("notion")?.paletteClass).toBe("theme-notion");
    expect(getThemeDef("kami")?.paletteClass).toBe("theme-kami");
    expect(getThemeDef("system")).toBeNull();
  });
```

1b. 追加行为测试（`theme lib` describe 内）：

```ts
  it("applyTheme(sentry/arc/mission): dark + 各自 palette class", () => {
    for (const id of ["sentry", "arc", "mission"] as const) {
      applyTheme(id);
      const cl = document.documentElement.classList;
      expect(cl.contains("dark")).toBe(true);
      expect(cl.contains(`theme-${id}`)).toBe(true);
    }
  });

  it("applyTheme(github/notion/kami): light + 各自 palette class", () => {
    for (const id of ["github", "notion", "kami"] as const) {
      applyTheme(id);
      const cl = document.documentElement.classList;
      expect(cl.contains("light")).toBe(true);
      expect(cl.contains(`theme-${id}`)).toBe(true);
    }
  });

  it("getInitialTheme: 新主题 id stored 原样读出", () => {
    localStorage.setItem(THEME_KEY, "kami");
    expect(getInitialTheme()).toBe("kami");
    localStorage.setItem(THEME_KEY, "mission");
    expect(getInitialTheme()).toBe("mission");
  });

  it("resolveEffectiveTheme: 新主题查 def.mode 正确", () => {
    expect(resolveEffectiveTheme("sentry")).toBe("dark");
    expect(resolveEffectiveTheme("arc")).toBe("dark");
    expect(resolveEffectiveTheme("mission")).toBe("dark");
    expect(resolveEffectiveTheme("github")).toBe("light");
    expect(resolveEffectiveTheme("notion")).toBe("light");
    expect(resolveEffectiveTheme("kami")).toBe("light");
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/web/frontend && npx vitest run src/lib/theme.test.ts`
Expected: FAIL（THEMES 数量断言、新 applyTheme 断言失败）

- [ ] **Step 3: 实现 theme.ts**

3a. `ThemeId` 类型（theme.ts:12）改为：

```ts
export type ThemeId = "system" | "charcoal" | "warm-paper" | "mac" | "midnight" | "graphite" | "sentry" | "arc" | "mission" | "github" | "notion" | "kami";
```

3b. `THEMES` 数组：graphite 项后追加 3 个深色项、warm-paper 项后追加 3 个浅色项：

```ts
  {
    id: "sentry",
    mode: "dark",
    paletteClass: "theme-sentry",
    preview: { bg: "hsl(258 40% 10%)", card: "hsl(259 40% 14%)", primary: "hsl(247 44% 56%)", border: "hsl(252 33% 26%)" },
  },
  {
    id: "arc",
    mode: "dark",
    paletteClass: "theme-arc",
    preview: { bg: "hsl(240 13% 9%)", card: "hsl(240 13% 15%)", primary: "hsl(15 60% 56%)", border: "hsl(0 0% 100% / 0.20)" },
  },
  {
    id: "mission",
    mode: "dark",
    paletteClass: "theme-mission",
    preview: { bg: "hsl(223 49% 8%)", card: "hsl(221 39% 11%)", primary: "hsl(43 100% 50%)", border: "hsl(214 52% 25%)" },
  },
```

```ts
  {
    id: "github",
    mode: "light",
    paletteClass: "theme-github",
    preview: { bg: "hsl(0 0% 100%)", card: "hsl(210 29% 97%)", primary: "hsl(212 92% 45%)", border: "hsl(210 18% 84%)" },
  },
  {
    id: "notion",
    mode: "light",
    paletteClass: "theme-notion",
    preview: { bg: "hsl(0 0% 100%)", card: "hsl(30 10% 96%)", primary: "hsl(208 100% 44%)", border: "hsl(0 0% 0% / 0.10)" },
  },
  {
    id: "kami",
    mode: "light",
    paletteClass: "theme-kami",
    preview: { bg: "hsl(53 29% 95%)", card: "hsl(48 33% 97%)", primary: "hsl(215 55% 24%)", border: "hsl(50 21% 89%)" },
  },
```

3c. `normalizeStored`（theme.ts:87-105）switch 增 6 个 case（与既有显式合法值同格式）：

```ts
    case "sentry":
    case "arc":
    case "mission":
    case "github":
    case "notion":
    case "kami":
```

加在现有 `case "graphite":` 之后、`return v;` 之前的位置（即把这些 case 与既有合法值并列返回 `v`）。

3d. 头部注释（theme.ts:1-9）末尾补一行：

```ts
   2026-08-25 扩展至 11 主题：+sentry/arc/mission（深）+ github/notion/kami（浅），
   移植自 OpenDesign DESIGN.md（spec 2026-08-25-theme-expansion）。 */
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd packages/web/frontend && npx vitest run src/lib/theme.test.ts`
Expected: PASS（含新增 4 个测试）

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/lib/theme.ts packages/web/frontend/src/lib/theme.test.ts
git commit -m "feat(web-theme): theme.ts 注册六新主题——ThemeId/THEMES/normalizeStored,11 主题格局"
```

---

### Task 8: i18n 标签 + SettingsPage 渲染

**Files:**
- Modify: `src/locales/zh.json` / `src/locales/en.json`（`settings.themes` 各 +6 key）
- Modify: `src/pages/SettingsPage.test.tsx`（cleanup palette class 列表 + 渲染断言）
- Modify: `src/pages/SettingsPage.tsx`（**预期零改动**——已是 `grid grid-cols-3`，11 主题深 6 浅 5 各 2 行；仅当视觉走查发现标签挤压时才降 `grid-cols-2`）

**Interfaces:**
- Consumes: Task 7 的 `THEMES`（11 项）。SettingsPage 按组渲染 `THEMES.filter(t => t.mode === "dark"|"light")` 的现有逻辑不变。
- Produces: i18n key `settings.themes.sentry/arc/mission/github/notion/kami`（camelCase 由 kebab id 自动拼出——单词 id 无连字符，key 即 id 本身）。

- [ ] **Step 1: 写失败断言（SettingsPage.test.tsx）**

1a. 扩 cleanup（SettingsPage.test.tsx:60 现有 `classList.remove(...)` 行）为：

```ts
  document.documentElement.classList.remove(
    "dark", "light", "theme-mac", "theme-midnight", "theme-graphite",
    "theme-sentry", "theme-arc", "theme-mission", "theme-github", "theme-notion", "theme-kami",
  );
```

1b. 追加渲染测试（放现有「主题选择器」describe 内；与文件现有模式一致——`renderWithTheme` / `findByText("个人化")` / `fireEvent.click`）：

```ts
  it("主题选择器：新六主题全部渲染 + 点 kami → light + theme-kami", async () => {
    renderWithTheme(<SettingsPage />);
    await screen.findByText("个人化");
    for (const label of ["Sentry 紫黑", "Arc 玻璃", "指挥中心", "GitHub", "Notion 暖灰", "kami 纸质"]) {
      expect(screen.getByRole("button", { name: new RegExp(label) })).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole("button", { name: /kami 纸质/ }));
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(document.documentElement.classList.contains("theme-kami")).toBe(true);
    expect(localStorage.getItem("supernova-theme")).toBe("kami");
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd packages/web/frontend && npx vitest run src/pages/SettingsPage.test.tsx`
Expected: FAIL（标签缺失：i18n key 未加）

- [ ] **Step 3: 实现 i18n**

zh.json `settings.themes` 对象追加：

```json
"sentry": "Sentry 紫黑",
"arc": "Arc 玻璃",
"mission": "指挥中心",
"github": "GitHub",
"notion": "Notion 暖灰",
"kami": "kami 纸质"
```

en.json `settings.themes` 对象追加：

```json
"sentry": "Sentry Purple",
"arc": "Arc Glass",
"mission": "Mission Control",
"github": "GitHub",
"notion": "Notion Warm",
"kami": "kami Paper"
```

（JSON 对象内逗号规则：加在现有最后一项 `"mac": "Mac"` 后，注意补前一项的尾逗号。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd packages/web/frontend && npx vitest run src/pages/SettingsPage.test.tsx`
Expected: PASS（含新增 11 主题测试；SettingsPage.tsx 零改动）

- [ ] **Step 5: Commit**

```bash
git add packages/web/frontend/src/locales/zh.json packages/web/frontend/src/locales/en.json packages/web/frontend/src/pages/SettingsPage.test.tsx
git commit -m "feat(web-theme): 六新主题 i18n 标签(中/英)+SettingsPage 11 主题渲染测试;页面零改动(grid-cols-3 自动容纳)"
```

---

### Task 9: 全量回归 + 视觉走查

**Files:**
- Test: 5 个主题相关测试文件全跑
- 无新增代码（若走查发现标签挤压：`src/pages/SettingsPage.tsx` 两组 `grid-cols-3` → `grid-cols-2` 并补 commit）

**Interfaces:**
- Consumes: Task 1-8 全部产出。

- [ ] **Step 1: 主题相关测试全量回归**

Run: `cd packages/web/frontend && npx vitest run src/lib/theme.test.ts src/lib/theme-context.test.tsx src/styles/tokens.test.ts src/pages/SettingsPage.test.tsx src/components/layout/ThemeToggle.test.tsx`
Expected: 全部 PASS

- [ ] **Step 2: 类型检查**

Run: `cd packages/web/frontend && npx tsc --noEmit`
Expected: 无错误（`ThemeId` 扩展可能暴露 switch 穷尽性或字面量类型问题，如有则修复）

- [ ] **Step 3: 视觉走查（dev 服务器）**

Run: `cd packages/web/frontend && npx vite dev`（或项目现有 dev 命令），浏览器逐主题切换 11 主题走查：
- Dashboard / 工作区详情 / 设置页：无破版、无不可读文本（AA）
- kami：报告页 prose 衬线生效、中文回退宋体
- arc：卡片磨砂透出环境光、popover 浮层玻璃
- mission：4px 硬朗圆角、琥珀按钮在深海军底上可读
- 若设置页 3 列网格标签挤压 → 改 `grid-cols-2` 并补 commit（`fix(web-theme): 设置页主题网格 3→2 列防标签挤压`）

- [ ] **Step 4: 最终 Commit（如有走查修复）**

```bash
git add -A packages/web/frontend
git commit -m "fix(web-theme): 视觉走查修复(如有)"
```

（无修复则跳过此步。）
