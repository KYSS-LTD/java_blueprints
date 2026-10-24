import { describe, expect, it } from "vitest";

import { parseArgs } from "./run-dialog";

describe("parseArgs", () => {
  it("разделяет по пробелам и учитывает кавычки", () => {
    expect(parseArgs("")).toEqual([]);
    expect(parseArgs("  a   b ")).toEqual(["a", "b"]);
    expect(parseArgs('--name "Иван Иванов" \'x y\' z')).toEqual(["--name", "Иван Иванов", "x y", "z"]);
  });
});
