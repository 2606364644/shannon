import { RouterProvider } from "react-router-dom";
import { router } from "./router";
import { Toaster } from "@/components/ui/sonner";
import { BrandProvider } from "@/brand/BrandContext";
import { AuthProvider } from "./auth/AuthContext";
import { ThemeProvider } from "@/lib/theme-context";

export default function App() {
  return (
    <BrandProvider>
      <AuthProvider>
        {/* ThemeProvider 覆盖所有路由：AppShell 的 TopBar ThemeToggle + SettingsPage segmented
            + LoginPage 浮动 ThemeToggle 共享同一 context（修两处不同步 bug） */}
        <ThemeProvider>
          <RouterProvider router={router} />
        </ThemeProvider>
      </AuthProvider>
      <Toaster />
    </BrandProvider>
  );
}
