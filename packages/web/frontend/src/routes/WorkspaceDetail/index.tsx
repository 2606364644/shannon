import { NavLink, Outlet, useParams } from "react-router-dom";

export default function WorkspaceDetail() {
  const { workspace } = useParams<{ workspace: string }>();
  const tabs = [
    { to: "overview", label: "概览" }, { to: "report", label: "报告" },
    { to: "deliverables", label: "产物" }, { to: "logs", label: "日志" }, { to: "live", label: "实时" },
  ];
  return (
    <div className="workspace-detail">
      <h2 className="mono">{workspace}</h2>
      <nav className="tab-nav">
        {tabs.map((t) => (
          <NavLink key={t.to} to={t.to} className={({ isActive }) => isActive ? "tab-active" : ""}>{t.label}</NavLink>
        ))}
      </nav>
      <div className="tab-body"><Outlet /></div>
    </div>
  );
}
