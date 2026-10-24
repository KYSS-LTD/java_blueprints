import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";
import flowsJson from "@/mocks/fixtures/flows.json";

import { buildFlowModel } from "./flow-model";
import { locateBlock, rangeContains } from "./locate";

type FlowGraph = components["schemas"]["FlowGraph"];

const flows = flowsJson as unknown as Record<string, FlowGraph>;
const model = buildFlowModel(flows["Main.java#Main.processOrders(List<Order>)"]);

const range = { file: "A.java", start_line: 3, start_col: 5, end_line: 5, end_col: 10 };

describe("rangeContains", () => {
  it("учитывает границы по строкам и колонкам", () => {
    expect(rangeContains(range, 3, 5)).toBe(true);
    expect(rangeContains(range, 3, 4)).toBe(false);
    expect(rangeContains(range, 4, 1)).toBe(true);
    expect(rangeContains(range, 5, 10)).toBe(true);
    expect(rangeContains(range, 5, 11)).toBe(false);
    expect(rangeContains(range, 2, 99)).toBe(false);
    expect(rangeContains(range, 6, 1)).toBe(false);
  });
});

describe("locateBlock", () => {
  it("находит самый глубокий блок: операция внутри try внутри цикла", () => {
    const op = [...model.blocks.values()].find((b) => b.kind === "operation" && b.range)!;
    const found = locateBlock(model, op.range!.start_line, op.range!.start_col + 1);
    expect(found).toBe(op.id);
    expect(model.blocks.get(found!)!.depth).toBeGreaterThan(1);
  });

  it("строка цикла даёт сам цикл, а не вложенные блоки", () => {
    const loop = [...model.blocks.values()].find((b) => b.kind === "loop_foreach")!;
    expect(locateBlock(model, loop.range!.start_line, loop.range!.start_col)).toBe(loop.id);
  });

  it("сигнатура метода даёт блок «Метод»", () => {
    const method = [...model.blocks.values()].find((b) => b.kind === "method")!;
    expect(locateBlock(model, method.range!.start_line, method.range!.start_col)).toBe(method.id);
  });

  it("позиция вне метода — ничего", () => {
    expect(locateBlock(model, 1, 1)).toBeNull();
  });

  it("каждый блок находится по началу своего диапазона не глубже себя", () => {
    for (const block of model.blocks.values()) {
      if (!block.range) continue;
      const found = locateBlock(model, block.range.start_line, block.range.start_col);
      expect(found, block.id).not.toBeNull();
      expect(model.blocks.get(found!)!.depth, block.id).toBeGreaterThanOrEqual(block.depth);
    }
  });
});
