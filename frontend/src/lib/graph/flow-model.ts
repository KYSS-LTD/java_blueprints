import type { components } from "@/lib/api/schema";

type Schemas = components["schemas"];

export type FlowGraph = Schemas["FlowGraph"];
export type FlowEdgeKind = Schemas["FlowEdge"]["kind"];
export type SourceRange = Schemas["SourceRange"];
export type BlockBadge = Schemas["Badge"];

/** Вид узла в контракте API: то, что можно вставить, изменить и удалить. */
export type NodeKind = Schemas["FlowNode"]["kind"];

/** Вид блока: виды узлов контракта плюс синтетический «внешний вызов» (его строит frontend). */
export type BlockKind = NodeKind | "external";

export type Block = {
  id: string;
  kind: BlockKind;
  /** Заголовок блока на русском («Условие», «Цикл (for each)»…), приходит с backend. */
  title: string;
  code: string;
  parentId: string | null;
  /** Прямые потомки в порядке следования кода; у контейнера (цикл, try, switch…) список непуст. */
  children: string[];
  depth: number;
  range: SourceRange | null;
  badges: BlockBadge[];
  complexity: string | null;
  errors: string[];
  data: Record<string, unknown>;
  /** Только для внешнего вызова: id вызываемого метода. */
  target?: string;
  /** Только для catch: тип исключения из ребра `exception`. */
  exception?: string;
};

export type BlockEdge = {
  id: string;
  source: string;
  target: string;
  kind: FlowEdgeKind;
  label: string | null;
};

export type FlowModel = {
  methodId: string;
  signature: string;
  file: string;
  versionId: number;
  blocks: Map<string, Block>;
  /** Обход в глубину: предок всегда раньше потомков (порядок, который требует React Flow). */
  order: string[];
  /** Рёбра переходов; связи «контейнер → его содержимое» убраны: их показывает вложенность. */
  edges: BlockEdge[];
};

export const EXTERNAL_PREFIX = "ext:";

/** Рёбра, которые возвращают управление к началу цикла: в раскладке не участвуют. */
export function isLoopEdge(kind: FlowEdgeKind): boolean {
  return kind === "back" || kind === "continue";
}

export function isContainer(block: Block): boolean {
  return block.children.length > 0;
}

/** Читаемое имя метода из id члена: `Main.java#Order.getTotal()` → `Order.getTotal()`. */
export function methodDisplayName(methodId: string): string {
  return methodId.split("#")[1] ?? methodId;
}

/** Все предки блока от ближайшего к корню. */
export function ancestorsOf(model: Pick<FlowModel, "blocks">, id: string): string[] {
  const result: string[] = [];
  let parent = model.blocks.get(id)?.parentId ?? null;
  while (parent !== null) {
    result.push(parent);
    parent = model.blocks.get(parent)?.parentId ?? null;
  }
  return result;
}

export function isDescendant(model: Pick<FlowModel, "blocks">, id: string, ancestor: string): boolean {
  return ancestorsOf(model, id).includes(ancestor);
}

/**
 * Модель блок-схемы для отображения: дерево блоков (по `parent_id`), рёбра переходов и
 * синтетические блоки «внешний вызов» для рёбер `call` к методам вне этой схемы.
 */
export function buildFlowModel(flow: FlowGraph): FlowModel {
  const blocks = new Map<string, Block>();
  for (const node of flow.nodes) {
    blocks.set(node.id, {
      id: node.id,
      kind: node.kind,
      title: node.label,
      code: node.code,
      parentId: node.parent_id,
      children: [],
      depth: 0,
      range: node.range,
      badges: node.badges,
      complexity: node.complexity,
      errors: node.errors,
      data: node.data,
    });
  }
  // Родитель, которого нет в схеме, считаем корнем: схема остаётся отображаемой.
  for (const block of blocks.values()) {
    if (block.parentId !== null && !blocks.has(block.parentId)) block.parentId = null;
  }

  const edges: BlockEdge[] = [];
  const seenCalls = new Set<string>();
  const external: Block[] = [];
  for (const edge of flow.edges) {
    if (!blocks.has(edge.source)) continue;
    if (edge.kind === "call" && !blocks.has(edge.target)) {
      const key = `${edge.source}>${edge.target}`;
      if (seenCalls.has(key)) continue;
      seenCalls.add(key);
      const id = `${EXTERNAL_PREFIX}${key}`;
      external.push({
        id,
        kind: "external",
        title: "",
        code: methodDisplayName(edge.target),
        parentId: blocks.get(edge.source)?.parentId ?? null,
        children: [],
        depth: 0,
        range: null,
        badges: [],
        complexity: null,
        errors: [],
        data: {},
        target: edge.target,
      });
      edges.push({ id: edge.id, source: edge.source, target: id, kind: "call", label: null });
      continue;
    }
    if (!blocks.has(edge.target)) continue;
    edges.push({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      kind: edge.kind,
      label: edge.label,
    });
  }
  for (const block of external) blocks.set(block.id, block);

  // Потомки в порядке следования кода: сначала по позиции, внешние вызовы — сразу после своего источника.
  const roots: string[] = [];
  for (const block of blocks.values()) {
    if (block.parentId === null) roots.push(block.id);
    else blocks.get(block.parentId)?.children.push(block.id);
  }
  const position = (id: string): [number, number] => {
    const range = blocks.get(id)?.range;
    return range ? [range.start_line, range.start_col] : [Number.MAX_SAFE_INTEGER, 0];
  };
  const byPosition = (a: string, b: string) => {
    const [la, ca] = position(a);
    const [lb, cb] = position(b);
    return la - lb || ca - cb;
  };
  // Внешние вызовы не имеют позиции, поэтому они остаются в конце: их место определяют рёбра.
  roots.sort(byPosition);
  for (const block of blocks.values()) block.children.sort(byPosition);

  const order: string[] = [];
  const visit = (id: string, depth: number) => {
    const block = blocks.get(id);
    if (!block) return;
    block.depth = depth;
    order.push(id);
    for (const child of block.children) visit(child, depth + 1);
  };
  for (const id of roots) visit(id, 0);

  // Связи «предок → потомок» (body, case, exception) читаются по вложенности, ребро не нужно.
  const model: FlowModel = {
    methodId: flow.method_id,
    signature: flow.signature,
    file: flow.file,
    versionId: flow.version_id,
    blocks,
    order,
    edges: [],
  };
  for (const edge of edges) {
    if (isDescendant(model, edge.target, edge.source)) {
      const target = blocks.get(edge.target);
      if (edge.kind === "exception" && target && edge.label) target.exception = edge.label;
      continue;
    }
    model.edges.push(edge);
  }
  return model;
}

/** Узлы автосвёртки: контейнеры глубже `maxDepth` уровня в больших схемах (docs/09 § 8). */
export function defaultCollapsed(model: FlowModel, maxDepth = 3, threshold = 150): Set<string> {
  const collapsed = new Set<string>();
  if (model.blocks.size <= threshold) return collapsed;
  for (const block of model.blocks.values()) {
    if (isContainer(block) && containerLevel(model, block.id) >= maxDepth) collapsed.add(block.id);
  }
  return collapsed;
}

/** Номер уровня контейнера: сколько контейнеров-предков у блока. */
function containerLevel(model: FlowModel, id: string): number {
  return ancestorsOf(model, id).filter((a) => {
    const block = model.blocks.get(a);
    return block ? isContainer(block) : false;
  }).length;
}
