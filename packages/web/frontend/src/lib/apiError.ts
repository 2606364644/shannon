import { ApiError } from "@/api/client";

/** 从 ApiError 提取后端可读原因；非 ApiError 或无可读 detail 时返回 fallback。
 *  兼容 FastAPI 两种 detail 形态：字符串（业务 HTTPException，如 "username exists"）
 *  与数组（422 pydantic 校验，[{msg, loc, type}]）。取法参照 FileSystemPicker.tsx
 *  （取 str）与 ScanNewPage.tsx 的 renderError（取 array[0].msg）。 */
export function apiErrorMessage(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    const detail = (e.body as { detail?: unknown } | null | undefined)?.detail;
    if (typeof detail === "string" && detail) return detail;
    if (Array.isArray(detail)) {
      const first = detail[0] as { msg?: unknown } | undefined;
      if (first && typeof first.msg === "string" && first.msg) return first.msg;
    }
  }
  return fallback;
}

/** 422 detail 为 {code:"provider_incomplete", missing:[...]}（工作区模型配置缺失/错误；
 *  /api/scan 与认证「测试登录」共用契约）→ 返回 missing 字段列表，否则 null。
 *  调用方据此展示「前往工作区设置补全 LLM 配置」指引，而非通用失败文案。 */
export function providerIncompleteMissing(e: unknown): string[] | null {
  if (!(e instanceof ApiError)) return null;
  const detail = (e.body as { detail?: unknown } | null | undefined)?.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const d = detail as { code?: unknown; missing?: unknown };
    if (d.code === "provider_incomplete" && Array.isArray(d.missing)) {
      const missing = d.missing.filter((m): m is string => typeof m === "string");
      if (missing.length > 0) return missing;
    }
  }
  return null;
}
