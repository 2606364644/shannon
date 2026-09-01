/* 主题模型：一维 ThemeId（每主题自带 mode + palette class）。
   html class 挂两层：mode class（dark|light，驱动 tailwind dark: variant 与
   report.css .light hljs 覆盖）+ 可选 palette class（theme-*，在 tokens.css
   内以 .dark.theme-midnight / .light.theme-mac 双类选择器覆盖 token，
   特异性 (0,2,0) 严格高于 :root/.light 单类，不依赖源顺序）。
   localStorage 旧值 "dark"/"light" 读时映射为 "charcoal"/"warm-paper"；
   "frost"（霜白，2026-08-24 改名 Mac）读时映射为 "mac"（只读时归一，不回写）。
   默认主题对（2026-08-24）：浅色=Mac、深色=Claude 深色（charcoal）——首次访问按
   OS 偏好落这一对，system 态与快捷翻转（oppositeBaseTheme）同源。
   2026-08-27 默认浅色回切 warm-paper：mac 果味修订（Apple 蓝主色）后，默认主题
   保留品牌 coral（用户决策）——mac 成为纯可选的果味主题，brand 基准回到
   warm-paper/charcoal 一对（spec 2026-08-27-mac-theme-apple-flavor）。
   2026-08-27（同日再调，用户决策）默认对改为：深色=graphite（近黑工作室）、
   浅色=openai（近单色研究室）。charcoal/warm-paper 仍是 token 基准对
   （:root/.light 基础块 + THEMES 组首），但不再是默认——「默认」单源于
   defaultThemeFor，与排序解绑。
   2026-08-25 扩展至 11 主题：+sentry/arc/mission（深）+ github/notion/kami（浅），
   移植自 OpenDesign DESIGN.md（spec 2026-08-25-theme-expansion）。
   2026-08-26 亮色材质升级至 12 主题：warm-paper 获得 paletteClass（材质专用块
   .light.theme-warm-paper 挂画布纸纹，色 token 仍单源于 .light 基础块）+
   新增 blueprint（浅 · 白盒蓝图——绘图网格画布，spec 2026-08-26-light-theme-material）。
   2026-08-27 增至 13 主题：+openai（浅 · OpenAI 近单色研究室——纯白画布 +
   深青黑墨色 + 墨黑主 CTA（teal 仅焦点/链接/成功），移植自 OpenDesign
   design-system-openai，spec 2026-08-27-openai-theme）。
   2026-08-31 增至 14 主题：+ember（深 · 余烬——暖褐炉膛 + coral 火种 + 底缘
   余烬辉光；暗色组唯一显性暖调，补「蓝色少一点」谱系空缺）。
   2026-08-31（同日二扩）增至 18 主题：+catppuccin / rose-pine / gruvbox /
   dracula（用户裁定 4 主流编辑器主题全上——预览页对照后判定「都挺有特色」；
   暗色组 11 款，走气质层路线 primary 用主题本色，sentry/mission 先例）。
   2026-08-31（同日三扩）增至 22 主题：+catppuccin-latte / rose-pine-dawn /
   gruvbox-light / solarized-light（亮色四款——前三为暗色四款的官方成对
   亮色 flavor + Solarized Light 经典；用户预览 theme-preview-light.html
   裁定全上；浅色组 11 款）。 */

export type ThemeMode = "dark" | "light";
export type ThemeId = "system" | "charcoal" | "warm-paper" | "mac" | "midnight" | "graphite" | "sentry" | "arc" | "mission" | "ember" | "catppuccin" | "rose-pine" | "gruvbox" | "dracula" | "github" | "notion" | "kami" | "blueprint" | "openai" | "catppuccin-latte" | "rose-pine-dawn" | "gruvbox-light" | "solarized-light";
/** @deprecated 语义由 ThemeMode 取代；保留别名避免存量导入破坏。 */
export type EffectiveTheme = ThemeMode;
export type Theme = ThemeId;
export const THEME_KEY = "supernova-theme";

const MQ = "(prefers-color-scheme: light)";

export interface ThemeDef {
  id: Exclude<ThemeId, "system">;
  mode: ThemeMode;
  /** tokens.css 的 palette 覆盖 class；null = 基础主题（:root 深默认 / .light 浅默认）。 */
  paletteClass: string | null;
  /** SettingsPage 色卡预览：硬编码 hsl 真值（与 tokens.css 对应块同步维护），
      不消费 CSS var —— var 随当前主题变，色卡必须恒定展示各主题本色。 */
  preview: { bg: string; card: string; primary: string; border: string };
}

