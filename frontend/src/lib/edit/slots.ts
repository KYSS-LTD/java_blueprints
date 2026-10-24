import type { Block, BlockKind, FlowModel } from "@/lib/graph/flow-model";

/**
 * Место вставки оператора: аргументы `insert_node` и `move_node` (docs/08 § 5).
 * `slot` — `body`, `then`, `else`, `catch:<n>` или `finally`; `index` — позиция среди операторов слота
 * в тексте до правки. Для ветки `switch` родитель — сам блок `case`, слот `body`.
 */
export type InsertPoint = { parentId: string; slot: string; index: number };

/**
 * Слот определяется по структурному id блока (docs/04 § 5.3), а не по `parent_id`: ветки `if` в схеме
 * не контейнеры, и операторы `then`/`else` числятся за ближайшим циклом, `try` или `catch`.
 * Примеры id относительно метода: `foreach[0]/if[0]/else/op[1]`, `try[0]/catch[0]/assign[0]`.
 */
type Slot = {
  /** `parentId` операции: метод, цикл, `if`, `try`. */
  parentId: string;
  slot: string;
  /** Общее начало id операторов слота. */
  prefix: string;
};

/** После этих операторов код недостижим, поэтому вставка «в конец» идёт перед ними. */
const TERMINATORS: ReadonlySet<BlockKind> = new Set(["return", "throw", "break", "continue", "yield"]);

/** Узлы, которые не являются операторами тела: вставлять рядом с ними «в строку» нельзя. */
const NOT_STATEMENTS: ReadonlySet<BlockKind> = new Set(["method", "external", "catch", "finally", "case"]);

/** Контейнеры, в конец тела которых попадает блок, брошенный на них. */
const CONTAINERS: ReadonlySet<BlockKind> = new Set([
  "loop_for",
  "loop_foreach",
  "loop_while",
  "try",
  "catch",
  "finally",
  "case",
]);

const LOOP_SEGMENT = /^(for|foreach|while)\[\d+\]$/;
const CATCH_SEGMENT = /^catch\[(\d+)\]$/;
const CASE_SEGMENT = /^case\[\d+\]$/;

function isStatement(block: Block): boolean {
  return !NOT_STATEMENTS.has(block.kind);
}

/** Сегменты id блока после id метода. */
function segmentsOf(model: FlowModel, id: string): string[] | null {
  if (!id.startsWith(`${model.methodId}/`)) return null;
  return id.slice(model.methodId.length + 1).split("/");
}

function joinPath(model: FlowModel, segments: string[], count: number): string {
  return [model.methodId, ...segments.slice(0, count)].join("/");
}

/** Слот, в котором лежит оператор; `null` — слот не поддерживается (`switch`, лямбды, метки). */
function slotOfStatement(model: FlowModel, block: Block): Slot | null {
  if (!isStatement(block)) return null;
  const segments = segmentsOf(model, block.id);
  if (!segments) return null;
  if (segments.length === 1) return { parentId: model.methodId, slot: "body", prefix: `${model.methodId}/` };

  const previous = segments[segments.length - 2];
  const owner = joinPath(model, segments, segments.length - 2); // путь до секции
  const section = joinPath(model, segments, segments.length - 1); // путь вместе с секцией
  if (previous === "then" || previous === "else") return { parentId: owner, slot: previous, prefix: `${section}/` };
  if (previous === "body" || previous === "finally") return { parentId: owner, slot: previous, prefix: `${section}/` };
  const clause = CATCH_SEGMENT.exec(previous);
  if (clause) return { parentId: owner, slot: `catch:${clause[1]}`, prefix: `${section}/` };
  if (LOOP_SEGMENT.test(previous) || CASE_SEGMENT.test(previous)) {
    return { parentId: section, slot: "body", prefix: `${section}/` };
  }
  return null;
}

