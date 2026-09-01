import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, waitFor } from "@testing-library/react";
import { RichText } from "./RichText";
import i18n from "@/i18n";

describe("RichText Markdown 代码块", () => {
  beforeEach(() => i18n.changeLanguage("zh"));

  it("结构化报告 narrative 中的 fenced code 也带复制按钮与语言角标", () => {
    const { container } = render(
      <RichText text={"修复示例：\n\n```bash\nnpm audit fix\n```"} />,
    );
    const blocks = container.querySelectorAll('pre[data-testid="code-block"]');
    expect(blocks).toHaveLength(1);
    expect(blocks[0].querySelector(".copy-btn")).not.toBeNull();
    expect(container.querySelector('[data-testid="code-lang"]')?.textContent).toBe("bash");
  });

  it("无语言标记的 fenced code 仍可复制（只是不显示语言角标）", () => {
    const { container } = render(<RichText text={"```\nplain\n```"} />);
    expect(container.querySelectorAll('pre[data-testid="code-block"] .copy-btn')).toHaveLength(1);
    expect(container.querySelector('[data-testid="code-lang"]')).toBeNull();
  });

  it("inline code 不加复制按钮", () => {
    const { container } = render(<RichText text="正文 `inline_x` 结尾" />);
    expect(container.querySelector("code")?.textContent).toBe("inline_x");
    expect(container.querySelector("pre")).toBeNull();
    expect(container.querySelector(".copy-btn")).toBeNull();
  });
});

describe("RichText Markdown 代码块复制", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("点击按钮复制代码块纯文本（不含语言角标 / 按钮文字）", async () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true, writable: true });
    let captured = "";
    const exec = vi.fn(() => {
      captured = document.querySelector("textarea")?.value ?? "";
      return true;
    });
    document.execCommand = exec as unknown as typeof document.execCommand;
    try {
      const { container } = render(<RichText text={"```bash\ncurl http://x\n```"} />);
      fireEvent.click(container.querySelector<HTMLButtonElement>(".copy-btn")!);
      expect(exec).toHaveBeenCalledWith("copy");
      expect(captured).toBe("curl http://x\n");
      await waitFor(() => expect(container.querySelector(".copy-btn")).toHaveAccessibleName("已复制"));
    } finally {
      delete (document as { execCommand?: unknown }).execCommand;
    }
  });
});
