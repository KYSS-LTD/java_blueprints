import { describe, expect, it } from "vitest";

import type { DecomposePlan } from "@/lib/api/actions";
import fixture from "@/mocks/fixtures/decompose-plan.json";

import {
  NEW_CLASS,
  canReceive,
  isMovable,
  isNameFree,
  isValidClassName,
  memberLabel,
  moveMember,
  ownerOf,
  renameClass,
  sameComposition,
  shrinkPercent,
  summarize,
  uniqueClassName,
} from "./plan-edit";

const plan = fixture as DecomposePlan;
const core = plan.classes.find((cls) => cls.role === "core")!;
const clusters = plan.classes.filter((cls) => cls.role !== "core" && cls.role !== "model");
const [first, second] = clusters;
const free = (id: string) => !(id in plan.pinned);
const movableOfFirst = first.members.find(free)!;

describe("перенос методов между классами плана", () => {
  it("метод переходит в другой класс, остальной состав не меняется", () => {
    const result = moveMember(plan, movableOfFirst, second.name)!;
    expect(result.removed).toEqual([]);
    const after = (name: string) => result.plan.classes.find((cls) => cls.name === name)!;
    expect(after(first.name).members).not.toContain(movableOfFirst);
    expect(after(second.name).members).toContain(movableOfFirst);
    expect(after(second.name).members).toHaveLength(second.members.length + 1);
    expect(ownerOf(result.plan, movableOfFirst)?.name).toBe(second.name);
    // исходный план не изменяется
    expect(ownerOf(plan, movableOfFirst)?.name).toBe(first.name);
  });

  it("опустевший класс убирается вместе со своими связями", () => {
    const small = clusters.reduce((best, cls) =>
      cls.members.length < best.members.length ? cls : best,
    );
    let current = plan;
    const removed: string[] = [];
    for (const id of small.members) {
      const result = moveMember(current, id, core.name)!;
      current = result.plan;
      removed.push(...result.removed);
    }
    expect(removed).toEqual([small.name]);
    expect(current.classes.some((cls) => cls.name === small.name)).toBe(false);
    expect(current.edges.some((edge) => edge.from === small.name || edge.to === small.name)).toBe(
      false,
    );
  });

  it("«новый класс» получает уникальное имя, файл и роль util", () => {
    const result = moveMember(plan, movableOfFirst, NEW_CLASS)!;
    expect(result.created).toBe("Helper");
    const created = result.plan.classes.find((cls) => cls.name === "Helper")!;
    expect(created).toMatchObject({ role: "util", file: "Helper.java", members: [movableOfFirst] });
    const again = moveMember(
      result.plan,
      first.members.find((id) => free(id) && id !== movableOfFirst)!,
      NEW_CLASS,
    )!;
    expect(again.created).toBe("Helper2");
  });

  it("закреплённые члены, вынесенные типы и тот же класс — отказ", () => {
    const pinnedId = Object.keys(plan.pinned)[0];
    expect(isMovable(plan, pinnedId)).toBe(false);
    expect(moveMember(plan, pinnedId, second.name)).toBeNull();

    const detached = plan.classes.find((cls) => cls.role === "model")!;
    expect(canReceive(detached)).toBe(false);
    expect(isMovable(plan, detached.members[0])).toBe(false);
    expect(moveMember(plan, movableOfFirst, detached.name)).toBeNull();

    expect(moveMember(plan, movableOfFirst, first.name)).toBeNull();
    expect(moveMember(plan, movableOfFirst, "Нет такого")).toBeNull();
  });

  it("единственный метод класса в «новый класс» не переносится: получилось бы переименование", () => {
    const single = {
      ...plan,
      classes: plan.classes.map((cls) =>
        cls.name === first.name ? { ...cls, members: [movableOfFirst] } : cls,
      ),
    };
    expect(moveMember(single, movableOfFirst, NEW_CLASS)).toBeNull();
  });
});

describe("имена классов", () => {
  it("переименование меняет класс, файл и концы связей", () => {
    const renamed = renameClass(plan, first.name, "OrderRules");
    const cls = renamed.classes.find((c) => c.name === "OrderRules")!;
    expect(cls.file).toBe("OrderRules.java");
    expect(renamed.classes.some((c) => c.name === first.name)).toBe(false);
    expect(renamed.edges.some((e) => e.from === first.name || e.to === first.name)).toBe(false);
    expect(renamed.edges.length).toBe(plan.edges.length);
  });

  it("проверка идентификатора и занятости", () => {
    expect(isValidClassName("OrderRules")).toBe(true);
    expect(isValidClassName("_x1")).toBe(true);
    for (const bad of ["", "1bad", "with space", "a-b", "Класс"])
      expect(isValidClassName(bad)).toBe(false);
    expect(isNameFree(plan, first.name)).toBe(false);
    expect(isNameFree(plan, first.name, first.name)).toBe(true);
    expect(isNameFree(plan, "Brand")).toBe(true);
    expect(uniqueClassName(plan, first.name)).toBe(`${first.name}2`);
  });
});

describe("вспомогательные расчёты", () => {
  it("подпись члена без файла и класса", () => {
    expect(memberLabel("Main.java#Main.parseInput(String)")).toBe("parseInput(String)");
    expect(memberLabel("Main.java#Main.lines")).toBe("lines");
    expect(memberLabel("Main.java#Main.Kind")).toBe("Kind");
    expect(memberLabel("Main.java#Main.put(Map<String, List<String>>)")).toBe(
      "put(Map<String, List<String>>)",
    );
  });

  it("изменение размера ядра в процентах", () => {
    expect(shrinkPercent(1000, 400)).toBe(-60);
    expect(shrinkPercent(0, 10)).toBe(0);
    expect(shrinkPercent(100, 100)).toBe(0);
  });

  it("сравнение состава и сводка", () => {
    expect(sameComposition(plan, structuredClone(plan))).toBe(true);
    expect(sameComposition(plan, moveMember(plan, movableOfFirst, second.name)!.plan)).toBe(false);
    expect(sameComposition(plan, renameClass(plan, first.name, "Other"))).toBe(false);
    const summary = summarize(plan);
    expect(summary.clusters).toBe(clusters.length);
    expect(summary.methods).toBe(clusters.reduce((n, cls) => n + cls.members.length, 0));
    expect(summary.detached).toBe(plan.classes.filter((c) => c.role === "model").length);
    expect(summary.pinned).toBeGreaterThan(0);
  });
});
