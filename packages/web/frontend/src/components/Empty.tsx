import type { ReactNode } from "react";

export function Empty({
  icon = "∅",
  title,
  hint,
  children,
}: {
  icon?: ReactNode;
  title: string;
  hint?: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-16 text-muted-foreground">
      <div className="text-3xl">{icon}</div>
      <div className="text-base text-foreground">{title}</div>
      {hint && <div className="text-sm">{hint}</div>}
      {children && <div className="mt-2">{children}</div>}
    </div>
  );
}
