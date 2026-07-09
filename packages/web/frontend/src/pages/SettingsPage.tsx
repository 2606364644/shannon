import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ErrorState";
import { useSystemStatus } from "@/api/systemStatus";
import { applyTheme, getInitialTheme, type Theme } from "@/lib/theme";

export function SettingsPage() {
  const { t } = useTranslation();
  const initial = typeof window !== "undefined" ? getInitialTheme() : "dark";
  const [theme, setThemeState] = useState<Theme>(initial);
  const { data, loading, error, refresh } = useSystemStatus();

  function setTheme(next: Theme) {
    setThemeState(next);
    applyTheme(next);
  }

  return (
    <div className="space-y-6">
      <h1 className="font-semibold tracking-tight text-2xl">{t("settings.title")}</h1>

      <Card>
        <CardHeader><CardTitle className="font-semibold tracking-tight text-base">{t("settings.themeTitle")}</CardTitle></CardHeader>
        <CardContent className="flex items-center gap-3 text-sm">
          <Label htmlFor="theme-switch">{t("settings.themeDark")}</Label>
          <Switch
            id="theme-switch"
            checked={theme === "light"}
            onCheckedChange={(c) => setTheme(c ? "light" : "dark")}
            aria-label={t("settings.themeSwitchAria")}
          />
          <Label htmlFor="theme-switch">{t("settings.themeLight")}</Label>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="font-semibold tracking-tight text-base">{t("settings.statusTitle")}</CardTitle></CardHeader>
        <CardContent>
          {loading && <Skeleton className="h-20 w-full" />}
          {error && <ErrorState message={t("settings.errors.loadFailed", { error })} onRetry={refresh} />}
          {data && (
            <dl className="grid grid-cols-[140px_1fr] gap-y-2 font-mono text-sm">
              <dt className="text-muted-foreground">{t("settings.fields.aiProvider")}</dt>
              <dd>{data.ai_provider}</dd>
              <dt className="text-muted-foreground">{t("settings.fields.browserEngine")}</dt>
              <dd>{data.browser_engine}</dd>
              <dt className="text-muted-foreground">Temporal</dt>
              <dd className="flex items-center gap-2">
                {data.temporal.host}
                <Badge variant="outline" className={data.temporal.last_status === "connected" ? "border-green/40 text-green" : "border-red/40 text-red"}>
                  {data.temporal.last_status}
                </Badge>
              </dd>
              <dt className="text-muted-foreground">{t("settings.fields.gitBinary")}</dt>
              <dd>{data.git.binary_available ? t("settings.gitBinary.installed") : t("settings.gitBinary.missing")}</dd>
              <dt className="text-muted-foreground">{t("settings.fields.gitCredentials")}</dt>
              <dd>{data.git.credentials_configured ? t("settings.gitCredentials.configured") : t("settings.gitCredentials.notConfigured")}</dd>
              <dt className="text-muted-foreground">{t("settings.fields.version")}</dt>
              <dd>{data.version}</dd>
            </dl>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="font-semibold tracking-tight text-base">{t("settings.aboutTitle")}</CardTitle></CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          <div>{t("settings.aboutDesc")}</div>
        </CardContent>
      </Card>
    </div>
  );
}
