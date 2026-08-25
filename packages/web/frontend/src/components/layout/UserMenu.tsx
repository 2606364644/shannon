import { useState } from "react";
import { Link } from "react-router-dom";
import { Users, LogOut } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Button } from "@/components/ui/button";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { cn } from "@/lib/utils";

/** 用户头像（spec 2026-08-25 §8）：SSO 用户直连 OA CDN（referrerPolicy=no-referrer，
 *  服务端不代理防 SSRF）；无 URL 或加载失败回退首字母圆形。 */
function UserAvatar({ url, name, size }: { url?: string | null; name: string; size: "sm" | "md" }) {
  const [broken, setBroken] = useState(false);
  const boxCls = size === "sm" ? "h-7 w-7 text-xs" : "h-8 w-8 text-sm";
  if (url && !broken) {
    return (
      <img src={url} alt="" referrerPolicy="no-referrer" data-testid="user-avatar-img"
           onError={() => setBroken(true)}
           className={cn("rounded-full object-cover", boxCls)} />
    );
  }
  return (
    <span className={cn(
      "flex items-center justify-center rounded-full bg-primary font-semibold text-primary-foreground",
      boxCls)}>
      {name.charAt(0).toUpperCase()}
    </span>
  );
}

export function UserMenu() {
  const { user, logout } = useAuth();
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (!user) return null;
  const isAdmin = user.role === "admin";
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button className="flex items-center gap-2 rounded-full border border-transparent py-1 pl-1 pr-2 text-sm transition-colors hover:border-border hover:bg-accent" data-testid="user-menu-trigger">
          <UserAvatar url={user.avatar_url} name={user.username} size="sm" />
          <span className="hidden sm:inline">{user.username}</span>
          <span className={cn("rounded px-1.5 py-0.5 text-[11px] font-medium", isAdmin ? "bg-orange/10 text-orange" : "bg-muted text-muted-foreground")}>
            {t(`auth.role.${user.role}`)}
          </span>
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-52 p-1">
        <div className="flex items-center gap-2.5 rounded-md px-2.5 py-2">
          <UserAvatar url={user.avatar_url} name={user.username} size="md" />
          <div className="min-w-0">
            <div className="truncate text-sm font-medium">{user.username}</div>
            <div className="text-xs text-muted-foreground">{t(`auth.role.${user.role}`)}</div>
          </div>
        </div>
        <div className="my-1 h-px bg-border" />
        {isAdmin && (
          <Button variant="ghost" className="w-full justify-start gap-2" asChild>
            <Link to="/users" data-testid="user-mgmt-link" onClick={() => setOpen(false)}>
              <Users className="size-4" /> {t("users.manageLink")}
            </Link>
          </Button>
        )}
        <Button
          variant="ghost"
          className="w-full justify-start gap-2 text-destructive hover:bg-destructive/10 hover:text-destructive"
          onClick={() => {
            setOpen(false);
            void logout();
          }}
        >
          <LogOut className="size-4" /> {t("auth.logout")}
        </Button>
      </PopoverContent>
    </Popover>
  );
}
