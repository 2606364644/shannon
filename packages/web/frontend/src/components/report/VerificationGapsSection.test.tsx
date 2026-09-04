import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { describe, expect, it, vi } from "vitest";

import i18n from "@/i18n";
import { VerificationGapsSection } from "./VerificationGapsSection";

function renderSection(gaps: Array<{ vuln_id: string; reason?: string | null }>) {
  return render(
    <I18nextProvider i18n={i18n}>
      <VerificationGapsSection gaps={gaps} onLocate={() => {}} />
    </I18nextProvider>,
  );
}

describe("VerificationGapsSection（spec 2026-09-03 §8 验证缺口节）", () => {
  it("gaps 非空：渲染节 + 每条 ID 与真实原因", () => {
    renderSection([
      { vuln_id: "XSS-VULN-01", reason: "agent 未完成验证闭环（登记 0/15）；工具轨迹显示已对该端点发起过请求，未产出结论" },
      { vuln_id: "INJ-VULN-02", reason: "agent 已登记验证结论但被校验拒收：L1 schema: exploited.severity Field required" },
    ]);
    expect(screen.getByTestId("verification-gaps-section")).toBeInTheDocument();
    expect(screen.getByText("XSS-VULN-01")).toBeInTheDocument();
    expect(screen.getByText(/登记 0\/15/)).toBeInTheDocument();
    expect(screen.getByText(/L1 schema/)).toBeInTheDocument();
  });

  it("gaps 空/缺省：不渲染节", () => {
    const { rerender } = renderSection([]);
    expect(screen.queryByTestId("verification-gaps-section")).not.toBeInTheDocument();
    rerender(
      <I18nextProvider i18n={i18n}>
        <VerificationGapsSection gaps={undefined} onLocate={() => {}} />
      </I18nextProvider>,
    );
    expect(screen.queryByTestId("verification-gaps-section")).not.toBeInTheDocument();
  });

  it("reason 缺省：ID 仍渲染、不崩", () => {
    renderSection([{ vuln_id: "V-1" }]);
    expect(screen.getByText("V-1")).toBeInTheDocument();
  });

  it("ID 可点击定位（onLocate 回调）", () => {
    const onLocate = vi.fn();
    render(
      <I18nextProvider i18n={i18n}>
        <VerificationGapsSection gaps={[{ vuln_id: "V-9", reason: "r" }]} onLocate={onLocate} />
      </I18nextProvider>,
    );
    screen.getByText("V-9").click();
    expect(onLocate).toHaveBeenCalledWith("V-9");
  });
});