/** 全部主题：深色组在前、浅色组在后（SettingsPicker 按组分块渲染；
    各组以基础主题对排首——charcoal/warm-paper = Claude 风深/浅基础主题
    （tokens.css :root/.light 精确对齐 claude.ai 真值色）。排序表达「基准」，
    不表达「默认」——默认对（2026-08-27：graphite/openai）见 defaultThemeFor。 */
export const THEMES: readonly ThemeDef[] = [
  {
    id: "charcoal",
    mode: "dark",
    paletteClass: null,
    preview: { bg: "hsl(60 3% 15%)", card: "hsl(60 3% 18%)", primary: "hsl(15 60% 56%)", border: "hsl(36 10% 90% / 0.10)" },
  },
  {
    id: "midnight",
    mode: "dark",
    paletteClass: "theme-midnight",
    // 2026-09-02 靛蓝显化：底 sat 20→28（深区 gamma 压扁靛蓝感，与 graphite 难分）
    preview: { bg: "hsl(230 28% 8%)", card: "hsl(230 25% 11%)", primary: "hsl(16 70% 62%)", border: "hsl(230 30% 80% / 0.14)" },
  },
  {
    id: "graphite",
    mode: "dark",
    paletteClass: "theme-graphite",
    preview: { bg: "hsl(0 0% 5%)", card: "hsl(0 0% 8%)", primary: "hsl(15 62% 56%)", border: "hsl(0 0% 100% / 0.12)" },
  },
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
  {
    id: "ember",
    mode: "dark",
    paletteClass: "theme-ember",
    // 2026-08-31 余烬（2026-09-02 重做）：暖褐炉膛显化（20°/16%）+ 炭火橙 primary（28°，
    // 气质层本色）+ 底缘余烬辉光——暗色组唯一显性暖调，与 charcoal 拉开色温族
    preview: { bg: "hsl(20 16% 9%)", card: "hsl(20 14% 12%)", primary: "hsl(28 72% 58%)", border: "hsl(20 16% 88% / 0.12)" },
  },
  {
    id: "catppuccin",
    mode: "dark",
    paletteClass: "theme-catppuccin",
    // 2026-08-31 Catppuccin Mocha：柔紫粉彩，primary=mauve 薰衣草（气质层）
    preview: { bg: "hsl(243 21% 15%)", card: "hsl(234 17% 23%)", primary: "hsl(267 84% 81%)", border: "hsl(230 15% 60% / 0.22)" },
  },
  {
    id: "rose-pine",
    mode: "dark",
    paletteClass: "theme-rose-pine",
    // 2026-08-31 Rosé Pine：夜紫墨 + 玫瑰金 primary（辨识核心）
    preview: { bg: "hsl(249 22% 12%)", card: "hsl(247 23% 15%)", primary: "hsl(2 55% 83%)", border: "hsl(246 25% 50% / 0.25)" },
  },
  {
    id: "gruvbox",
    mode: "dark",
    paletteClass: "theme-gruvbox",
    // 2026-08-31 Gruvbox：亮暗色档（L16%）复古棕 + 亮黄 primary
    preview: { bg: "hsl(0 0% 16%)", card: "hsl(20 6% 22%)", primary: "hsl(42 95% 58%)", border: "hsl(40 15% 70% / 0.16)" },
  },
  {
    id: "dracula",
    mode: "dark",
    paletteClass: "theme-dracula",
    // 2026-08-31 Dracula：紫蓝灰 + 多巴胺高饱和，primary=purple
    preview: { bg: "hsl(231 15% 18%)", card: "hsl(230 15% 24%)", primary: "hsl(265 90% 78%)", border: "hsl(226 30% 55% / 0.30)" },
  },
  {
    id: "warm-paper",
    mode: "light",
    // 2026-08-26 亮色材质升级：材质专用 palette class（只挂 --canvas-material 纸纹；
    // 色 token 仍单源于 tokens.css .light 基础块，本项 preview 与其同步维护）。
    // 2026-08-27 起为默认浅色主题（mac 果味修订后 brand 基准回位）
    paletteClass: "theme-warm-paper",
    preview: { bg: "hsl(48 33% 97%)", card: "hsl(0 0% 100%)", primary: "hsl(15 58% 50%)", border: "hsl(40 8% 17% / 0.10)" },
  },
  {
    id: "mac",
    mode: "light",
    paletteClass: "theme-mac",
    // 2026-08-27 果味修订：画布蓝饱和回 #F2F2F7 真值、primary 换 apple.com CTA 蓝（与 tokens.css mac 块同步）
    preview: { bg: "hsl(240 24% 96%)", card: "hsl(0 0% 100%)", primary: "hsl(211 100% 45%)", border: "hsl(240 6% 10% / 0.14)" },
  },
  {
    id: "github",
    mode: "light",
    paletteClass: "theme-github",
    // 2026-09-02 primary coral 赤褐→Primer 蓝（系统对齐层本色纪律，mac 先例；与 notion 撞款解）
    preview: { bg: "hsl(0 0% 100%)", card: "hsl(210 29% 97%)", primary: "hsl(212 92% 44%)", border: "hsl(210 18% 84%)" },
  },
  {
    id: "notion",
    mode: "light",
    paletteClass: "theme-notion",
    preview: { bg: "hsl(0 0% 100%)", card: "hsl(30 10% 96%)", primary: "hsl(14 58% 46%)", border: "hsl(0 0% 0% / 0.10)" },
  },
  {
    id: "kami",
    mode: "light",
    paletteClass: "theme-kami",
    // 2026-08-26 材质补课：画布 95→93 / sand ring 89→86（与 tokens.css kami 块同步）
    preview: { bg: "hsl(52 30% 93%)", card: "hsl(48 33% 97%)", primary: "hsl(10 52% 40%)", border: "hsl(50 22% 86%)" },
  },
  {
    id: "blueprint",
    mode: "light",
    paletteClass: "theme-blueprint",
    preview: { bg: "hsl(214 40% 97%)", card: "hsl(0 0% 100%)", primary: "hsl(224 58% 34%)", border: "hsl(215 25% 84%)" },
  },
  {
    id: "openai",
    mode: "light",
    paletteClass: "theme-openai",
    // 2026-08-27 OpenDesign design-system-openai 移植：纯白画布 / 珍珠次级面 /
    // 主 CTA 墨黑 #0d0d0d（DESIGN.md 主要按钮档，teal 仅焦点/链接/成功）/ 细线
    // #e5e5e5（与 tokens.css openai 块同步维护）
    preview: { bg: "hsl(0 0% 100%)", card: "hsl(0 0% 96%)", primary: "hsl(0 0% 5%)", border: "hsl(0 0% 90%)" },
  },
  {
    id: "catppuccin-latte",
    mode: "light",
    paletteClass: "theme-catppuccin-latte",
    // 2026-08-31 Catppuccin Latte：catppuccin(Mocha) 的官方亮色对，mauve 深紫 primary
    preview: { bg: "hsl(220 23% 95%)", card: "hsl(220 26% 98%)", primary: "hsl(266 85% 58%)", border: "hsl(229 12% 60% / 0.25)" },
  },
  {
    id: "rose-pine-dawn",
    mode: "light",
    paletteClass: "theme-rose-pine-dawn",
    // 2026-08-31 Rosé Pine Dawn：rose-pine 的官方亮色对，米杏底 + 玫瑰金 primary
    preview: { bg: "hsl(33 57% 95%)", card: "hsl(24 100% 98%)", primary: "hsl(343 35% 55%)", border: "hsl(20 25% 55% / 0.25)" },
  },
  {
    id: "gruvbox-light",
    mode: "light",
    paletteClass: "theme-gruvbox-light",
    // 2026-08-31 Gruvbox Light：gruvbox 的官方亮色对，奶油黄底（深度画布档）+ 琥珀 primary 深字
    preview: { bg: "hsl(48 87% 88%)", card: "hsl(45 50% 95%)", primary: "hsl(40 69% 49%)", border: "hsl(40 20% 40% / 0.18)" },
  },
  {
    id: "solarized-light",
    mode: "light",
    paletteClass: "theme-solarized-light",
    // 2026-08-31 Solarized Light：米黄底 + 青灰正文（独有气质）+ 压深 blue primary
    preview: { bg: "hsl(44 87% 94%)", card: "hsl(46 55% 93%)", primary: "hsl(205 70% 42%)", border: "hsl(40 25% 45% / 0.20)" },
  },
];

