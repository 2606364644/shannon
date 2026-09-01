import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { AlertTriangle, LayoutTemplate } from "lucide-react";
import { useAuth } from "@/auth/AuthContext";
import { getWsConfig, putWsConfig, type WsConfigWarnings } from "@/api/wsConfig";
import { getMembers } from "@/api/members";
import type { Member } from "@/api/members";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import WsPricingCard from "@/components/pricing/WsPricingCard";

const PLACEHOLDER = [
  "SUPERNOVA_AI_PROVIDER=openai_compatible",
  "SUPERNOVA_OPENAI_API_KEY=填入你的 API key",
  "SUPERNOVA_OPENAI_BASE_URL=https://llm-proxy.futuoa.com/v1",
  "SUPERNOVA_OPENAI_LARGE_MODEL=glm-5.2-coder",
  "SUPERNOVA_OPENAI_MEDIUM_MODEL=glm-5.2-coder",
  "SUPERNOVA_OPENAI_SMALL_MODEL=glm-5.2-coder",
].join("\n");

type KeyKind = "str" | "int" | "float" | "bool";
interface CfgKey {
  key: string;
  kind: KeyKind;
  // 注入到左侧编辑框时使用的默认值；凭据类留空字符串（等用户填值）。
  defaultValue: string;
  // 空值键不一定是凭据（如 MODEL_CONTEXT_OVERRIDE）；凭据用显式标记打点。
  credential?: boolean;
}
interface CfgGroup {
  titleKey: string;
  keys: CfgKey[];
  // 是否进入默认预填模板（is_default 时自动预填）。git 段默认不预填——多数扫描不依赖 GitLab，
  // 需要时可在右侧词典点击注入；词典渲染不受此标记影响（EFFECTIVE_GROUPS.map 仍渲染全部组）。
  prefill?: boolean;
}

// 生效类：ws 级覆盖真生效（存 config.yaml）。与后端 ENV_TO_FIELD + SCAN_ENV_KEYS 对齐。
// 默认值与后端 ws_config_store.DEFAULT_WS_* 对齐；凭据类（API key / token）留空等用户填。
const EFFECTIVE_GROUPS: CfgGroup[] = [
  {
    titleKey: "wsConfig.keys.groups.engine",
    keys: [
      { key: "SUPERNOVA_AI_PROVIDER", kind: "str", defaultValue: "openai_compatible" },
      { key: "SUPERNOVA_OPENAI_BASE_URL", kind: "str", defaultValue: "https://llm-proxy.futuoa.com/v1" },
      { key: "SUPERNOVA_OPENAI_API_KEY", kind: "str", defaultValue: "", credential: true },
    ],
  },
  {
    titleKey: "wsConfig.keys.groups.models",
    keys: [
      { key: "SUPERNOVA_MODEL", kind: "str", defaultValue: "glm-5.2-coder" },
      { key: "SUPERNOVA_OPENAI_SMALL_MODEL", kind: "str", defaultValue: "glm-5.2-coder" },
      { key: "SUPERNOVA_OPENAI_MEDIUM_MODEL", kind: "str", defaultValue: "glm-5.2-coder" },
      { key: "SUPERNOVA_OPENAI_LARGE_MODEL", kind: "str", defaultValue: "glm-5.2-coder" },
    ],
  },
  {
    titleKey: "wsConfig.keys.groups.runtime",
    keys: [
      { key: "SUPERNOVA_MAX_TURNS", kind: "int", defaultValue: "10000" },
      // 2026-09-01 默认 false（用户裁定「默认不开 think」）：推理快照模型
      // （deepseek-v4-flash-0731 等）默认开 thinking 且 reasoning 计入
      // completion——chain verdict 单链 133s 撑爆 15min 窗口
      // （NodeGoat-20260901-015018）；关后单轮 19s→7s。两引擎同字段同语义
      // （False=显式禁用；providers_openai client 包装层全请求注入）。
      { key: "SUPERNOVA_ADAPTIVE_THINKING", kind: "bool", defaultValue: "false" },
    ],
  },
  {
    titleKey: "wsConfig.keys.groups.scanSwitches",
    keys: [
      { key: "SUPERNOVA_LLM_TRACK_ENABLED", kind: "bool", defaultValue: "1" },
      { key: "SUPERNOVA_GITNEXUS_LLM_ENABLED", kind: "bool", defaultValue: "0" },
      { key: "SUPERNOVA_BROWSER_ENGINE", kind: "str", defaultValue: "agent-browser" },
      // 2026-08-31 准入（白名单+词典同步）：接口富化开关 = 工作区预算×质量取舍。
      // GN 富化档位 SUPERNOVA_GN_ENRICH_MODE 同日整键移除（off/light/deep 精简为
      // deep 常开，后端读取点已删）→ 词典不再展示；运维参数（TRANSIENT_RETRIES
      // 等）按「全局配置走全局 .env」原则不进词典。
      { key: "SUPERNOVA_ENDPOINT_ENRICH_ENABLED", kind: "bool", defaultValue: "1" },
      { key: "SUPERNOVA_AGENT_NARRATION_LANG", kind: "str", defaultValue: "zh" },
      // PRICING_OVERRIDE 已移出词典/模板（2026-08-31）：它是定价四层链的最高层
      // （工作区层），模板预填/词典推荐会把 profile 定价钉死进工作区——web 全局
      // 定价界面对该工作区接管失效。per-ws 定价差异走 WsPricingCard
      // （pricing.override.json）；后端 SCAN_ENV_KEYS 仍收该键（手写向后兼容）。
    ],
  },
  {
    titleKey: "wsConfig.keys.groups.advanced",
    // 高级调参默认不进模板：预填会把全局运维值钉死成工作区值；需要时点击注入。
    prefill: false,
    keys: [
      { key: "SUPERNOVA_LLM_PER_CALL_TIMEOUT", kind: "float", defaultValue: "60" },
      { key: "SUPERNOVA_CHUNK_MAX_CALLS", kind: "int", defaultValue: "100" },
      { key: "SUPERNOVA_MODEL_CONTEXT_OVERRIDE", kind: "str", defaultValue: "" },
      { key: "SUPERNOVA_CHUNK_TOKEN_THRESHOLD", kind: "int", defaultValue: "" },
      { key: "SUPERNOVA_CHAIN_VERDICT_CONCURRENCY", kind: "int", defaultValue: "4" },
      { key: "SUPERNOVA_AUTH_VALIDATION_TIMEOUT_SECONDS", kind: "int", defaultValue: "600" },
    ],
  },
  {
    titleKey: "wsConfig.keys.groups.git",
    // 默认不进入预填模板（多数扫描不依赖 GitLab）；词典仍显示，需要时点击注入。
    prefill: false,
    keys: [
      { key: "GITLAB_USER", kind: "str", defaultValue: "" },
      { key: "GITLAB_TOKEN", kind: "str", defaultValue: "", credential: true },
    ],
  },
];

