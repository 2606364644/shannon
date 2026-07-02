const MAP: Record<string, { icon: string; cls: string }> = {
  running:   { icon: "●", cls: "ev-info" },
  completed: { icon: "✓", cls: "ev-agent-ok" },
  done:      { icon: "✓", cls: "ev-agent-ok" },
  failed:    { icon: "✗", cls: "ev-agent-fail" },
  killed:    { icon: "✗", cls: "ev-agent-fail" },
  crashed:   { icon: "⚠", cls: "ev-warn" },
};

export function StatusBadge({ status, correlation = false }: { status: string; correlation?: boolean }) {
  const m = MAP[status] ?? { icon: "?", cls: "ev-warn" };
  return (
    <span className={`status-badge ${m.cls}`}>
      <span className="mono">{m.icon}</span> {status}{correlation ? " 🔗" : ""}
    </span>
  );
}
