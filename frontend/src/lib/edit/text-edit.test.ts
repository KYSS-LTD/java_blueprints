import { describe, expect, it } from "vitest";

import { minimalChange, type TextChange } from "./text-edit";

function apply(before: string, change: TextChange | null): string {
  return change ? before.slice(0, change.start) + change.text + before.slice(change.end) : before;
}

describe("minimalChange", () => {
  it("одинаковые тексты — правки нет", () => {
    expect(minimalChange("class A {}", "class A {}")).toBeNull();
  });

  it("вставка строки в середину меняет только её", () => {
    const before = "a();\nreturn x;\n";
    const after = "a();\nb();\nreturn x;\n";
    const change = minimalChange(before, after);
    expect(change).toEqual({ start: 5, end: 5, text: "b();\n" });
    expect(apply(before, change)).toBe(after);
  });

  it("удаление и замена", () => {
    expect(apply("one two three", minimalChange("one two three", "one three"))).toBe("one three");
    const change = minimalChange("int x = 1;", "int y = 1;");
    expect(change).toEqual({ start: 4, end: 5, text: "y" });
  });

  it("повторяющийся текст не уводит правку за границы", () => {
    const before = "aaa";
    const after = "aaaa";
    expect(apply(before, minimalChange(before, after))).toBe(after);
    expect(apply(after, minimalChange(after, before))).toBe(before);
  });

  it("не разрывает суррогатную пару эмодзи", () => {
    const before = "x = \"😀\";";
    const after = "x = \"😁\";";
    const change = minimalChange(before, after)!;
    expect(apply(before, change)).toBe(after);
    expect(change.text).toBe("😁");
    expect(before.slice(change.start, change.end)).toBe("😀");
  });

  it("кириллица и пустые тексты", () => {
    expect(apply("", minimalChange("", "Привет"))).toBe("Привет");
    expect(apply("Привет", minimalChange("Привет", ""))).toBe("");
    expect(apply("Привет, мир", minimalChange("Привет, мир", "Привет, Java"))).toBe("Привет, Java");
  });
});
