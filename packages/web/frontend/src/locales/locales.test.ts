import { describe, it, expect } from "vitest";
import zh from "./zh.json";
import en from "./en.json";

describe("auth i18n", () => {
  it("zh.auth.login.submit 是真中文", () => {
    expect((zh as any).auth.login.submit).toBe("登录");
  });
  it("zh.auth.role.admin 是真中文（守 zh 值漏翻陷阱）", () => {
    expect((zh as any).auth.role.admin).toBe("管理员");
    expect((zh as any).auth.role.user).toBe("用户");
  });
  it("zh.auth.sessionExpired 真中文", () => {
    expect((zh as any).auth.sessionExpired).toBe("会话已过期，请重新登录");
  });
  it("en has auth keys", () => {
    expect((en as any).auth.login.submit).toBe("Sign in");
    expect((en as any).auth.role.admin).toBe("Admin");
  });
});
