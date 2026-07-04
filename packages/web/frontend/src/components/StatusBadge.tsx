import { Badge } from "@/components/ui/badge";

const MAP: Record<string, { icon: string; cls: string }> = {
  running:   { icon: "●", cls: "border-cyan/40 text-cyan" },
  completed: { icon: "✓", cls: "border-green/40 text-green" },
  done:      { icon: "✓", cls: "border-green/40 text-green" },
  failed:    { icon: "✗", cls: "border-red/40 text-red" },
  killed:    { icon: "✗", cls: "border-red/40 text-red" },
  crashed:   { icon: "⚠", cls: "border-yellow/40 text-yellow" },
};

export function StatusBadge({ status, correlation = false }: { status: string; correlation?: boolean }) {
  const m = MAP[status] ?? { icon: "?", cls: "border-yellow/40 text-yellow" };
  return (
    <Badge variant="outline" className={`gap-1 font-mono ${m.cls}`} title={status}>
      <span aria-hidden>{m.icon}</span>
      {status}
      {correlation ? " 🔗" : ""}
    </Badge>
  );
}