/** Слот контейнера, куда попадает блок, брошенный на него. */
function slotOfContainer(model: FlowModel, container: Block): Slot | null {
  const segments = segmentsOf(model, container.id);
  if (!segments) return null;
  switch (container.kind) {
    case "loop_for":
    case "loop_foreach":
    case "loop_while":
      return { parentId: container.id, slot: "body", prefix: `${container.id}/` };
    case "try":
      return { parentId: container.id, slot: "body", prefix: `${container.id}/body/` };
    case "catch": {
      const clause = CATCH_SEGMENT.exec(segments[segments.length - 1]);
      return clause
        ? { parentId: joinPath(model, segments, segments.length - 1), slot: `catch:${clause[1]}`, prefix: `${container.id}/` }
        : null;
    }
    case "finally":
      return { parentId: joinPath(model, segments, segments.length - 1), slot: "finally", prefix: `${container.id}/` };
    case "case":
      return { parentId: container.id, slot: "body", prefix: `${container.id}/` };
    default:
      return null;
  }
}

function position(block: Block): [number, number] {
  return block.range ? [block.range.start_line, block.range.start_col] : [Number.MAX_SAFE_INTEGER, 0];
}

/** Операторы слота в порядке следования кода. */
function siblingsOf(model: FlowModel, slot: Slot): string[] {
  const found: Block[] = [];
  for (const id of model.order) {
    if (!id.startsWith(slot.prefix) || id.slice(slot.prefix.length).includes("/")) continue;
    const block = model.blocks.get(id);
    if (block && isStatement(block)) found.push(block);
  }
  found.sort((a, b) => {
    const [la, ca] = position(a);
    const [lb, cb] = position(b);
    return la - lb || ca - cb;
  });
  return found.map((block) => block.id);
}

/** Позиция вставки «в конец» слота: перед завершающим `return`/`throw`/`break`/`continue`. */
function endIndex(model: FlowModel, siblings: string[]): number {
  const last = siblings.length > 0 ? model.blocks.get(siblings[siblings.length - 1]) : undefined;
  return last && TERMINATORS.has(last.kind) ? siblings.length - 1 : siblings.length;
}

/** Куда вставить блок по умолчанию (клик в палитре без выбранного блока): в конец тела метода. */
export function methodBodyEnd(model: FlowModel): InsertPoint {
  const slot: Slot = { parentId: model.methodId, slot: "body", prefix: `${model.methodId}/` };
  return { parentId: slot.parentId, slot: slot.slot, index: endIndex(model, siblingsOf(model, slot)) };
}

/** Вставка перед оператором. */
export function insertBefore(model: FlowModel, blockId: string): InsertPoint | null {
  const block = model.blocks.get(blockId);
  const slot = block ? slotOfStatement(model, block) : null;
  if (!slot) return null;
  return { parentId: slot.parentId, slot: slot.slot, index: siblingsOf(model, slot).indexOf(blockId) };
}

/** Вставка после оператора; после `return` и подобных — перед ним. */
export function insertAfter(model: FlowModel, blockId: string): InsertPoint | null {
  const block = model.blocks.get(blockId);
  const slot = block ? slotOfStatement(model, block) : null;
  if (!block || !slot) return null;
  const at = siblingsOf(model, slot).indexOf(blockId);
  return { parentId: slot.parentId, slot: slot.slot, index: TERMINATORS.has(block.kind) ? at : at + 1 };
}

/** Вставка в конец тела контейнера (цикл, `try`, `catch`, `finally`). */
export function appendInto(model: FlowModel, containerId: string): InsertPoint | null {
  const container = model.blocks.get(containerId);
  if (!container || !CONTAINERS.has(container.kind)) return null;
  const slot = slotOfContainer(model, container);
  if (!slot) return null;
  return { parentId: slot.parentId, slot: slot.slot, index: endIndex(model, siblingsOf(model, slot)) };
}

