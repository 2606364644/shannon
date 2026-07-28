import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useSystemStatus } from "@/api/systemStatus";
import { apiPut, ApiError } from "@/api/client";
import i18n from "@/i18n";

/**
 * 平台品牌名 — 左上角字标 + 浏览器标签页 title 的唯一数据源。
 *
 * 链路: SUPERNOVA_WEB_BRAND_NAME env / 运行时覆盖(branding.json) → /api/system-status.brand_name
 *       → 此 context → TopBar 字标 + document.title。默认 "Supernova"。
 *
 * 运行时改名(admin 经设置页)走 setBrand():乐观更新本 context(左上角立即变)+
 * PUT /api/branding 落盘;失败回滚并暴露 error。env 未设 / 接口失败 / 未包 Provider
 * (单元测试直渲染 TopBar) 时均回落默认,保证左上角永远有可读字标。
 */
const DEFAULT_BRAND = "Supernova";

interface BrandContextValue {
  brand: string;
  /** 乐观改名 + 落盘。返回 true=成功(已生效), false=失败(已回滚)。 */
  setBrand: (name: string | null) => Promise<boolean>;
  saving: boolean;
  error: string | null;
}

const BrandContext = createContext<BrandContextValue>({
  brand: DEFAULT_BRAND,
  setBrand: async () => false,
  saving: false,
  error: null,
});

export function BrandProvider({ children }: { children: ReactNode }) {
  const { data } = useSystemStatus();
  const serverBrand = data?.brand_name?.trim() || DEFAULT_BRAND;
  const [brand, setBrandState] = useState<string>(serverBrand);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 服务器真相追踪:初始跟随 system-status;改名后乐观领先,直到下次 system-status 刷新。
  const lastServer = useRef<string>(serverBrand);

  // system-status 刷新(改名成功后其它 useSystemStatus 实例 / 重开页面)→ 对齐服务器值。
  useEffect(() => {
    if (serverBrand && serverBrand !== lastServer.current) {
      lastServer.current = serverBrand;
      setBrandState(serverBrand);
    } else if (!lastServer.current) {
      lastServer.current = serverBrand;
      setBrandState(serverBrand);
    }
  }, [serverBrand]);

  // 浏览器标签页 title 跟随品牌名(index.html 初始 <title>Supernova</title> 作首屏兜底)。
  useEffect(() => {
    document.title = brand;
  }, [brand]);

  const setBrand = useCallback(async (name: string | null): Promise<boolean> => {
    const prev = brand;
    setSaving(true);
    setError(null);
    // 乐观:即时改左上角 + title。
    const optimistic = name && name.trim() ? name.trim() : lastServer.current;
    setBrandState(optimistic);
    try {
      const res = await apiPut<{ brand_name: string | null; effective?: string }>("/branding", { brand_name: name });
      // 落盘成功:以服务器返回的 effective(已解析 override/env/default)为权威。
      // 清除覆盖时 effective = 部署默认(env/default),避免前端猜错。
      const effective = (res.effective && res.effective.trim()) || optimistic;
      lastServer.current = effective;
      setBrandState(effective);
      return true;
    } catch (e) {
      // 失败回滚。
      setBrandState(prev);
      setError(
        e instanceof ApiError
          ? i18n.t("branding.errors.saveFailedStatus", { status: e.status })
          : i18n.t("branding.errors.saveFailed"),
      );
      return false;
    } finally {
      setSaving(false);
    }
  }, [brand]);

  return (
    <BrandContext.Provider value={{ brand, setBrand, saving, error }}>
      {children}
    </BrandContext.Provider>
  );
}

/** 取当前品牌名(左上角字标);无 Provider(单元测试) 时返回默认 "Supernova"。 */
export function useBrand(): string {
  return useContext(BrandContext).brand;
}

/** 品牌名编辑器:改名 + 落盘状态。管理员在设置页用。 */
export function useBrandEditor() {
  return useContext(BrandContext);
}
