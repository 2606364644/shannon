import { fireEvent, render, screen } from "@testing-library/react";
import i18n from "@/i18n";
import { RepositoryMultiSelector } from "./RepositoryMultiSelector";

// 组件测试默认语言可能是 en（detector 走 localStorage）——中文文案断言前显式切 zh。
beforeEach(() => { i18n.changeLanguage("zh"); });

const repos = [
  { name: "web", state: "ready" as const },
  { name: "order", state: "ready" as const },
  { name: "order-jobs", state: "stale" as const },
  { name: "broken", state: "failed" as const },
];

it("selects multiple ready repositories", () => {
  const onChange = vi.fn();
  render(<RepositoryMultiSelector repos={repos} selected={["web"]} onChange={onChange} />);
  fireEvent.click(screen.getByRole("checkbox", { name: /order-jobs/ }));
  expect(onChange).toHaveBeenCalledWith(["web", "order-jobs"]);
  expect((screen.getByRole("checkbox", { name: /broken/ }) as HTMLInputElement).disabled).toBe(true);
});

it("filters the list by search query (substring, case-insensitive)", () => {
  const onChange = vi.fn();
  render(<RepositoryMultiSelector repos={repos} selected={[]} onChange={onChange} />);
  fireEvent.change(screen.getByLabelText("搜索仓库…"), { target: { value: "ORD" } });
  expect(screen.getByRole("checkbox", { name: "order" })).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: /order-jobs/ })).toBeInTheDocument();
  expect(screen.queryByRole("checkbox", { name: "web" })).toBeNull();
});

it("filter empty state shows when nothing matches", () => {
  render(<RepositoryMultiSelector repos={repos} selected={[]} onChange={() => {}} />);
  fireEvent.change(screen.getByLabelText("搜索仓库…"), { target: { value: "nope" } });
  expect(screen.getByText("没有匹配的仓库")).toBeInTheDocument();
});

it("select-all only picks filtered selectable repos and never disabled ones", () => {
  const onChange = vi.fn();
  render(<RepositoryMultiSelector repos={repos} selected={[]} onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: "全选" }));
  expect(onChange).toHaveBeenCalledWith(["web", "order", "order-jobs"]);

  // 搜索过滤后：全选只加可见项，不误选被过滤掉的仓
  onChange.mockClear();
  fireEvent.change(screen.getByLabelText("搜索仓库…"), { target: { value: "jobs" } });
  fireEvent.click(screen.getByRole("button", { name: "全选" }));
  expect(onChange).toHaveBeenCalledWith(["order-jobs"]);
});

it("select-all is disabled when every visible selectable repo is already selected", () => {
  render(<RepositoryMultiSelector repos={repos} selected={["web", "order", "order-jobs"]} onChange={() => {}} />);
  expect((screen.getByRole("button", { name: "全选" }) as HTMLButtonElement).disabled).toBe(true);
});

it("clear-all empties the selection", () => {
  const onChange = vi.fn();
  render(<RepositoryMultiSelector repos={repos} selected={["web", "order"]} onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: "清空" }));
  expect(onChange).toHaveBeenCalledWith([]);
});

it("selected count badge reflects the selection size", () => {
  render(<RepositoryMultiSelector repos={repos} selected={["web", "order"]} onChange={() => {}} />);
  expect(screen.getByText("已选 2")).toBeInTheDocument();
});