// 启动期：worker main() 启动时读一次，ws 覆盖不生效（需全局配）。
// 后端 INEFFECTIVE_KEYS（ws_env_codec）仍含 CLAUDE_CODE_MAX_OUTPUT_TOKENS——用户误写入
// env_text 时警告兜底，但词典不再展示：后端代码默认 64000（providers_anthropic）对全部
// 在用模型安全（最小上限 GLM-4.5-Air 96K），无工作区配置价值，展示反而误导（旧标签 32000）。
const PROCESS_KEYS: CfgKey[] = [
  { key: "SUPERNOVA_MAX_CONCURRENT", kind: "str", defaultValue: "4" },
];

// 推荐模板：遍历生效配置组（prefill!==false），非凭据/空值键填真实默认值（保存即生效），
// 凭据类（defaultValue=""）用 # 注释行（不落盘空串、用户删 # 填值才生效）。
// 不含：进程级键（PROCESS_KEYS，ws 不生效）+ git 段（prefill=false，多数扫描不依赖 GitLab）。
// 词典渲染仍显示全部组（EFFECTIVE_GROUPS.map），需要时点击注入。
// includeKey 给定时只收该函数放行的 key（模板注入时跳过文本区已有键）；全部被滤掉返回 ""。
function buildDefaultTemplate(t: (k: string) => string, includeKey?: (key: string) => boolean): string {
  const blocks = EFFECTIVE_GROUPS.filter((g) => g.prefill !== false).map((g) => {
    const keys = includeKey ? g.keys.filter((k) => includeKey(k.key)) : g.keys;
    if (!keys.length) return null;
    const lines = [`# --- ${t(g.titleKey)} ---`];
    for (const k of keys) {
      lines.push(k.defaultValue === "" ? `#${k.key}=` : `${k.key}=${k.defaultValue}`);
    }
    return lines.join("\n");
  }).filter((b): b is string => b !== null);
  return blocks.length ? blocks.join("\n\n") + "\n" : "";
}

function kindColor(kind: KeyKind): string | undefined {
  if (kind === "int" || kind === "float") return "hsl(var(--c-cyan))";
  if (kind === "bool") return "hsl(var(--c-green))";
  return undefined; // str 走 muted
}

