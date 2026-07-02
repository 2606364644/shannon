import { describe, it, expect } from "vitest";
import type { NdjsonEvent, Workspace, Vulnerability } from "./types";

describe("NdjsonEvent", () => {
  it("PhaseEvent has common + phase fields", () => {
    const ev: NdjsonEvent = {
      ts: "2026-07-02T09:44:01.123Z",
      category: "PHASE",
      type: "PhaseEvent",
      phase: "recon",
      event: "start",
      steps: ["s1", "s2"],
      step_intents: ["", ""],
    };
    expect(ev.type).toBe("PhaseEvent");
    expect(ev.phase).toBe("recon");
  });

  it("scan_end control row is a NdjsonEvent", () => {
    const ev: NdjsonEvent = {
      ts: "2026-07-02T09:50:00.000Z",
      category: "CONTROL",
      type: "scan_end",
      status: "completed",
      returncode: 0,
    };
    expect(ev.type).toBe("scan_end");
  });

  it("Vulnerability carries externally_exploitable + merge_source", () => {
    const v: Vulnerability = {
      ID: "SSRF-VULN-01",
      vulnerability_type: "URL_Manipulation",
      externally_exploitable: true,
      merge_source: "llm-only",
      confidence: "needs_review",
      source_endpoint: "GET /research",
    };
    expect(v.merge_source).toBe("llm-only");
  });

  it("Workspace satisfies type contract", () => {
    const ws: Workspace = {
      name: "test_ws",
      scan_type: "whitebox",
      status: "completed",
      created_at: 1719890000,
    };
    expect(ws.scan_type).toBe("whitebox");
  });
});