const PALETTE_CLASSES = THEMES.map((t) => t.paletteClass).filter((c): c is string => c !== null);

export function getThemeDef(id: ThemeId): ThemeDef | null {
  if (id === "system") return null;
  return THEMES.find((t) => t.id === id) ?? null;
}

/** 各 mode 的默认主题（2026-08-27 用户决策：深色=graphite 近黑工作室 / 浅色=openai
    近单色研究室；同日早先为 charcoal/warm-paper 品牌对）。首次访问无 stored、
    system 态解析、快捷翻转三处共用这一对。 */
export function defaultThemeFor(mode: ThemeMode): "openai" | "graphite" {
  return mode === "light" ? "openai" : "graphite";
}

/** 快捷翻转的目标：对侧 mode 的默认主题（dark→openai / light→graphite）。
    palette 主题（midnight/mac 等）无对侧变体，一律翻到对侧默认。 */
export function oppositeBaseTheme(mode: ThemeMode): Exclude<ThemeId, "system"> {
  return defaultThemeFor(mode === "dark" ? "light" : "dark");
}

/** 存储值归一：旧 "dark"/"light" → "charcoal"/"warm-paper"；"frost"（改名前旧 id）
    → "mac"；非法值 → null。导出供 theme-context 校准后端 user.theme 时复用（2026-08-28
    per-user 主题：后端白名单挡写入，此处归一是脏值防御层）。 */
