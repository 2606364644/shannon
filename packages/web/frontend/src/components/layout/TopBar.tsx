import { Link, NavLink } from "react-router-dom";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "./ThemeToggle";

interface NavItem {
  label: string;
  to: string;
  disabled?: boolean;
  end?: boolean;
}

// 顶层导航:Dashboard / Workspaces / 仓库 / Scan / Settings(子项目5 全启用)
const NAV: NavItem[] = [
  { label: "Dashboard", to: "/", end: true },
  { label: "Workspaces", to: "/workspaces", end: true },
  { label: "仓库", to: "/repos", end: true },
  { label: "Scan", to: "/scan/new" },
  { label: "Settings", to: "/settings" },
];

export function TopBar() {
  return (
    <header className="border-b border-border bg-card">
      <div className="mx-auto flex h-12 max-w-[1400px] items-center gap-6 px-7">
        <Link to="/" className="flex items-center gap-1.5 font-serif text-base">
          <span className="text-cyan">⬡</span>
          <span>Shannon</span>
        </Link>
        <nav className="flex items-center gap-1" aria-label="主导航">
          {NAV.map((n) =>
            n.disabled ? (
              <span
                key={n.label}
                aria-disabled="true"
                className="cursor-not-allowed border-b-2 border-transparent px-3 py-1.5 text-sm text-muted-foreground/50"
              >
                {n.label}
              </span>
            ) : (
              <NavLink key={n.label} to={n.to} end={n.end} className="inline-flex">
                {({ isActive }) => (
                  <span
                    data-active={isActive}
                    className={cn(
                      "border-b-2 px-3 py-1.5 text-sm transition-colors",
                      isActive
                        ? "border-primary text-primary"
                        : "border-transparent text-muted-foreground hover:text-foreground"
                    )}
                  >
                    {n.label}
                  </span>
                )}
              </NavLink>
            )
          )}
        </nav>
        <div className="ml-auto flex items-center gap-1">
          {/* 运行中扫描指示器 slot（子项目 5 接 SSE） */}
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
