import { useState } from "react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Button } from "@/components/ui/button";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { cn } from "@/lib/utils";

export function UserMenu() {
  const { user, logout } = useAuth();
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (!user) return null;
  const isAdmin = user.role === "admin";
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button className="flex items-center gap-1.5 rounded-md px-2 py-1 text-sm hover:bg-accent" data-testid="user-menu-trigger">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary text-xs text-primary-foreground">
            {user.username.charAt(0).toUpperCase()}
          </span>
          <span className={cn("rounded px-1.5 py-0.5 text-xs", isAdmin ? "text-[hsl(var(--c-orange))]" : "text-muted-foreground")}>
            {t(`auth.role.${user.role}`)}
          </span>
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-48">
        <div className="px-3 py-2 text-sm">
          <div className="font-medium">{user.username}</div>
          <div className="text-xs text-muted-foreground">{t(`auth.role.${user.role}`)}</div>
        </div>
        <Button
          variant="ghost"
          className="w-full justify-start"
          onClick={() => {
            setOpen(false);
            void logout();
          }}
        >
          {t("auth.logout")}
        </Button>
      </PopoverContent>
    </Popover>
  );
}
