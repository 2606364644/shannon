import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { ScanSummary } from "@/api/types";

export interface ScanFiltersValue {
  status: string;   // all | running | completed | failed | killed | crashed | interrupted
  type: string;     // all | whitebox | blackbox | correlation
  keyword: string;
  time: string;     // all | today | 7d | 30d
}

export const DEFAULT_SCAN_FILTERS: ScanFiltersValue = { status: "all", type: "all", keyword: "", time: "all" };

export function ScanFilters({ value, onChange }: { value: ScanFiltersValue; onChange: (v: ScanFiltersValue) => void }) {
  const { t } = useTranslation();
  const set = (patch: Partial<ScanFiltersValue>) => onChange({ ...value, ...patch });
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Input
        placeholder={t("scanFilters.keyword")}
        value={value.keyword}
        onChange={(e) => set({ keyword: e.target.value })}
        className="max-w-xs"
      />
      <Select value={value.status} onValueChange={(v) => set({ status: v })}>
        <SelectTrigger aria-label={t("scanFilters.status")} className="w-32"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">{t("workspaces.filter.allStatus")}</SelectItem>
          <SelectItem value="running">{t("workspaces.status.running")}</SelectItem>
          <SelectItem value="completed">{t("workspaces.status.completed")}</SelectItem>
          <SelectItem value="failed">{t("workspaces.status.failed")}</SelectItem>
          <SelectItem value="killed">{t("workspaces.status.killed")}</SelectItem>
          <SelectItem value="crashed">{t("workspaces.status.crashed")}</SelectItem>
          <SelectItem value="interrupted">{t("workspaces.status.interrupted")}</SelectItem>
        </SelectContent>
      </Select>
      <Select value={value.type} onValueChange={(v) => set({ type: v })}>
        <SelectTrigger aria-label={t("scanFilters.type")} className="w-32"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">{t("workspaces.filter.allType")}</SelectItem>
          <SelectItem value="whitebox">{t("workspaces.filter.whitebox")}</SelectItem>
          <SelectItem value="blackbox">{t("workspaces.filter.blackbox")}</SelectItem>
          <SelectItem value="correlation">{t("workspaces.filter.correlation")}</SelectItem>
        </SelectContent>
      </Select>
      <Select value={value.time} onValueChange={(v) => set({ time: v })}>
        <SelectTrigger aria-label={t("scanFilters.timeLabel")} className="w-32"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">{t("scanFilters.time.all")}</SelectItem>
          <SelectItem value="today">{t("scanFilters.time.today")}</SelectItem>
          <SelectItem value="7d">{t("scanFilters.time.7d")}</SelectItem>
          <SelectItem value="30d">{t("scanFilters.time.30d")}</SelectItem>
        </SelectContent>
      </Select>
    </div>
  );
}

function inTimeWindow(unix: number, window: string): boolean {
  if (window === "all") return true;
  const now = Date.now() / 1000;
  const day = 86400;
  if (window === "today") {
    const d = new Date(unix * 1000), n = new Date();
    return d.toDateString() === n.toDateString();
  }
  if (window === "7d") return unix >= now - 7 * day;
  if (window === "30d") return unix >= now - 30 * day;
  return true;
}

export function useScanFilters(scans: ScanSummary[], value: ScanFiltersValue) {
  const filtered = scans.filter((s) => {
    if (value.status !== "all" && s.status !== value.status) return false;
    if (value.type !== "all" && s.scan_type !== value.type) return false;
    if (value.time !== "all" && !inTimeWindow(s.created_at, value.time)) return false;
    if (value.keyword.trim()) {
      const q = value.keyword.toLowerCase();
      const hay = `${s.workflow_id ?? s.scan_id} ${s.scan_id} ${s.workspace ?? ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  return { filters: value, setFilters: () => {}, filtered };
}
