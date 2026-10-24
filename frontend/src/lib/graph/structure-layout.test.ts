// @vitest-environment node
import ELK from "elkjs/lib/elk.bundled.js";
import { describe, expect, it } from "vitest";

import structureJson from "@/mocks/fixtures/structure.json";

import { collectPlacements } from "./flow-layout";
import { buildStructureLayout, isTypeNode, memberCounts, type StructureGraph } from "./structure-layout";

const graph = structureJson as unknown as StructureGraph;
const elk = new ELK();

describe("buildStructureLayout", () => {
  it("«Типы»: только карточки типов и рёбра наследования", () => {
    const layout = buildStructureLayout(graph, "types");
    expect(layout.nodes.every(isTypeNode)).toBe(true);
    expect(layout.nodes.length).toBeGreaterThan(3);
    expect(layout.hierarchy.every((e) => e.kind === "extends" || e.kind === "implements")).toBe(true);
    expect(layout.links).toEqual([]);
  });

  it("«Методы»: члены лежат внутри своих типов, вызовы вынесены в links", () => {
    const layout = buildStructureLayout(graph, "methods");
    const main = layout.elk.children!.find((c) => c.id.endsWith("#Main"))!;
    expect(main.children!.length).toBeGreaterThan(10);
    expect(layout.links.some((e) => e.kind === "calls")).toBe(true);
    const ids = new Set(layout.nodes.map((n) => n.id));
    expect(ids.size).toBe(layout.nodes.length);
  });

  it("считает члены каждого типа", () => {
    const counts = memberCounts(graph);
    const main = graph.nodes.find((n) => n.id.endsWith("#Main") && isTypeNode(n))!;
    expect(counts.get(main.id)).toBeGreaterThan(50);
  });

  it("раскладывается ELK: у каждого видимого узла есть размеры", async () => {
    for (const view of ["types", "methods"] as const) {
      const layout = buildStructureLayout(graph, view);
      const placed = collectPlacements(await elk.layout(layout.elk));
      for (const node of layout.nodes) {
        const box = placed.get(node.id);
        expect(box, `${view}: ${node.id}`).toBeDefined();
        expect(box!.width).toBeGreaterThan(0);
      }
    }
  });
});
