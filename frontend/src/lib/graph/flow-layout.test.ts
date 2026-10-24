// @vitest-environment node
import ELK from "elkjs/lib/elk.bundled.js";
import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";
import flowsJson from "@/mocks/fixtures/flows.json";

import {
  buildElkGraph,
  buildVisibleFlow,
  collectPlacements,
  type LayoutSettings,
  type Placement,
} from "./flow-layout";
import { buildFlowModel, isContainer } from "./flow-model";

type FlowGraph = components["schemas"]["FlowGraph"];

const flows = flowsJson as unknown as Record<string, FlowGraph>;
const processOrders = flows["Main.java#Main.processOrders(List<Order>)"];
const processCommand = flows["Main.java#Main.processCommand(String, Scanner)"];
const elk = new ELK();

const open: LayoutSettings = { collapsed: new Set(), showCalls: true };

async function layout(flow: FlowGraph, settings: LayoutSettings = open) {
  const vis = buildVisibleFlow(buildFlowModel(flow), settings);
  const result = await elk.layout(buildElkGraph(vis));
  return { vis, placements: collectPlacements(result) };
}

function overlap(a: Placement, b: Placement): boolean {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
}

describe("buildVisibleFlow", () => {
  const model = buildFlowModel(processOrders);
  const foreach = [...model.blocks.values()].find((b) => b.kind === "loop_foreach")!;

  it("свёрнутый контейнер скрывает содержимое и забирает его рёбра", () => {
    const vis = buildVisibleFlow(model, { collapsed: new Set([foreach.id]), showCalls: true });
    expect(vis.order).not.toContain(foreach.children[0]);
    expect(vis.representative.get(foreach.children[0])).toBe(foreach.id);
    for (const edge of vis.edges) {
      expect(vis.order).toContain(edge.source);
      expect(vis.order).toContain(edge.target);
      expect(edge.source).not.toBe(edge.target);
    }
  });

  it("без вызовов внешние блоки и их рёбра пропадают", () => {
    const vis = buildVisibleFlow(model, { collapsed: new Set(), showCalls: false });
    expect(vis.order.some((id) => id.startsWith("ext:"))).toBe(false);
    expect(vis.edges.some((e) => e.kind === "call")).toBe(false);
  });
});

describe("раскладка ELK", () => {
  it("размещает каждый видимый блок", async () => {
    const { vis, placements } = await layout(processOrders);
    for (const id of vis.order) expect(placements.has(id), id).toBe(true);
  });

  it("вложенные блоки лежат внутри контейнера и не пересекаются", async () => {
    const { vis, placements } = await layout(processOrders);
    const { blocks } = vis.model;
    for (const id of vis.order) {
      const block = blocks.get(id)!;
      const box = placements.get(id)!;
      if (block.parentId !== null) {
        const parent = placements.get(block.parentId)!;
        expect(box.x, id).toBeGreaterThanOrEqual(0);
        expect(box.y, id).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width, id).toBeLessThanOrEqual(parent.width + 0.5);
        expect(box.y + box.height, id).toBeLessThanOrEqual(parent.height + 0.5);
      }
      const siblings = vis.order.filter((o) => blocks.get(o)!.parentId === block.parentId && o !== id);
      for (const other of siblings) {
        expect(overlap(box, placements.get(other)!), `${id} × ${other}`).toBe(false);
      }
    }
  });

  it("ветка «Да» стоит левее ветки «Нет»", async () => {
    const { vis, placements } = await layout(processOrders);
    const yes = vis.edges.find((e) => e.kind === "true")!;
    const no = vis.edges.find((e) => e.kind === "false")!;
    expect(yes.source).toBe(no.source);
    // сравниваем в одной системе координат: оба блока лежат в одном контейнере
    expect(placements.get(yes.target)!.x).toBeLessThan(placements.get(no.target)!.x);
  });

  it("петли не вытягивают контейнер вверх: порядок идёт сверху вниз", async () => {
    const { vis, placements } = await layout(processOrders);
    const roots = vis.order.filter((id) => vis.model.blocks.get(id)!.parentId === null);
    const ys = roots
      .filter((id) => vis.model.blocks.get(id)!.kind !== "external")
      .map((id) => placements.get(id)!.y);
    expect(ys).toEqual([...ys].sort((a, b) => a - b));
  });

  it("раскладывает все блок-схемы демо-монолита", async () => {
    for (const [id, flow] of Object.entries(flows)) {
      const { vis, placements } = await layout(flow);
      expect(placements.size, id).toBe(vis.order.length);
      for (const placed of placements.values()) {
        expect(Number.isFinite(placed.x + placed.y + placed.width + placed.height), id).toBe(true);
      }
    }
  }, 60_000);

  it("switch с 22 ветками не вытягивается в одну линию", async () => {
    const { vis, placements } = await layout(processCommand);
    const sw = [...vis.model.blocks.values()].find((b) => b.kind === "switch")!;
    expect(isContainer(sw)).toBe(true);
    const box = placements.get(sw.id)!;
    expect(box.width / box.height).toBeGreaterThan(0.6);
    expect(box.width / box.height).toBeLessThan(4);
  });
});
