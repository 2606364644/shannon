import { Link, NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "./ThemeToggle";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { BrandMark } from "./BrandMark";
import { UserMenu } from "./UserMenu";
import { useBrand } from "@/brand/BrandContext";

interface NavItem {
  labelKey: string;
  to: string;
  disabled?: boolean;
  end?: boolean;
}

const NAV: NavItem[] = [
  { labelKey: "nav.dashboard", to: "/", end: true },
  { labelKey: "nav.workspaces", to: "/workspaces", end: true },
  { labelKey: "nav.repos", to: "/repos", end: true },
  { labelKey: "nav.scan", to: "/scan/new" },
  { labelKey: "nav.settings", to: "/settings" },
];

export function TopBar() {
  const { t } = useTranslation();
  const brand = useBrand();
  return (
    <header data-testid="topbar" className="sticky top-0 z-40 border-b border-border bg-card print:static">
      <div className="mx-auto flex h-12 max-w-[1400px] items-center gap-6 px-7">
        <Link to="/" className="flex items-center gap-1.5 font-semibold tracking-tight text-base">
          <BrandMark className="h-[1.15em] w-[1.15em] text-foreground" />
          <span>{brand}</span>
        </Link>
        <nav className="flex items-center gap-1" aria-label={t("nav.mainAria")}>
          {NAV.map((n) =>
            n.disabled ? (
              <span
                key={n.labelKey}
                aria-disabled="true"
                className="cursor-not-allowed border-b-2 border-transparent px-3 py-1.5 text-sm text-muted-foreground/50"
              >
                {t(n.labelKey)}
              </span>
            ) : (
              <NavLink key={n.labelKey} to={n.to} end={n.end} className="inline-flex">
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
                    {t(n.labelKey)}
                  </span>
                )}
              </NavLink>
            )
          )}
        </nav>
        <div className="ml-auto flex items-center gap-1">
          {/* 运行中扫描指示器 slot（子项目 5 接 SSE） */}
          <LanguageSwitcher />
          <ThemeToggle />
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
