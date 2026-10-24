import { describe, expect, it } from "vitest";

import treeJson from "@/mocks/fixtures/tree.json";

import { defaultExpanded, findNode, pathTo, type TreeNode, visibleRows } from "./tree-model";

const roots = (treeJson as unknown as { roots: TreeNode[] }).roots;

describe("tree-model", () => {
  it("по умолчанию раскрывает файлы, но не типы", () => {
    const rows = visibleRows(roots, defaultExpanded(roots), "");
    expect(rows[0].node.kind).toBe("file");
    expect(rows.some((r) => r.node.kind === "type")).toBe(true);
    expect(rows.some((r) => r.node.kind === "member")).toBe(false);
  });

  it("поиск раскрывает путь к совпадению и помечает совпавшую строку", () => {
    const rows = visibleRows(roots, new Set(), "mergeDuplicates");
    const matched = rows.filter((r) => r.matched);
    expect(matched.map((r) => r.node.name)).toEqual([expect.stringContaining("mergeDuplicates")]);
    expect(rows.map((r) => r.node.kind)).toEqual(["file", "type", "member"]);
    expect(rows.map((r) => r.depth)).toEqual([0, 1, 2]);
  });

  it("поиск без совпадений даёт пустой список", () => {
    expect(visibleRows(roots, new Set(), "нет-такого-метода")).toEqual([]);
  });

  it("находит путь до метода и сам узел", () => {
    const method = findNode(roots, (n) => n.method_id !== null && n.name.startsWith("mergeDuplicates"))!;
    const path = pathTo(roots, method.id);
    expect(path).toHaveLength(2);
    // порядок: от ближайшего предка к корню
    expect(findNode(roots, (n) => n.id === path[0])?.kind).toBe("type");
    expect(findNode(roots, (n) => n.id === path[1])?.kind).toBe("file");
  });
});
