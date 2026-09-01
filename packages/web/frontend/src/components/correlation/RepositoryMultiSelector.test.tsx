import { fireEvent, render, screen } from "@testing-library/react";
import { RepositoryMultiSelector } from "./RepositoryMultiSelector";

const repos = [
  { name: "web", state: "ready" as const },
  { name: "order", state: "ready" as const },
  { name: "broken", state: "failed" as const },
];

it("selects multiple ready repositories", () => {
  const onChange = vi.fn();
  render(<RepositoryMultiSelector repos={repos} selected={["web"]} onChange={onChange} />);
  fireEvent.click(screen.getByRole("checkbox", { name: /order/ }));
  expect(onChange).toHaveBeenCalledWith(["web", "order"]);
  expect((screen.getByRole("checkbox", { name: /broken/ }) as HTMLInputElement).disabled).toBe(true);
});
