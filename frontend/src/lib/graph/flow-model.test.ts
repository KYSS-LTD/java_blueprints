import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";
import flowsJson from "@/mocks/fixtures/flows.json";

import {
  ancestorsOf,
  buildFlowModel,
  defaultCollapsed,
  isContainer,
  methodDisplayName,
} from "./flow-model";

type FlowGraph = components["schemas"]["FlowGraph"];

const flows = flowsJson as unknown as Record<string, FlowGraph>;
const processOrders = flows["Main.java#Main.processOrders(List<Order>)"];
const processCommand = flows["Main.java#Main.processCommand(String, Scanner)"];

describe("buildFlowModel", () => {
  const model = buildFlowModel(processOrders);

  it("строит дерево блоков по parent_id: предок раньше потомков", () => {
    const index = new Map(model.order.map((id, i) => [id, i]));
    for (const block of model.blocks.values()) {
      if (block.parentId !== null) {
        expect(index.get(block.parentId)!).toBeLessThan(index.get(block.id)!);
      }
    }
    expect(model.order).toHaveLength(model.blocks.size);
  });

  it("цикл и try — контейнеры, условие и операция — нет", () => {
    const kinds = new Map([...model.blocks.values()].map((b) => [b.kind, isContainer(b)]));
    expect(kinds.get("loop_foreach")).toBe(true);
    expect(kinds.get("try")).toBe(true);
    expect(kinds.get("condition")).toBe(false);
    expect(kinds.get("operation")).toBe(false);
  });

  it("вызовы методов вне схемы становятся блоками «внешний вызов»", () => {
    const external = [...model.blocks.values()].filter((b) => b.kind === "external");
    expect(external.length).toBeGreaterThan(0);
    for (const block of external) {
      expect(block.target).toBeDefined();
      expect(block.code).toBe(methodDisplayName(block.target!));
      const edge = model.edges.find((e) => e.target === block.id);
      expect(edge?.kind).toBe("call");
    }
  });

  it("убирает рёбра «контейнер → содержимое», но запоминает тип исключения у catch", () => {
    for (const edge of model.edges) {
      expect(ancestorsOf(model, edge.target)).not.toContain(edge.source);
    }
    const catchBlock = [...model.blocks.values()].find((b) => b.kind === "catch");
    expect(catchBlock?.exception).toBe("OrderValidationException");
  });

  it("сохраняет петли и ветки Да/Нет", () => {
    const kinds = new Set(model.edges.map((e) => e.kind));
    expect(kinds).toContain("back");
    expect(kinds).toContain("true");
    expect(kinds).toContain("false");
    const yes = model.edges.find((e) => e.kind === "true");
    expect(yes?.label).toBe("Да");
  });

  it("не теряет ни одного узла контракта", () => {
    const own = [...model.blocks.values()].filter((b) => b.kind !== "external");
    expect(own).toHaveLength(processOrders.nodes.length);
  });
});

describe("все блок-схемы демо-монолита", () => {
  it("строятся без потерь: у каждого блока есть место в порядке обхода", () => {
    for (const [id, flow] of Object.entries(flows)) {
      const model = buildFlowModel(flow);
      expect(model.order.length, id).toBe(model.blocks.size);
      for (const edge of model.edges) {
        expect(model.blocks.has(edge.source), `${id}: ${edge.id}`).toBe(true);
        expect(model.blocks.has(edge.target), `${id}: ${edge.id}`).toBe(true);
      }
    }
  });
});

describe("defaultCollapsed", () => {
  it("в небольшой схеме ничего не сворачивает", () => {
    expect(defaultCollapsed(buildFlowModel(processOrders)).size).toBe(0);
  });

  it("в большой сворачивает контейнеры глубже третьего уровня", () => {
    const model = buildFlowModel(processCommand);
    const collapsed = defaultCollapsed(model, 1, 10);
    expect(collapsed.size).toBeGreaterThan(0);
    for (const id of collapsed) expect(isContainer(model.blocks.get(id)!)).toBe(true);
  });
});
