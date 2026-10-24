import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PreconditionList } from "./precondition-list";

describe("PreconditionList", () => {
  it("причина показывается после названия, но не повторяет его", () => {
    render(
      <PreconditionList
        preconditions={[
          { name: "Нет выхода за границу", passed: false, reason: "в фрагменте есть continue" },
          { name: "`break` выходит за границу", passed: false, reason: "`break` выходит за границу" },
          { name: "Типы известны", passed: true, reason: null },
        ]}
      />,
    );
    const rows = screen.getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("Нет выхода за границу — в фрагменте есть continue");
    expect(rows[1].textContent).toBe("`break` выходит за границу");
    expect(rows[2]).toHaveAttribute("data-passed", "true");
  });

  it("пустой список ничего не рисует", () => {
    const { container } = render(<PreconditionList preconditions={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
