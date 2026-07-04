import { Outlet, useParams, useLocation, useNavigate } from "react-router-dom";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

const TABS = [
  { value: "overview", label: "概览" },
  { value: "report", label: "报告" },
  { value: "deliverables", label: "产物" },
  { value: "logs", label: "日志" },
  { value: "live", label: "实时" },
];

export default function WorkspaceDetail() {
  const { workspace } = useParams<{ workspace: string }>();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const current = pathname.split("/").pop() ?? "overview";
  return (
    <div className="space-y-4">
      <h2 className="font-mono text-xl">{workspace}</h2>
      <Tabs value={current} onValueChange={(v) => navigate(v)}>
        <TabsList>
          {TABS.map((t) => (
            <TabsTrigger key={t.value} value={t.value}>{t.label}</TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <div><Outlet /></div>
    </div>
  );
}
