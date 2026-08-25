import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { downloadTextFile, reportDownloadFilename } from "./download";

// jsdom 未实现 URL.createObjectURL/revokeObjectURL —— stub 成可断言的 mock（configurable 便于 afterEach 摘除）。
const createObjectURL = vi.fn((_blob: Blob) => "blob:mock-url");
const revokeObjectURL = vi.fn();

beforeEach(() => {
  Object.defineProperty(URL, "createObjectURL", { value: createObjectURL, configurable: true });
  Object.defineProperty(URL, "revokeObjectURL", { value: revokeObjectURL, configurable: true });
});

afterEach(() => {
  delete (URL as { createObjectURL?: unknown }).createObjectURL;
  delete (URL as { revokeObjectURL?: unknown }).revokeObjectURL;
  vi.restoreAllMocks();
  createObjectURL.mockClear();
  revokeObjectURL.mockClear();
});

describe("downloadTextFile", () => {
  it("Blob + <a download> 触发下载，且用后 revoke", () => {
    // mockImplementation 吞掉真实 click：jsdom 对 a.click() 报 "Not implemented: navigation" 噪音
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    downloadTextFile("scan1-report.md", "# 报告\n正文");

    // Blob：markdown 类型 + 完整文本字节（jsdom Blob 无 .text()，用 size 佐证未截断）
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    const blob = createObjectURL.mock.calls[0][0] as Blob;
    expect(blob.type).toContain("text/markdown");
    expect(blob.size).toBe(new TextEncoder().encode("# 报告\n正文").length);
    // <a>：href 指向 blob url、download 文件名、真实 click
    const a = clickSpy.mock.contexts[0] as HTMLAnchorElement;
    expect(a.href).toBe("blob:mock-url");
    expect(a.download).toBe("scan1-report.md");
    expect(clickSpy).toHaveBeenCalledTimes(1);
    // 用后释放
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
  });
});

describe("reportDownloadFilename", () => {
  it("单报告（无 track/run）：{scanId}-report.md", () => {
    expect(reportDownloadFilename("scan1")).toBe("scan1-report.md");
  });

  it("组合子 tab（scan 级）：{scanId}-report-{track}.md", () => {
    expect(reportDownloadFilename("scan1", "whitebox")).toBe("scan1-report-whitebox.md");
    expect(reportDownloadFilename("scan1", "combined")).toBe("scan1-report-combined.md");
  });

  it("run 级：{scanId}-run-{runId}-report-{track}.md", () => {
    expect(reportDownloadFilename("scan1", "blackbox", "run-3"))
      .toBe("scan1-run-run-3-report-blackbox.md");
  });

  it("run 级兜底（无 track）：{scanId}-run-{runId}-report.md", () => {
    expect(reportDownloadFilename("scan1", undefined, "run-2")).toBe("scan1-run-run-2-report.md");
  });
});
