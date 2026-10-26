import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Brand } from "./components/Brand";

describe("brand", () => {
  it("renders the canonical DataPipette name", () => {
    render(<Brand />);

    expect(screen.getByLabelText("DataPipette")).toHaveTextContent("DataPipette");
    expect(screen.queryByText("DatePipette")).not.toBeInTheDocument();
  });
});
