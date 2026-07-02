import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import App from "./App";

describe("App", () => {
  it("renders the Shannon Web title", () => {
    render(<App />);
    expect(screen.getByText(/Shannon Web/i)).toBeInTheDocument();
  });
});