export function normalizeStored(v: string | null): ThemeId | null {
  switch (v) {
    case "dark":
      return "charcoal";
    case "light":
      return "warm-paper";
    case "frost":
      return "mac";
    case "system":
    case "charcoal":
    case "warm-paper":
    case "mac":
    case "midnight":
    case "graphite":
    case "sentry":
    case "arc":
    case "mission":
    case "ember":
    case "catppuccin":
    case "rose-pine":
    case "gruvbox":
    case "dracula":
    case "github":
    case "notion":
    case "kami":
    case "blueprint":
    case "openai":
    case "catppuccin-latte":
    case "rose-pine-dawn":
    case "gruvbox-light":
    case "solarized-light":
      return v;
    default:
      return null;
  }
}

export function getInitialTheme(): ThemeId {
  if (typeof window === "undefined") return defaultThemeFor("dark");
  const stored = normalizeStored(localStorage.getItem(THEME_KEY));
  if (stored) return stored;
  return defaultThemeFor(window.matchMedia(MQ).matches ? "light" : "dark");
}

/** 解析 theme 的实际渲染 mode：system → 读 prefers-color-scheme；显式主题查 def.mode。 */
export function resolveEffectiveTheme(theme: ThemeId): ThemeMode {
  if (theme === "system") {
    if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
      return window.matchMedia(MQ).matches ? "light" : "dark";
    }
    return "dark";
  }
  return getThemeDef(theme)?.mode ?? "dark";
}

function applyClass(mode: ThemeMode, paletteClass: string | null): void {
  const root = document.documentElement;
  root.classList.remove("dark", "light", ...PALETTE_CLASSES);
  root.classList.add(mode);
  if (paletteClass) root.classList.add(paletteClass);
}

// system 监听单例：applyTheme 切到 system 时注册、切回显式态时清理，避免重复注册 / 泄漏。
let systemMql: MediaQueryList | null = null;
let systemListener: ((e: { matches: boolean }) => void) | null = null;

function detachSystemListener(): void {
  if (systemMql && systemListener) {
    systemMql.removeEventListener("change", systemListener);
  }
  systemMql = null;
  systemListener = null;
}

function attachSystemListener(): void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
  systemMql = window.matchMedia(MQ);
  // system 态 = 跟随系统用「该 mode 的默认主题」（light→openai / dark→graphite）。
  systemListener = (e) => {
    const mode = e.matches ? "light" : "dark";
    applyClass(mode, getThemeDef(defaultThemeFor(mode))?.paletteClass ?? null);
  };
  systemMql.addEventListener("change", systemListener);
}

export function applyTheme(t: ThemeId): void {
  localStorage.setItem(THEME_KEY, t);
  detachSystemListener();
  if (t === "system") {
    const mode = resolveEffectiveTheme("system");
    applyClass(mode, getThemeDef(defaultThemeFor(mode))?.paletteClass ?? null);
    attachSystemListener();
  } else {
    const def = getThemeDef(t);
    if (def) applyClass(def.mode, def.paletteClass);
  }
}
