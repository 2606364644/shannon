import { useState } from "react";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ErrorState";
import { useSystemStatus } from "@/api/systemStatus";
import { applyTheme, getInitialTheme, type Theme } from "@/lib/theme";

export function SettingsPage() {
  const initial = typeof window !== "undefined" ? getInitialTheme() : "dark";
  const [theme, setThemeState] = useState<Theme>(initial);
  const { data, loading, error, refresh } = useSystemStatus();

  function setTheme(t: Theme) {
    setThemeState(t);
    applyTheme(t);
  }

  return (
    <div className="space-y-6">
      <h1 className="font-serif text-2xl">设置</h1>

      <Card>
        <CardHeader><CardTitle className="font-serif text-base">主题</CardTitle></CardHeader>
        <CardContent className="flex items-center gap-3 text-sm">
          <Label htmlFor="theme-switch">深色</Label>
          <Switch
            id="theme-switch"
            checked={theme === "light"}
            onCheckedChange={(c) => setTheme(c ? "light" : "dark")}
            aria-label="切换深浅主题"
          />
          <Label htmlFor="theme-switch">浅色</Label>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="font-serif text-base">系统状态</CardTitle></CardHeader>
        <CardContent>
          {loading && <Skeleton className="h-20 w-full" />}
          {error && <ErrorState message={`状态加载失败:${error}`} onRetry={refresh} />}
          {data && (
            <dl className="grid grid-cols-[140px_1fr] gap-y-2 font-mono text-sm">
              <dt className="text-muted-foreground">AI 引擎</dt>
              <dd>{data.ai_provider}</dd>
              <dt className="text-muted-foreground">浏览器引擎</dt>
              <dd>{data.browser_engine}</dd>
              <dt className="text-muted-foreground">Temporal</dt>
              <dd className="flex items-center gap-2">
                {data.temporal.host}
                <Badge variant="outline" className={data.temporal.last_status === "connected" ? "border-green/40 text-green" : "border-red/40 text-red"}>
                  {data.temporal.last_status}
                </Badge>
              </dd>
              <dt className="text-muted-foreground">Git</dt>
              <dd>{data.git_available ? "可用" : "不可用"}</dd>
              <dt className="text-muted-foreground">版本</dt>
              <dd>{data.version}</dd>
            </dl>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="font-serif text-base">关于</CardTitle></CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          <div>shannon-py 安全扫描平台 web 控制台。版本信息见上方系统状态面板。</div>
        </CardContent>
      </Card>
    </div>
  );
}
