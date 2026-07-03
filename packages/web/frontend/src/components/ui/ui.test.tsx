import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

describe("shadcn ui 组件集成", () => {
  it("Button 各 variant 渲染不炸", () => {
    const { container } = render(
      <>
        <Button>default</Button>
        <Button variant="secondary">sec</Button>
        <Button variant="ghost">ghost</Button>
        <Button variant="destructive">destructive</Button>
        <Button variant="outline">outline</Button>
      </>
    );
    expect(screen.getByRole("button", { name: "default" })).toBeInTheDocument();
    expect(container.querySelectorAll("button")).toHaveLength(5);
  });

  it("Button size=icon 是方形（a11y：aria-label）", () => {
    render(<Button size="icon" aria-label="操作" />);
    expect(screen.getByRole("button", { name: "操作" })).toBeInTheDocument();
  });

  it("Input 渲染 + placeholder", () => {
    render(<Input placeholder="输入" />);
    expect(screen.getByPlaceholderText("输入")).toBeInTheDocument();
  });

  it("Badge 渲染", () => {
    render(<Badge>badge</Badge>);
    expect(screen.getByText("badge")).toBeInTheDocument();
  });

  it("Card 含 header/title/content 子组件", () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>title</CardTitle>
        </CardHeader>
        <CardContent>content</CardContent>
      </Card>
    );
    expect(screen.getByText("title")).toBeInTheDocument();
    expect(screen.getByText("content")).toBeInTheDocument();
  });
});
