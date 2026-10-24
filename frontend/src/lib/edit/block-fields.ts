import type { components } from "@/lib/api/schema";
import type { Block, BlockKind } from "@/lib/graph/flow-model";

export type PaletteBlock = components["schemas"]["PaletteBlock"];
export type PaletteField = components["schemas"]["PaletteField"];

export type FormValues = Record<string, string | boolean>;

export function makeField(
  name: string,
  label: string,
  type: PaletteField["type"],
  options: { required?: boolean; default?: string | boolean | null } = {},
): PaletteField {
  return { name, label, type, required: options.required ?? true, default: options.default ?? null, options: [] };
}

/** Блоки, у которых есть поля правки, но которых нет в палитре (их нельзя вставить, но можно править). */
const EXTRA_FIELDS: Partial<Record<BlockKind, PaletteField[]>> = {
  switch: [makeField("expr", "Выражение", "expression")],
  catch: [
    makeField("exception_type", "Тип исключения", "type", { default: "Exception" }),
    makeField("name", "Имя переменной", "identifier", { default: "e" }),
  ],
};

/** Поля формы, которые можно менять у уже вставленного блока (остальные работают только при вставке). */
const INSERT_ONLY: Partial<Record<BlockKind, string[]>> = {
  condition: ["add_else"],
  loop_while: ["do_while"],
};

/** Виды, которые `update_node` не правит по полям: их меняют текстом (`replace_code`). */
const TEXT_ONLY: ReadonlySet<BlockKind> = new Set(["try", "method", "lambda", "local_type", "labeled", "assert", "sync", "case", "finally", "yield"]);

/** Описание формы блока: заголовок и поля; `null` — для блока нет формы, остаётся правка текстом. */
export function fieldsForKind(kind: BlockKind, palette: PaletteBlock[] | undefined, mode: "insert" | "update"): PaletteField[] | null {
  if (mode === "update" && TEXT_ONLY.has(kind)) return null;
  const fromPalette = palette?.find((block) => block.kind === kind)?.fields;
  const fields = fromPalette ?? EXTRA_FIELDS[kind];
  if (!fields) return null;
  const skipped = mode === "update" ? (INSERT_ONLY[kind] ?? []) : [];
  const result = fields.filter((f) => !skipped.includes(f.name));
  return result.length > 0 ? result : null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function list(value: unknown): string {
  return Array.isArray(value) ? value.map(String).join(", ") : "";
}

/** Значения формы по данным уже существующего блока (`FlowNode.data`). */
export function valuesOfBlock(block: Block): FormValues {
  const data = block.data;
  switch (block.kind) {
    case "var_decl": {
      const names = Array.isArray(data.names) ? data.names : [];
      const declarators = Array.isArray(data.declarators) ? (data.declarators as Array<{ init?: string | null }>) : [];
      return { type: text(data.type), name: text(names[0]), value: text(declarators[0]?.init) };
    }
    case "assign":
      return { target: text(data.target), op: text(data.op) || "=", value: text(data.value) };
    case "operation":
    case "return":
    case "throw":
    case "condition":
    case "switch":
      return { expr: text(data.expr) };
    case "break":
    case "continue":
      return { label: text(data.label) };
    case "loop_for":
      return { init: list(data.init), cond: text(data.cond), update: list(data.update) };
    case "loop_foreach":
      return { type: text(data.type), name: text(data.name), iterable: text(data.iterable) };
    case "loop_while":
      return { cond: text(data.cond) };
    case "catch": {
      const types = Array.isArray(data.types) ? data.types.map(String) : [];
      return { exception_type: types.length === 1 ? types[0] : "", name: text(data.name) };
    }
    case "code":
      return { text: text(data.text) || block.code };
    default:
      return {};
  }
}

/** Начальные значения формы вставки: значения по умолчанию из палитры. */
export function defaultValues(fields: PaletteField[]): FormValues {
  const values: FormValues = {};
  for (const f of fields) {
    values[f.name] = f.type === "boolean" ? f.default === true : typeof f.default === "string" ? f.default : "";
  }
  return values;
}

/** Поля для операции: пустые необязательные строки не передаются, чтобы не проверялись как фрагмент. */
export function cleanFields(fields: PaletteField[], values: FormValues): Record<string, string | boolean> {
  const result: Record<string, string | boolean> = {};
  for (const f of fields) {
    const value = values[f.name];
    if (value === undefined) continue;
    if (typeof value === "string") {
      if (value.trim() === "" && !f.required) continue;
      result[f.name] = value.trim();
    } else {
      result[f.name] = value;
    }
  }
  return result;
}

/** Вид фрагмента для живой проверки поля формы; `null` — поле проверять нечем. */
export function fragmentKind(type: PaletteField["type"]): "expression" | "type" | "identifier" | null {
  return type === "expression" || type === "type" || type === "identifier" ? type : null;
}

/** `break` и `continue` вставляются сразу, метка (необязательная) правится потом в инспекторе. */
const IMMEDIATE: ReadonlySet<BlockKind> = new Set(["break", "continue"]);

/** Блок вставляется через форму инспектора, а не сразу по клику. */
export function needsForm(kind: BlockKind, fields: PaletteField[] | null): boolean {
  return fields !== null && fields.length > 0 && !IMMEDIATE.has(kind);
}

/** Поля действий «обернуть в …» (`wrap_node`). */
export const WRAP_FIELDS = {
  if: [makeField("expr", "Условие", "expression")],
  while: [makeField("cond", "Условие", "expression")],
  for: [
    makeField("init", "Инициализация", "text", { default: "int i = 0" }),
    makeField("cond", "Условие", "expression", { default: "i < n" }),
    makeField("update", "Шаг", "text", { default: "i++" }),
  ],
} satisfies Record<string, PaletteField[]>;

/** Поля действий «добавить ветку» (`add_branch`), которым нужны данные. */
export const BRANCH_FIELDS = {
  else_if: [makeField("expr", "Условие", "expression")],
  catch: [
    makeField("exception_type", "Тип исключения", "type", { default: "Exception" }),
    makeField("name", "Имя переменной", "identifier", { default: "e" }),
  ],
  case: [makeField("expr", "Значение case", "expression")],
} satisfies Record<string, PaletteField[]>;
