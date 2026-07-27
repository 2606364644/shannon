import { useEffect, useState } from "react";
import { Outlet } from "react-router-dom";
import { TopBar } from "./TopBar";
import { ChangePasswordDialog } from "@/components/ChangePasswordDialog";
import { useAuth } from "@/auth/AuthContext";

export function AppShell() {
  const { user, refreshUser } = useAuth();
  const [cpOpen, setCpOpen] = useState(false);
  const mustChange = user?.must_change_password === true;

  // 登录后若 must_change_password=true，自动弹一次改密提醒（用户可点「稍后」关闭，
  // 关闭后顶栏 ⚠ badge 仍持续可见，点击可再次打开）。
  useEffect(() => {
    if (mustChange) setCpOpen(true);
  }, [mustChange]);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <TopBar onOpenChangePwd={() => setCpOpen(true)} />
      <main className="mx-auto max-w-[1400px] px-7 py-5">
        <Outlet />
      </main>
      {mustChange && (
        <ChangePasswordDialog
          open={cpOpen}
          onOpenChange={setCpOpen}
          onChanged={refreshUser}
        />
      )}
    </div>
  );
}
