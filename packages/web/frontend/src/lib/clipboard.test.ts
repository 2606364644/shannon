import { afterEach, describe, expect, it, vi } from "vitest";
import { copyToClipboard } from "./clipboard";

/** jsdom 的 navigator.clipboard 默认不存在；其他测试文件的 Object.assign 会跨用例残留，
 *  这里显式 defineProperty 控制场景（configurable 保证可反复改写）。 */
function stubClipboard(value: unknown) {
  Object.defineProperty(navigator, "clipboard", { value, configurable: true, writable: true });
}

afterEach(() => {
  stubClipboard(undefined);
  // execCommand 是实例赋值遮蔽原型，删掉恢复 jsdom 原行为
  delete (document as { execCommand?: unknown }).execCommand;
  vi.restoreAllMocks();
});

describe("copyToClipboard", () => {
  it("安全上下文：走 navigator.clipboard.writeText", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    stubClipboard({ writeText });
    await expect(copyToClipboard("hello")).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("hello");
  });

  it("非安全上下文（navigator.clipboard === undefined，如 http://内网IP:7878 部署访问）：fallback 到 execCommand", async () => {
    stubClipboard(undefined);
    const exec = vi.fn(() => true);
    document.execCommand = exec as unknown as typeof document.execCommand;
    await expect(copyToClipboard("curl http://t")).resolves.toBe(true);
    expect(exec).toHaveBeenCalledWith("copy");
  });

  it("fallback 复制的正是目标文本（临时 textarea.value）", async () => {
    stubClipboard(undefined);
    let captured = "";
    document.execCommand = (() => {
      captured = document.querySelector("textarea")?.value ?? "";
      return true;
    }) as unknown as typeof document.execCommand;
    await copyToClipboard("line1\nline2");
    expect(captured).toBe("line1\nline2");
  });

  it("execCommand 返回 false（浏览器策略拒绝）→ 返回 false（调用方可 toast 报错）", async () => {
    stubClipboard(undefined);
    document.execCommand = (() => false) as unknown as typeof document.execCommand;
    await expect(copyToClipboard("x")).resolves.toBe(false);
  });

  it("writeText reject（权限拒绝）→ 落 execCommand fallback 再试", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    stubClipboard({ writeText });
    const exec = vi.fn(() => true);
    document.execCommand = exec as unknown as typeof document.execCommand;
    await expect(copyToClipboard("y")).resolves.toBe(true);
    expect(exec).toHaveBeenCalledWith("copy");
  });

  it("临时 textarea 用后即删（不残留 DOM）", async () => {
    stubClipboard(undefined);
    document.execCommand = (() => true) as unknown as typeof document.execCommand;
    await copyToClipboard("z");
    expect(document.querySelector("textarea")).toBeNull();
  });
});
