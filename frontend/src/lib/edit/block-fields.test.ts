import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";
import { buildFlowModel } from "@/lib/graph/flow-model";
import flowsJson from "@/mocks/fixtures/flows.json";
import paletteJson from "@/mocks/fixtures/palette.json";

import {
  BRANCH_FIELDS,
  cleanFields,
  defaultValues,
  fieldsForKind,
  fragmentKind,
  needsForm,
  type PaletteBlock,
  valuesOfBlock,
  WRAP_FIELDS,
} from "./block-fields";

type FlowGraph = components["schemas"]["FlowGraph"];

const palette = (paletteJson as { blocks: PaletteBlock[] }).blocks;
const flows = flowsJson as unknown as Record<string, FlowGraph>;
const orders = buildFlowModel(flows["Main.java#Main.processOrders(List<Order>)"]);
const blockOfKind = (kind: string) => [...orders.blocks.values()].find((b) => b.kind === kind)!;

describe("поля форм", () => {
  it("при вставке поля берутся из палитры backend", () => {
    expect(fieldsForKind("operation", palette, "insert")?.map((f) => f.name)).toEqual(["expr"]);
    expect(fieldsForKind("condition", palette, "insert")?.map((f) => f.name)).toEqual(["expr", "add_else"]);
  });

  it("при правке флаги вставки (else у условия) не предлагаются", () => {
    expect(fieldsForKind("condition", palette, "update")?.map((f) => f.name)).toEqual(["expr"]);
  });

  it("switch и catch правятся по полям, хотя их нет в палитре; try, метод и лямбда — только текстом", () => {
    expect(fieldsForKind("switch", palette, "update")?.map((f) => f.name)).toEqual(["expr"]);
    expect(fieldsForKind("catch", palette, "update")?.map((f) => f.name)).toEqual(["exception_type", "name"]);
    expect(fieldsForKind("try", palette, "update")).toBeNull();
    expect(fieldsForKind("method", palette, "update")).toBeNull();
    expect(fieldsForKind("lambda", palette, "update")).toBeNull();
  });

  it("форма нужна всем вставляемым блокам, кроме break и continue", () => {
    const fields = (kind: Parameters<typeof fieldsForKind>[0]) => fieldsForKind(kind, palette, "insert");
    expect(needsForm("operation", fields("operation"))).toBe(true);
    expect(needsForm("return", fields("return"))).toBe(true);
    expect(needsForm("break", [{ name: "label", label: "Метка", type: "identifier", required: false, default: null, options: [] }])).toBe(false);
    expect(needsForm("continue", null)).toBe(false);
    expect(needsForm("operation", null)).toBe(false);
  });

  it("значения формы по данным существующих блоков", () => {
    const variable = blockOfKind("var_decl");
    expect(valuesOfBlock(variable)).toEqual({ type: "int", name: "accepted", value: "0" });
    expect(valuesOfBlock(blockOfKind("loop_foreach"))).toEqual({
      type: "Order",
      name: "order",
      iterable: "batch",
    });
    expect(valuesOfBlock(blockOfKind("condition"))).toEqual({ expr: "order.getQuantity() > 0" });
    const assign = [...orders.blocks.values()].find((b) => b.kind === "assign" && b.data.op === "++")!;
    expect(valuesOfBlock(assign)).toMatchObject({ op: "++", value: "" });
    expect(valuesOfBlock(blockOfKind("catch"))).toMatchObject({ exception_type: "OrderValidationException" });
  });

  it("значения по умолчанию: строки из палитры и выключенные флаги", () => {
    const fields = fieldsForKind("condition", palette, "insert")!;
    expect(defaultValues(fields)).toEqual({ expr: "", add_else: false });
  });

  it("пустые необязательные поля не уходят на backend, строки обрезаются", () => {
    const fields = fieldsForKind("var_decl", palette, "insert")!;
    expect(cleanFields(fields, { type: " int ", name: "n", value: "  " })).toEqual({ type: "int", name: "n" });
    const condition = fieldsForKind("condition", palette, "insert")!;
    expect(cleanFields(condition, { expr: "a > b", add_else: true })).toEqual({ expr: "a > b", add_else: true });
  });

  it("живая проверка только у выражений, типов и имён", () => {
    expect(fragmentKind("expression")).toBe("expression");
    expect(fragmentKind("type")).toBe("type");
    expect(fragmentKind("identifier")).toBe("identifier");
    expect(fragmentKind("text")).toBeNull();
    expect(fragmentKind("boolean")).toBeNull();
  });

  it("поля действий «обернуть» и «ветка»", () => {
    expect(WRAP_FIELDS.if.map((f) => f.name)).toEqual(["expr"]);
    expect(WRAP_FIELDS.while.map((f) => f.name)).toEqual(["cond"]);
    expect(WRAP_FIELDS.for.map((f) => f.name)).toEqual(["init", "cond", "update"]);
    expect(BRANCH_FIELDS.catch.map((f) => f.name)).toEqual(["exception_type", "name"]);
    expect(BRANCH_FIELDS.else_if.map((f) => f.name)).toEqual(["expr"]);
  });
});
