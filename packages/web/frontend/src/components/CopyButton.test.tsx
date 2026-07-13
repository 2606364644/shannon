import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import i18n from "@/i18n";
import { CopyButton } from "./CopyButton";

beforeEach(() => {
  i18n.changeLanguage("zh");
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
  });
});
afterEach(() => cleanup());

describe("CopyButton", () => {
  it("点击调用 clipboard.writeText(value)", async () => {
    render(<CopyButton value="https://x/foo.git" ariaLabel="复制来源 URL" />);
    fireEvent.click(screen.getByRole("button", { name: "复制来源 URL" }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith("https://x/foo.git"));
  });

  it("复制成功后切换为已复制反馈（Check）", async () => {
    render(<CopyButton value="https://x/foo.git" ariaLabel="复制来源 URL" />);
    fireEvent.click(screen.getByRole("button", { name: "复制来源 URL" }));
    // aria-label 在 done 态切到「已复制」，1.2s 后回滚（此处只验证进入 done 态）
    await waitFor(() => expect(screen.getByRole("button", { name: "已复制" })).toBeInTheDocument());
  });
});
