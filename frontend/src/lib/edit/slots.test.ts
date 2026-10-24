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
  rubberBandSelection,
  siblingRange,
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

// processCommand: switch[0] { case[1]: var[0..2], op[0], break[0]; case[19], case[20]: return[0] без операторов перед ним }
const COMMAND = "Main.java#Main.processCommand(String, Scanner)";
const commandModel: FlowModel = buildFlowModel(flows[COMMAND]);
const caseId = (n: number, tail = "") => `${COMMAND}/switch[0]/case[${n}]${tail}`;

describe("ветки switch", () => {
  it("операторы ветки лежат в слоте body самого case; break остаётся последним", () => {
    expect(insertBefore(commandModel, caseId(1, "/op[0]"))).toEqual({
      parentId: caseId(1),
      slot: "body",
      index: 3,
    });
    expect(insertAfter(commandModel, caseId(1, "/var[0]"))).toEqual({ parentId: caseId(1), slot: "body", index: 1 });
    expect(insertAfter(commandModel, caseId(1, "/break[0]"))).toEqual({ parentId: caseId(1), slot: "body", index: 4 });
  });

  it("бросок и клик на ветку кладут блок в конец её тела, перед завершающим break", () => {
    expect(dropPoint(commandModel, caseId(1), "bottom")).toEqual({ parentId: caseId(1), slot: "body", index: 4 });
    expect(appendInto(commandModel, caseId(1))).toEqual({ parentId: caseId(1), slot: "body", index: 4 });
    expect(clickPoint(commandModel, caseId(1))).toEqual({ parentId: caseId(1), slot: "body", index: 4 });
  });

  it("ветка без операторов принимает блок; сама ветка не оператор: рядом с ней не вставляют", () => {
    expect(appendInto(commandModel, caseId(19))).toEqual({ parentId: caseId(19), slot: "body", index: 0 });
    expect(insertBefore(commandModel, caseId(1))).toBeNull();
    expect(insertAfter(commandModel, caseId(1))).toBeNull();
    expect(isMovable(commandModel, caseId(1))).toBe(false);
  });

  it("операторы ветки перемещаются, а диапазон не выходит за ветку", () => {
    expect(isMovable(commandModel, caseId(1, "/op[0]"))).toBe(true);
    expect(siblingRange(commandModel, caseId(1, "/var[0]"), caseId(1, "/op[0]"))).toEqual([
      caseId(1, "/var[0]"),
      caseId(1, "/var[1]"),
      caseId(1, "/var[2]"),
      caseId(1, "/op[0]"),
    ]);
    expect(siblingRange(commandModel, caseId(1, "/op[0]"), caseId(2, "/op[0]"))).toBeNull();
    expect(isSamePlace(commandModel, caseId(1, "/op[0]"), { parentId: caseId(1), slot: "body", index: 3 })).toBe(true);
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

describe("подряд идущие операторы одного слота", () => {
  it("диапазон между двумя блоками тела метода — в порядке кода, в любую сторону", () => {
    expect(siblingRange(model, id("var[0]"), id("foreach[0]"))).toEqual([id("var[0]"), id("foreach[0]")]);
    expect(siblingRange(model, id("return[0]"), id("var[0]"))).toEqual([
      id("var[0]"),
      id("foreach[0]"),
      id("return[0]"),
    ]);
    expect(siblingRange(model, id("var[0]"), id("var[0]"))).toEqual([id("var[0]")]);
  });

  it("блоки из разных слотов (тело метода и тело цикла, then и else) диапазона не дают", () => {
    expect(siblingRange(model, id("var[0]"), id("foreach[0]/if[0]"))).toBeNull();
    expect(siblingRange(model, id("foreach[0]/if[0]/else/assign[0]"), id("foreach[0]/if[0]/else/op[0]"))).toEqual([
      id("foreach[0]/if[0]/else/assign[0]"),
      id("foreach[0]/if[0]/else/op[0]"),
    ]);
    expect(
      siblingRange(model, id("foreach[0]/if[0]/else/assign[0]"), id("foreach[0]/if[0]/then/try[0]")),
    ).toBeNull();
  });

  it("метод, ветки catch и неизвестные блоки не входят в диапазон", () => {
    expect(siblingRange(model, ORDERS, id("var[0]"))).toBeNull();
    expect(siblingRange(model, id("var[0]"), id("нет такого"))).toBeNull();
  });
});

describe("выделение рамкой", () => {
  it("из блоков в рамке берётся отрезок одного слота от первого оператора до последнего", () => {
    expect(rubberBandSelection(model, [id("var[0]"), id("foreach[0]"), id("return[0]")])).toEqual([
      id("var[0]"),
      id("foreach[0]"),
      id("return[0]"),
    ]);
    // порядок в рамке любой, блоки из чужих слотов (тело цикла) отбрасываются
    expect(rubberBandSelection(model, [id("foreach[0]/if[0]"), id("foreach[0]"), id("var[0]")])).toEqual([
      id("var[0]"),
      id("foreach[0]"),
    ]);
  });

  it("в рамке без операторов тела — пустое выделение", () => {
    expect(rubberBandSelection(model, [])).toEqual([]);
    expect(rubberBandSelection(model, [ORDERS, "нет-такого-блока"])).toEqual([]);
  });

  it("внутри ветки switch выделяются только её операторы", () => {
    expect(
      rubberBandSelection(commandModel, [caseId(1, "/var[1]"), caseId(1, "/op[0]"), caseId(2, "/op[0]")]),
    ).toEqual([caseId(1, "/var[1]"), caseId(1, "/var[2]"), caseId(1, "/op[0]")]);
  });
});