function KeyRow({
  cfgKey,
  processLevel,
  onInject,
}: {
  cfgKey: CfgKey;
  processLevel?: boolean;
  onInject: (k: CfgKey) => void;
}) {
  const isCredential = cfgKey.credential === true;
  return (
    <li>
      <button
        type="button"
        onClick={() => onInject(cfgKey)}
        title={`${cfgKey.key}=${cfgKey.defaultValue}`}
        className="group flex w-full items-center gap-2 rounded-md px-2 py-1 text-left transition-colors hover:bg-accent"
      >
        <span
          className="truncate font-mono text-xs"
          style={processLevel ? { color: "hsl(var(--c-amber))" } : undefined}
        >
          {cfgKey.key}
        </span>
        {isCredential && (
          <span
            className="shrink-0 font-mono text-[10px]"
            style={{ color: "hsl(var(--c-amber))" }}
          >
            ·
          </span>
        )}
        <span
          className="ml-auto shrink-0 font-mono text-[10px] uppercase tracking-wide text-muted-foreground"
          style={kindColor(cfgKey.kind) ? { color: kindColor(cfgKey.kind) } : undefined}
        >
          {cfgKey.kind}
        </span>
      </button>
    </li>
  );
}

export default function WsSettingsTab() {
  const { workspace: ws = "" } = useParams<{ workspace: string }>();
  const { t } = useTranslation();
  const { user } = useAuth();
  const [envText, setEnvText] = useState("");
  const [warnings, setWarnings] = useState<WsConfigWarnings | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [isDefault, setIsDefault] = useState(false);

  useEffect(() => {
    getWsConfig(ws).then((r) => {
      // 新工作区（is_default）→ 预填完整推荐模板；否则显示后端实际配置。
      setIsDefault(r.is_default);
      setEnvText(r.is_default ? buildDefaultTemplate(t) : r.env_text);
      setLoaded(true);
    })
      .catch(() => setLoaded(true));
    getMembers(ws).then((r) => setMembers(r.members)).catch(() => {});
  }, [ws]);

  // workspace 级角色来自 members API（全局 user.role 只有 admin/user）；复用 MemberManagerDialog 模式
  const myRole = user?.role === "admin" ? "admin" : members.find((m) => m.user_id === user?.id)?.role;
  const canEdit = myRole === "admin" || myRole === "manager";

  async function onSave() {
    setBusy(true);
    setWarnings(null);
    try {
      const r = await putWsConfig(ws, envText);
      // 用 PUT 响应的原样文本回显（注释/顺序保留、凭据打码）——保存什么就看到什么，
      // 免去二次 GET；config.yaml 已存在，is_default 置 false 免得预填提示残留
      setEnvText(r.env_text);
      setIsDefault(false);
      if (r.warnings && (r.warnings.ineffective.length || r.warnings.unknown.length)) {
        setWarnings(r.warnings);
      }
      toast.success(t("wsConfig.saved"));
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      const key = status === 403 ? "wsConfig.errors.forbidden"
        : status === 422 ? "wsConfig.errors.invalid"
        : "wsConfig.errors.saveFailed";
      toast.error(t(key));
    } finally {
      setBusy(false);
    }
  }

  // 点击配置项 → 把 `KEY=默认值` 注入左侧编辑框（凭据类留空等用户填）。
  // 已存在同名 key（含被 # 注释的行）则跳过，避免覆盖用户已填值。
  function injectKey(k: CfgKey) {
    setEnvText((prev) => {
      // 行首容忍前导空白与可选 # 注释；匹配 `KEY=`。
      const existsRe = new RegExp(`^\\s*#?\\s*${k.key}=`, "m");
      if (existsRe.test(prev)) {
        toast.info(t("wsConfig.keys.exists", { key: k.key }));
        return prev;
      }
      const line = `${k.key}=${k.defaultValue}`;
      toast.success(t("wsConfig.keys.inserted", { key: k.key }));
      if (!prev.trim()) return line;
      return `${prev.replace(/\n$/, "")}\n${line}`;
    });
  }

  // 把推荐模板（与新建预填、单击注入同源 EFFECTIVE_GROUPS）注入文本区：真实默认值直接生效，
  // 凭据行以 # 注释占位。文本区已有同名 key（含 # 注释行）则跳过，与单击注入的防重复语义一致
  // （parse 时后行覆盖前行，重复行会悄悄改值）。
  function insertTemplate() {
    const exists = (key: string) => new RegExp(`^\\s*#?\\s*${key}=`, "m").test(envText);
    const tpl = buildDefaultTemplate(t, (key) => !exists(key));
    if (!tpl) {
      toast.info(t("wsConfig.keys.existsAll"));
      return;
    }
    setEnvText(envText.trim() ? `${envText.replace(/\n$/, "")}\n\n${tpl}` : tpl);
    toast.success(t("wsConfig.keys.templateInserted"));
  }

  if (!loaded) return null;
  return (
    <div className="space-y-3">
    <Card>
      <CardHeader>
        <CardTitle className="font-semibold tracking-tight text-base">{t("wsConfig.title")}</CardTitle>
        <p className="text-sm text-muted-foreground">{t("wsConfig.subtitle")}</p>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* lg 下整卡锁定一屏：词典面板自身滚动，编辑区填满剩余高度，
            保存按钮收进编辑区底部（原来挂在整块 grid 之下，首屏看不到）。 */}
        <div className="grid gap-5 lg:h-[calc(100dvh-29rem)] lg:min-h-[22rem] lg:grid-cols-[minmax(0,1fr)_20rem]">
          {/* 编辑区（无可见 Label：卡片标题+副标题已说明，textarea 留 aria-label） */}
          <div className="flex min-w-0 flex-col gap-2">
            <Textarea
              aria-label={t("wsConfig.envText")}
              className="min-h-[460px] font-mono text-sm lg:min-h-0 lg:flex-1"
              value={envText}
              disabled={!canEdit}
              placeholder={PLACEHOLDER}
              onChange={(e) => setEnvText(e.target.value)}
            />
            <div className="flex flex-wrap items-center gap-3">
              {canEdit && (
                <Button onClick={onSave} disabled={busy}>{t("wsConfig.save")}</Button>
              )}
              {isDefault && canEdit && (
                <p className="text-xs text-muted-foreground">{t("wsConfig.keys.prefillHint")}</p>
              )}
            </div>
            {warnings && (
              <div className="space-y-1 text-sm text-amber-600 dark:text-amber-500">
                {warnings.ineffective.length > 0 && (
                  <p>{t("wsConfig.warnings.ineffective")}: {warnings.ineffective.join(", ")}</p>
                )}
                {warnings.unknown.length > 0 && (
                  <p>{t("wsConfig.warnings.unknown")}: {warnings.unknown.join(", ")}</p>
                )}
              </div>
            )}
          </div>

          {/* 配置词典面板：把后端 key 分类（生效 / 进程级）做成始终可见、可交互的清单 */}
          <aside className="space-y-4 rounded-lg border bg-card/60 p-4 [backdrop-filter:var(--backdrop-card,none)] lg:overflow-y-auto">
            <div className="space-y-1">
              <h3 className="text-sm font-medium">{t("wsConfig.keys.panelTitle")}</h3>
              <p className="text-xs text-muted-foreground">{t("wsConfig.keys.panelDesc")}</p>
            </div>
            {canEdit && (
              <Button variant="outline" size="sm" className="w-full" onClick={insertTemplate}>
                <LayoutTemplate className="size-3.5" />
                {t("wsConfig.keys.insertTemplate")}
              </Button>
            )}
            <p className="text-xs text-muted-foreground">{t("wsConfig.keys.kindHint")}</p>

            {/* 生效组 */}
            <div className="space-y-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t("wsConfig.keys.effectiveTitle")}
              </div>
              {EFFECTIVE_GROUPS.map((g) => (
                <div key={g.titleKey} className="space-y-1">
                  <div className="text-xs font-medium text-foreground/80">{t(g.titleKey)}</div>
                  <ul>
                    {g.keys.map((k) => (
                      <KeyRow
                        key={k.key}
                        cfgKey={k}
                        onInject={injectKey}
                      />
                    ))}
                  </ul>
                </div>
              ))}
            </div>

            {/* 进程级组：ws 级不生效，琥珀警示 */}
            <div
              className="space-y-2 border-t pt-3"
              style={{ borderColor: "hsl(var(--c-amber) / 0.4)" }}
            >
              <div
                className="flex items-center gap-1.5 text-xs font-semibold"
                style={{ color: "hsl(var(--c-amber))" }}
              >
                <AlertTriangle className="size-3.5" />
                {t("wsConfig.keys.processTitle")}
              </div>
              <p className="text-xs text-muted-foreground">{t("wsConfig.keys.processDesc")}</p>
              <ul>
                {PROCESS_KEYS.map((k) => (
                  <KeyRow
                    key={k.key}
                    cfgKey={k}
                    processLevel
                    onInject={injectKey}
                  />
                ))}
              </ul>
            </div>
          </aside>
        </div>
      </CardContent>
    </Card>
    {/* 定价卡（spec 2026-08-28）：继承全局 / 覆盖本工作区，来源徽章展示生效层 */}
    <WsPricingCard />
    </div>
  );
}
