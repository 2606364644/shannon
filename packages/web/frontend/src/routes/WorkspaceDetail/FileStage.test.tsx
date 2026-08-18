import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import i18n from "@/i18n";
import { FileStage } from "./FileStage";
import type { DeliverablesFile } from "../../api/types";

vi.mock("@/api/useApiResource", () => ({
  useApiText: (p: string | null) => ({ text: p ? "content[truncated: showing 1 of 999 characters — full file on disk]" : "", loading: false, error: undefined }),
}));

const file: DeliverablesFile = { path: "whitebox/code_index.json", size: 999, kind: "md", tier: "intermediate" };

describe("FileStage 后端截断提示", () => {
  beforeEach(() => i18n.changeLanguage("zh"));
  afterEach(() => i18n.changeLanguage("zh"));
  it("后端截断标注时展示提示横幅", () => {
    render(
      <MemoryRouter>
        <FileStage ws="w" scanId="s" file={file} onBack={() => {}} />
      </MemoryRouter>,
    );
    expect(screen.getByText(/服务端已截断/)).toBeInTheDocument();
  });
});
