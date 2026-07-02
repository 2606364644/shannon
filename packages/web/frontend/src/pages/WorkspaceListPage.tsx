import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { Workspace } from "../api/types";
import { apiGet } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";

function fmtMs(ms?: number): string {
  if (!ms) return "—";
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}m${s % 60}s`;
}

export function WorkspaceListPage() {
  const [items, setItems] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const load = useCallback(() => {
    setLoading(true);
    return apiGet<Workspace[]>("/workspaces")
      .then((r) => { setItems(r); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <div className="page">
      <h1>
        Workspaces <button onClick={load} aria-label="refresh">↻</button>{" "}
        <Link to="/scan/new"><button>+ new scan</button></Link>
      </h1>
      {!loading && items.length === 0 ? (
        <p className="empty">no workspaces yet</p>
      ) : (
        <table className="ledger mono">
          <thead>
            <tr><th>workspace</th><th>status</th><th>type</th><th>vulns</th><th>cost</th><th>time</th></tr>
          </thead>
          <tbody>{items.map((w) => <Row key={w.name} w={w} />)}</tbody>
        </table>
      )}
    </div>
  );
}

function Row({ w }: { w: Workspace }) {
  const corr = w.scan_type === "correlation";
  const children = corr ? (w.links?.child_workspaces ?? []) : [];
  return (
    <>
      <tr className={`ledger-row status-${w.status}`}>
        <td>
          <span className={`status-bar status-${w.status}`} />{" "}
          <Link to={`/p/${w.name}`}>{w.name}</Link>{corr ? " 🔗" : ""}
        </td>
        <td><StatusBadge status={w.status} correlation={corr} /></td>
        <td>{w.scan_type}</td>
        <td>{w.vuln_count ?? "—"}</td>
        <td>${(w.total_cost_usd ?? 0).toFixed(2)}</td>
        <td>{fmtMs(w.total_duration_ms)}</td>
      </tr>
      {children.map((c) => (
        <tr key={c} className="ledger-child trace">
          <td colSpan={6}>　└─ <Link to={`/p/${c}`}>{c}</Link></td>
        </tr>
      ))}
    </>
  );
}