/**
 * Куда можно положить блок, брошенный на `targetId` (`half` — верхняя или нижняя половина блока):
 * на контейнер — в конец его тела, на обычный блок — перед ним (верх) или после него (низ).
 */
export function dropPoint(model: FlowModel, targetId: string, half: "top" | "bottom"): InsertPoint | null {
  const target = model.blocks.get(targetId);
  if (!target) return null;
  if (target.kind === "method") return methodBodyEnd(model);
  if (CONTAINERS.has(target.kind)) return appendInto(model, targetId);
  return half === "top" ? insertBefore(model, targetId) : insertAfter(model, targetId);
}

/**
 * Куда вставляется блок по клику в палитре: после выбранного оператора, а если выбран пустой контейнер —
 * внутрь него; без выбора — в конец тела метода.
 */
export function clickPoint(model: FlowModel, selectedId: string | null): InsertPoint {
  const selected = selectedId ? model.blocks.get(selectedId) : undefined;
  if (selected) {
    if (CONTAINERS.has(selected.kind) && (selected.children.length === 0 || selected.kind === "case")) {
      const inside = appendInto(model, selected.id);
      if (inside) return inside;
    }
    const after = insertAfter(model, selected.id);
    if (after) return after;
  }
  return methodBodyEnd(model);
}

/**
 * Подряд идущие операторы одного слота от `fromId` до `toId` включительно (в порядке кода);
 * `null`, если блоки лежат в разных слотах или один из них не оператор тела.
 */
export function siblingRange(model: FlowModel, fromId: string, toId: string): string[] | null {
  const from = model.blocks.get(fromId);
  const to = model.blocks.get(toId);
  const first = from ? slotOfStatement(model, from) : null;
  const second = to ? slotOfStatement(model, to) : null;
  if (!first || !second || first.prefix !== second.prefix) return null;
  const siblings = siblingsOf(model, first);
  const a = siblings.indexOf(fromId);
  const b = siblings.indexOf(toId);
  if (a === -1 || b === -1) return null;
  return siblings.slice(Math.min(a, b), Math.max(a, b) + 1);
}

/**
 * Выделение рамкой: из блоков внутри рамки берутся операторы того же слота, что и первый по порядку кода,
 * и выделяется подряд идущий отрезок от первого до последнего из них. Пусто, если в рамке нет операторов тела.
 */
export function rubberBandSelection(model: FlowModel, ids: readonly string[]): string[] {
  const statements = ids
    .map((id) => model.blocks.get(id))
    .filter((block): block is Block => block !== undefined && slotOfStatement(model, block) !== null)
    .sort((a, b) => {
      const [la, ca] = position(a);
      const [lb, cb] = position(b);
      return la - lb || ca - cb;
    });
  const first = statements[0];
  if (!first) return [];
  let last = first.id;
  for (const block of statements) {
    if (siblingRange(model, first.id, block.id) !== null) last = block.id;
  }
  return siblingRange(model, first.id, last) ?? [first.id];
}

/** Оператор можно переместить, дублировать и удалить как строку тела. */
export function isMovable(model: FlowModel, blockId: string): boolean {
  const block = model.blocks.get(blockId);
  return block !== undefined && slotOfStatement(model, block) !== null;
}

/** `id` — сам `ancestorId` или лежит внутри него (id устроены как пути). */
export function isInside(_model: FlowModel, id: string, ancestorId: string): boolean {
  return id === ancestorId || id.startsWith(`${ancestorId}/`);
}

/** Перемещение в то же место: backend отклонит такую операцию, UI её даже не отправляет. */
export function isSamePlace(model: FlowModel, blockId: string, point: InsertPoint): boolean {
  const block = model.blocks.get(blockId);
  const slot = block ? slotOfStatement(model, block) : null;
  if (!slot || slot.parentId !== point.parentId || slot.slot !== point.slot) return false;
  const at = siblingsOf(model, slot).indexOf(blockId);
  return point.index === at || point.index === at + 1;
}
