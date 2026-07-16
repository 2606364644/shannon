import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "./table";

describe("Table 视觉精修不变量", () => {
  it("TableHead 有表头轻底色（bg-muted），与正文分层", () => {
    const { container } = render(
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>列</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell>v</TableCell>
          </TableRow>
        </TableBody>
      </Table>
    );
    const th = container.querySelector("th");
    expect(th?.className).toMatch(/bg-muted/);
  });

  it("TableRow hover 足够明显（bg-muted/70，非过淡的 /50）", () => {
    const { container } = render(
      <Table>
        <TableBody>
          <TableRow>
            <TableCell>v</TableCell>
          </TableRow>
        </TableBody>
      </Table>
    );
    const tr = container.querySelector("tbody tr");
    expect(tr?.className).toMatch(/hover:bg-muted\/70/);
    expect(tr?.className).not.toMatch(/hover:bg-muted\/50/);
  });
});
