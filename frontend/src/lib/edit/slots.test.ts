import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";
import { buildFlowModel, type FlowModel } from "@/lib/graph/flow-model";
import flowsJson from "@/mocks/fixtures/flows.json";

import {
  appendInto,
  clickPoint,
  dropPoint,
  insertAfter,
  insertBefore,
  isInside,
  isMovable,
  isSamePlace,
  methodBodyEnd,
} from "./slots";

type FlowGraph = components["schemas"]["FlowGraph"];

const flows = flowsJson as unknown as Record<string, FlowGraph>;
const ORDERS = "Main.java#Main.processOrders(List<Order>)";
const model: FlowModel = buildFlowModel(flows[ORDERS]);
const id = (path: string) => `${ORDERS}/${path}`;

// processOrders: var[0]; foreach[0] { if[0] { then: try[0] { body: op[0], assign[0]; catch[0]: assign[0], op[0] } else: assign[0], op[0] } }; return[0]
describe("слоты вставки по структурным id", () => {
  it("корень метода: слот body, return остаётся последним", () => {
    expect(methodBodyEnd(model)).toEqual({ parentId: ORDERS, slot: "body", index: 2 });
    expect(insertAfter(model, id("var[0]"))).toEqual({ parentId: ORDERS, slot: "body", index: 1 });
    expect(insertBefore(model, id("foreach[0]"))).toEqual({ parentId: ORDERS, slot: "body", index: 1 });
  });

  it("после return вставляется перед ним: код после return недостижим", () => {
    expect(insertAfter(model, id("return[0]"))).toEqual({ parentId: ORDERS, slot: "body", index: 2 });
  });

  it("тело цикла: слот body цикла; вложенное и ветки if не считаются его операторами", () => {
    expect(insertAfter(model, id("foreach[0]/if[0]"))).toEqual({
      parentId: id("foreach[0]"),
      slot: "body",
      index: 1,
    });
    expect(appendInto(model, id("foreach[0]"))).toEqual({ parentId: id("foreach[0]"), slot: "body", index: 1 });
  });

  it("ветка else принадлежит if, а не циклу, хотя в схеме её блоки лежат в цикле", () => {
    expect(insertAfter(model, id("foreach[0]/if[0]/else/assign[0]"))).toEqual({
      parentId: id("foreach[0]/if[0]"),
      slot: "else",
      index: 1,
    });
    expect(insertBefore(model, id("foreach[0]/if[0]/else/op[0]"))).toEqual({
      parentId: id("foreach[0]/if[0]"),
      slot: "else",
      index: 1,
    });
  });

  it("then: try — единственный оператор ветки", () => {
    expect(insertAfter(model, id("foreach[0]/if[0]/then/try[0]"))).toEqual({
      parentId: id("foreach[0]/if[0]"),
      slot: "then",
      index: 1,
    });
  });

  it("тело try и ветки catch", () => {
    expect(insertAfter(model, id("foreach[0]/if[0]/then/try[0]/body/op[0]"))).toEqual({
      parentId: id("foreach[0]/if[0]/then/try[0]"),
      slot: "body",
      index: 1,
    });
    expect(appendInto(model, id("foreach[0]/if[0]/then/try[0]"))).toEqual({
      parentId: id("foreach[0]/if[0]/then/try[0]"),
      slot: "body",
      index: 2,
    });
    expect(insertAfter(model, id("foreach[0]/if[0]/then/try[0]/catch[0]/assign[0]"))).toEqual({
      parentId: id("foreach[0]/if[0]/then/try[0]"),
      slot: "catch:0",
      index: 1,
    });
    expect(appendInto(model, id("foreach[0]/if[0]/then/try[0]/catch[0]"))).toEqual({
      parentId: id("foreach[0]/if[0]/then/try[0]"),
      slot: "catch:0",
      index: 2,
    });
  });

  it("клик в палитре: после выбранного блока, без выбора — в конец метода", () => {
    expect(clickPoint(model, id("var[0]"))).toEqual({ parentId: ORDERS, slot: "body", index: 1 });
    expect(clickPoint(model, null)).toEqual(methodBodyEnd(model));
    expect(clickPoint(model, "нет такого")).toEqual(methodBodyEnd(model));
  });

  it("бросок на блок: верх — перед, низ — после; на контейнер — в конец его тела", () => {
    const op = id("foreach[0]/if[0]/else/op[0]");
    expect(dropPoint(model, op, "top")).toEqual(insertBefore(model, op));
    expect(dropPoint(model, op, "bottom")).toEqual(insertAfter(model, op));
    expect(dropPoint(model, id("foreach[0]"), "top")).toEqual(appendInto(model, id("foreach[0]")));
    expect(dropPoint(model, ORDERS, "bottom")).toEqual(methodBodyEnd(model));
  });

  it("условие — обычный блок: бросок на него вставляет рядом, а не в ветку", () => {
    expect(dropPoint(model, id("foreach[0]/if[0]"), "bottom")).toEqual(insertAfter(model, id("foreach[0]/if[0]")));
    expect(appendInto(model, id("foreach[0]/if[0]"))).toBeNull();
  });

  it("catch и finally не операторы: рядом с ними не вставляют", () => {
    const clause = id("foreach[0]/if[0]/then/try[0]/catch[0]");
    expect(insertAfter(model, clause)).toBeNull();
    expect(isMovable(model, clause)).toBe(false);
    expect(isMovable(model, ORDERS)).toBe(false);
    expect(isMovable(model, id("var[0]"))).toBe(true);
  });
});

describe("перемещение", () => {
  it("внутрь самого себя нельзя; id устроены как пути", () => {
    expect(isInside(model, id("foreach[0]/if[0]"), id("foreach[0]"))).toBe(true);
    expect(isInside(model, id("foreach[0]"), id("foreach[0]"))).toBe(true);
    expect(isInside(model, id("foreach[1]"), id("foreach[0]"))).toBe(false);
    expect(isInside(model, id("var[0]"), id("foreach[0]"))).toBe(false);
  });

  it("в то же место — не перемещение", () => {
    const loop = id("foreach[0]");
    expect(isSamePlace(model, loop, { parentId: ORDERS, slot: "body", index: 1 })).toBe(true); // перед собой
    expect(isSamePlace(model, loop, { parentId: ORDERS, slot: "body", index: 2 })).toBe(true); // сразу после себя
    expect(isSamePlace(model, loop, { parentId: ORDERS, slot: "body", index: 0 })).toBe(false);
    expect(isSamePlace(model, loop, { parentId: id("foreach[0]/if[0]"), slot: "else", index: 0 })).toBe(false);
  });
});
