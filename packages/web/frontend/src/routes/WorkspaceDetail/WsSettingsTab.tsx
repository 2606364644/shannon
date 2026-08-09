import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { useAuth } from "@/auth/AuthContext";
import { getWsConfig, putWsConfig, type WsProviderFields, type WsGitFields } from "@/api/wsConfig";
import { getMembers } from "@/api/members";
import type { Member } from "@/api/members";
import { ApiError } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";

const EMPTY: WsProviderFields = {
  ai_provider: null, api_key: null, base_url: null, model: null,
  small_model: null, medium_model: null, large_model: null,
  max_turns: null, adaptive_thinking: null,
};
const EMPTY_GIT: WsGitFields = { gitlab_user: null, gitlab_token: null };

// 合法 provider 名（与后端 PROVIDER_SETTINGS 键一致）
const PROVIDERS = ["anthropic_api", "openai_compatible", "bedrock", "vertex", "litellm_router"];

// 三档模型（string|null），窄化 key 类型让 setField 泛型可推断。
const MODEL_TIERS: { key: "small_model" | "medium_model" | "large_model"; labelKey: string; tierKey: string }[] = [
  { key: "small_model", labelKey: "smallModel", tierKey: "small" },
  { key: "medium_model", labelKey: "mediumModel", tierKey: "medium" },
  { key: "large_model", labelKey: "largeModel", tierKey: "large" },
];

// 计「已覆盖」计数的非凭据字段（凭据另有 已配置/未配置 语义）。
const OVERRIDE_KEYS: (keyof WsProviderFields)[] = [
  "ai_provider", "base_url", "model", "small_model", "medium_model", "large_model", "max_turns", "adaptive_thinking",
];

const isSet = (v: unknown): boolean => v !== null && v !== undefined && v !== "";

// 来源 / 状态徽标色调：覆盖(amber)最突出，其余退后。
type Tone = "inherit" | "override" | "set" | "unset";
const TONE_CLS: Record<Tone, string> = {
  inherit: "border-border text-muted-foreground",
  override: "border-amber/40 text-amber",
  set: "border-green/40 text-green",
  unset: "border-border text-muted-foreground",
};

function StatusPill({ tone }: { tone: Tone }) {
  const { t } = useTranslation();
  return (
    <Badge variant="outline" className={cn("font-mono", TONE_CLS[tone])}>
      {t(`wsConfig.provenance.${tone}`)}
    </Badge>
  );
}

function ResetLink({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <button type="button" onClick={onClick}
      className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline">
      ↺ {t("wsConfig.reset")}
    </button>
  );
}

function Section({ titleKey, descKey, children }: { titleKey: string; descKey: string; children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader className="gap-0.5 pb-3">
        <CardTitle className="text-sm font-semibold tracking-tight">{t(titleKey)}</CardTitle>
        <p className="text-xs text-muted-foreground">{t(descKey)}</p>
      </CardHeader>
      <CardContent className="space-y-4">{children}</CardContent>
    </Card>
  );
}

// 字段标签行：左 label，右（重置? + 徽标）
function FieldLabel({ htmlFor, label, tone, onReset }: {
  htmlFor?: string; label: string; tone: Tone; onReset?: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <Label htmlFor={htmlFor} className="text-sm">{label}</Label>
      <div className="flex items-center gap-2">
        {tone === "override" && onReset && <ResetLink onClick={onReset} />}
        <StatusPill tone={tone} />
      </div>
    </div>
  );
}

