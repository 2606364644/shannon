import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import LanguageSwitcher from "./LanguageSwitcher";
import i18n from "@/i18n";

describe("LanguageSwitcher", () => {
  beforeEach(() => {
    localStorage.removeItem("shannon.lang");
    i18n.changeLanguage("zh");
  });

  it("中文时显示 EN 按钮", () => {
    render(<LanguageSwitcher />);
    expect(screen.getByLabelText("切换语言")).toHaveTextContent("EN");
  });

  it("点击切换到英文并持久化", () => {
    render(<LanguageSwitcher />);
    fireEvent.click(screen.getByLabelText("切换语言"));
    expect(i18n.language).toMatch(/^en/);
    expect(localStorage.getItem("shannon.lang")).toBe("en");
  });
});
