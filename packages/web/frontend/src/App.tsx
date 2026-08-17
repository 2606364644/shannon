import { RouterProvider } from "react-router-dom";
import { router } from "./router";
import { Toaster } from "@/components/ui/sonner";
import { BrandProvider } from "@/brand/BrandContext";
import { AuthProvider } from "./auth/AuthContext";
import { ThemeProvider } from "./lib/theme-context";
import { SWRConfig } from "swr";
import { apiGet } from "@/api/client";

export default function App() {
  return (
    <BrandProvider>
      <AuthProvider>
        {/* ThemeProvider 覆盖所有路由：AppShell 的 TopBar ThemeToggle + SettingsPage segmented
            + LoginPage 浮动 ThemeToggle 共享同一 context（修两处不同步 bug） */}
        <ThemeProvider>
          {/* SWR 全局 fetcher（spec §D）：key 直接用 API path，hook 侧免传 fetcher。 */}
          <SWRConfig value={{ fetcher: (path: string) => apiGet(path) }}>
            <RouterProvider router={router} />
          </SWRConfig>
        </ThemeProvider>
      </AuthProvider>
      <Toaster />
    </BrandProvider>
  );
}