export default function WsSettingsTab() {
  const { workspace: ws = "" } = useParams<{ workspace: string }>();
  const { t } = useTranslation();
  const { user } = useAuth();

  const [cfg, setCfg] = useState<WsProviderFields>(EMPTY);
  const [initialCfg, setInitialCfg] = useState<WsProviderFields>(EMPTY);
  const [gitCfg, setGitCfg] = useState<WsGitFields>(EMPTY_GIT);
  const [initialGit, setInitialGit] = useState<WsGitFields>(EMPTY_GIT);
  const [apiKeyInput, setApiKeyInput] = useState(""); // password 框，空=不改
  const [gitlabTokenInput, setGitlabTokenInput] = useState(""); // password 框，空=不改
  const [members, setMembers] = useState<Member[]>([]);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    setLoaded(false);
    setLoadError(false);
    getWsConfig(ws).then((r) => {
      const git = r.git ?? EMPTY_GIT;
      setCfg(r.provider); setInitialCfg(r.provider);
      setGitCfg(git); setInitialGit(git);
      setLoaded(true);
    }).catch(() => { setLoadError(true); setLoaded(true); });
    getMembers(ws).then((r) => setMembers(r.members)).catch(() => {});
  }, [ws]);

  // workspace 级角色来自 members API（全局 user.role 只有 admin/user）；复用 MemberManagerDialog 模式
  const myRole = user?.role === "admin" ? "admin" : members.find((m) => m.user_id === user?.id)?.role;
  const canEdit = myRole === "admin" || myRole === "manager";

  const providerDirty = useMemo(() => JSON.stringify(cfg) !== JSON.stringify(initialCfg), [cfg, initialCfg]);
  const gitDirty = useMemo(() => JSON.stringify(gitCfg) !== JSON.stringify(initialGit), [gitCfg, initialGit]);
  const dirty = providerDirty || gitDirty || apiKeyInput !== "" || gitlabTokenInput !== "";

  const overrideCount =
    OVERRIDE_KEYS.filter((k) => isSet(cfg[k])).length + (isSet(gitCfg.gitlab_user) ? 1 : 0);

  function setField<K extends keyof WsProviderFields>(key: K, val: WsProviderFields[K]) {
    setCfg((c) => ({ ...c, [key]: val }));
  }

  function discard() {
    setCfg(initialCfg); setGitCfg(initialGit); setApiKeyInput(""); setGitlabTokenInput("");
  }

  async function onSave() {
    setBusy(true);
    try {
      await putWsConfig(ws, {
        provider: {
          ...cfg,
          api_key: apiKeyInput || undefined, // 空=不发（后端保原值）
        },
        git: {
          gitlab_user: gitCfg.gitlab_user,
          gitlab_token: gitlabTokenInput || undefined, // 空=不发（后端保原值）
        },
      });
      setApiKeyInput("");
      setGitlabTokenInput("");
      const fresh = await getWsConfig(ws);
      const git = fresh.git ?? EMPTY_GIT;
      setCfg(fresh.provider); setInitialCfg(fresh.provider);
      setGitCfg(git); setInitialGit(git);
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

  if (!loaded) {
    return (
      <div className="max-w-3xl space-y-4" aria-busy="true">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="max-w-3xl">
        <Card>
          <CardContent className="flex items-center justify-between py-6">
            <p className="text-sm text-muted-foreground">{t("wsConfig.loadFailed")}</p>
            <Button variant="outline" onClick={() => {
              setLoaded(false); setLoadError(false);
              getWsConfig(ws).then((r) => {
                const git = r.git ?? EMPTY_GIT;
                setCfg(r.provider); setInitialCfg(r.provider);
                setGitCfg(git); setInitialGit(git); setLoaded(true);
              }).catch(() => { setLoadError(true); setLoaded(true); });
            }}>{t("wsConfig.retry")}</Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="max-w-3xl space-y-5">
      {/* 标题 + 覆盖计数汇总 */}
      <div className="space-y-1.5">
        <h1 className="text-lg font-semibold tracking-tight">{t("wsConfig.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("wsConfig.subtitle")}</p>
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <Badge variant="outline" className={cn("font-mono", overrideCount > 0 ? "border-amber/40 text-amber" : "border-border text-muted-foreground")}>
            {overrideCount > 0 ? t("wsConfig.summary.overridden", { count: overrideCount }) : t("wsConfig.summary.none")}
          </Badge>
        </div>
      </div>

      {!canEdit && (
        <p className="rounded-md border border-yellow/40 bg-yellow/10 px-3 py-2 text-xs text-yellow">
          {t("wsConfig.readonlyBanner")}
        </p>
      )}

      {/* AI 引擎与凭据 */}
      <Section titleKey="wsConfig.sections.engine.title" descKey="wsConfig.sections.engine.desc">
        <div className="space-y-1.5">
          <FieldLabel label={t("wsConfig.fields.aiProvider")} tone={isSet(cfg.ai_provider) ? "override" : "inherit"}
            onReset={() => setField("ai_provider", null)} />
          <Select value={cfg.ai_provider ?? "__unset__"} disabled={!canEdit}
            onValueChange={(v) => setField("ai_provider", v === "__unset__" ? null : v)}>
            <SelectTrigger><SelectValue placeholder={t("wsConfig.fallbackHint")} /></SelectTrigger>
            <SelectContent>
              <SelectItem value="__unset__">{t("wsConfig.fallbackHint")}</SelectItem>
              {PROVIDERS.map((p) => <SelectItem key={p} value={p}>{p}</SelectItem>)}
            </SelectContent>
          </Select>
          {cfg.ai_provider === "bedrock" && <p className="text-xs text-muted-foreground">{t("wsConfig.providerNote.bedrock")}</p>}
          {cfg.ai_provider === "vertex" && <p className="text-xs text-muted-foreground">{t("wsConfig.providerNote.vertex")}</p>}
        </div>

        <div className="space-y-1.5">
          <FieldLabel htmlFor="ws-api-key" label={t("wsConfig.fields.apiKey")} tone={isSet(cfg.api_key) ? "set" : "unset"} />
          <Input id="ws-api-key" type="password" value={apiKeyInput} disabled={!canEdit}
            className="font-mono"
            placeholder={cfg.api_key ? t("wsConfig.apiKey.configured") : t("wsConfig.apiKey.notConfigured")}
            onChange={(e) => setApiKeyInput(e.target.value)} />
        </div>

        <div className="space-y-1.5">
          <FieldLabel htmlFor="ws-base-url" label={t("wsConfig.fields.baseUrl")} tone={isSet(cfg.base_url) ? "override" : "inherit"}
            onReset={() => setField("base_url", null)} />
          <Input id="ws-base-url" value={cfg.base_url ?? ""} disabled={!canEdit}
            className="font-mono" onChange={(e) => setField("base_url", e.target.value || null)} />
        </div>
      </Section>

      {/* 模型 */}
      <Section titleKey="wsConfig.sections.models.title" descKey="wsConfig.sections.models.desc">
        <div className="space-y-1.5">
          <FieldLabel htmlFor="ws-model" label={t("wsConfig.fields.model")} tone={isSet(cfg.model) ? "override" : "inherit"}
            onReset={() => setField("model", null)} />
          <Input id="ws-model" value={cfg.model ?? ""} disabled={!canEdit} className="font-mono"
            onChange={(e) => setField("model", e.target.value || null)} />
          <p className="text-xs text-muted-foreground">{t("wsConfig.tiers.fallback")}</p>
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          {MODEL_TIERS.map(({ key, labelKey, tierKey }) => (
            <div key={key} className="space-y-1.5">
              <FieldLabel htmlFor={`ws-${key}`} label={t(`wsConfig.fields.${labelKey}`)}
                tone={isSet(cfg[key]) ? "override" : "inherit"} onReset={() => setField(key, null)} />
              <Input id={`ws-${key}`} value={(cfg[key] as string) ?? ""} disabled={!canEdit}
                className="font-mono" onChange={(e) => setField(key, e.target.value || null)} />
              <p className="text-xs text-muted-foreground">{t(`wsConfig.tiers.${tierKey}`)}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* 运行时 */}
      <Section titleKey="wsConfig.sections.runtime.title" descKey="wsConfig.sections.runtime.desc">
        <div className="space-y-1.5">
          <FieldLabel htmlFor="ws-max-turns" label={t("wsConfig.fields.maxTurns")} tone={isSet(cfg.max_turns) ? "override" : "inherit"}
            onReset={() => setField("max_turns", null)} />
          <Input id="ws-max-turns" type="number" value={cfg.max_turns ?? ""} disabled={!canEdit}
            className="font-mono" onChange={(e) => setField("max_turns", e.target.value ? Number(e.target.value) : null)} />
        </div>
        <div className="space-y-1.5">
          <FieldLabel label={t("wsConfig.fields.adaptiveThinking")} tone={isSet(cfg.adaptive_thinking) ? "override" : "inherit"}
            onReset={() => setField("adaptive_thinking", null)} />
          <div className="flex items-center gap-2">
            <Switch checked={cfg.adaptive_thinking ?? false} disabled={!canEdit}
              onCheckedChange={(v) => setField("adaptive_thinking", v)} />
            <span className="font-mono text-xs text-muted-foreground">
              {cfg.adaptive_thinking === null ? t("wsConfig.provenance.inherit") : (cfg.adaptive_thinking ? "on" : "off")}
            </span>
          </div>
        </div>
      </Section>

      {/* Git 访问 */}
      <Section titleKey="wsConfig.sections.git.title" descKey="wsConfig.sections.git.desc">
        <div className="space-y-1.5">
          <FieldLabel htmlFor="ws-gitlab-user" label={t("wsConfig.fields.gitlabUser")} tone={isSet(gitCfg.gitlab_user) ? "override" : "inherit"}
            onReset={() => setGitCfg((g) => ({ ...g, gitlab_user: null }))} />
          <Input id="ws-gitlab-user" value={gitCfg.gitlab_user ?? ""} disabled={!canEdit} className="font-mono"
            onChange={(e) => setGitCfg({ ...gitCfg, gitlab_user: e.target.value || null })} />
        </div>
        <div className="space-y-1.5">
          <FieldLabel htmlFor="ws-gitlab-token" label={t("wsConfig.fields.gitlabToken")} tone={isSet(gitCfg.gitlab_token) ? "set" : "unset"} />
          <Input id="ws-gitlab-token" type="password" value={gitlabTokenInput} disabled={!canEdit}
            className="font-mono"
            placeholder={gitCfg.gitlab_token ? t("wsConfig.gitToken.configured") : t("wsConfig.gitToken.notConfigured")}
            onChange={(e) => setGitlabTokenInput(e.target.value)} />
        </div>
      </Section>

      {/* 保存条 */}
      {canEdit && (
        <div className="flex items-center justify-end gap-3 border-t pt-4">
          {dirty && (
            <span className="mr-auto flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className="h-1.5 w-1.5 rounded-full bg-amber" /> {t("wsConfig.dirtyHint")}
            </span>
          )}
          {dirty && <Button variant="ghost" onClick={discard} disabled={busy}>{t("wsConfig.discard")}</Button>}
          <Button onClick={onSave} disabled={!dirty || busy}>{t("wsConfig.save")}</Button>
        </div>
      )}
    </div>
  );
}
