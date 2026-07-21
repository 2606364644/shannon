import { createContext, useContext, useEffect } from "react";
import type { ReactNode } from "react";
import { useSystemStatus } from "@/api/systemStatus";

/**
 * 平台品牌名 — 左上角字标 + 浏览器标签页 title 的唯一数据源。
 *
 * 链路: SUPERNOVA_WEB_BRAND_NAME env → /api/system-status.brand_name → 此 context。
 * 默认 "Supernova"; env 未设 / 接口失败 / 未包 Provider(单元测试直渲染 TopBar) 时均回落默认,
 * 保证左上角永远有可读字标。SettingsPage 仍独立用 useSystemStatus 展示实时系统状态。
 */
const DEFAULT_BRAND = "Supernova";

const BrandContext = createContext<string>(DEFAULT_BRAND);

export function BrandProvider({ children }: { children: ReactNode }) {
  const { data } = useSystemStatus();
  const brand = data?.brand_name?.trim() || DEFAULT_BRAND;

  // 浏览器标签页 title 跟随品牌名(index.html 初始 <title>Supernova</title> 作首屏兜底)。
  useEffect(() => {
    document.title = brand;
  }, [brand]);

  return <BrandContext.Provider value={brand}>{children}</BrandContext.Provider>;
}

/** 取当前品牌名; 无 Provider(单元测试直渲染 TopBar) 时返回默认 "Supernova"。 */
export function useBrand(): string {
  return useContext(BrandContext);
}
