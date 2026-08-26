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

describe("FileStage 下载按钮", () => {
  beforeEach(() => i18n.changeLanguage("zh"));
  afterEach(() => i18n.changeLanguage("zh"));

  it("scan 级：href 带 ?download=1，download 属性为 track 前缀文件名", () => {
    render(
      <MemoryRouter>
        <FileStage ws="w" scanId="s" file={file} onBack={() => {}} />
      </MemoryRouter>,
    );
    const a = screen.getByRole("link", { name: /下载/ }) as HTMLAnchorElement;
    // jsdom 的 a.href 是解析后的绝对 URL，断言原始属性值
    expect(a.getAttribute("href")).toBe(
      "/api/workspaces/w/scans/s/deliverables?path=whitebox%2Fcode_index.json&download=1");
    expect(a.download).toBe("whitebox-code_index.json");
  });

  it("run 级（runId 传入）：href 走 blackbox-runs 端点，strip 路径无前缀仅 basename", () => {
    const runFile: DeliverablesFile = { path: "report.md", size: 10, kind: "md" };
    render(
      <MemoryRouter>
        <FileStage ws="w" scanId="s" file={runFile} runId="run-2" onBack={() => {}} />
      </MemoryRouter>,
    );
    const a = screen.getByRole("link", { name: /下载/ }) as HTMLAnchorElement;
    expect(a.getAttribute("href")).toBe(
      "/api/workspaces/w/scans/s/blackbox-runs/run-2/deliverables?path=report.md&download=1");
    expect(a.download).toBe("report.md");
  });

  it("empty_json 也渲染下载按钮（后端附件不受预览 kind 限制）", () => {
    const emptyFile: DeliverablesFile = { path: "whitebox/x_exploitation_queue.json", size: 2, kind: "empty_json" };
    render(
      <MemoryRouter>
        <FileStage ws="w" scanId="s" file={emptyFile} onBack={() => {}} />
      </MemoryRouter>,
    );
    const a = screen.getByRole("link", { name: /下载/ }) as HTMLAnchorElement;
    expect(a.href).toContain("download=1");
    expect(a.download).toBe("whitebox-x_exploitation_queue.json");
  });
});
