import { describe, it, expect } from "vitest";
import { ApiError } from "@/api/client";
import { apiErrorMessage } from "./apiError";

describe("apiErrorMessage", () => {
  it("从 ApiError 提取字符串 detail（业务 HTTPException，如 409 重名）", () => {
    const e = new ApiError(409, { detail: "username exists" });
    expect(apiErrorMessage(e, "fallback")).toBe("username exists");
  });

  it("从 422 数组 detail 提取首条 msg（pydantic 校验错）", () => {
    const e = new ApiError(422, {
      detail: [{ msg: "field required", loc: ["body", "x"], type: "value_error.missing" }],
    });
    expect(apiErrorMessage(e, "fallback")).toBe("field required");
  });

  it("非 ApiError（如网络错误）返回 fallback", () => {
    expect(apiErrorMessage(new Error("network down"), "fallback")).toBe("fallback");
  });

  it("ApiError 无 detail 字段返回 fallback（如 500 纯文本响应）", () => {
    const e = new ApiError(500, "internal server error");
    expect(apiErrorMessage(e, "fallback")).toBe("fallback");
  });

  it("ApiError detail 为空字符串返回 fallback", () => {
    const e = new ApiError(400, { detail: "" });
    expect(apiErrorMessage(e, "fallback")).toBe("fallback");
  });
});
