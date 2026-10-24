import type { ElkExtendedEdge, ElkNode } from "elkjs/lib/elk-api";

import {
  type Block,
  type BlockEdge,
  type FlowModel,
  isContainer,
  isLoopEdge,
} from "./flow-model";

export type LayoutSettings = {
  /** Свёрнутые контейнеры: их содержимое скрыто, рёбра переносятся на сам контейнер. */
  collapsed: ReadonlySet<string>;
  /** Показывать блоки «внешний вызов». */
  showCalls: boolean;
};

export type Size = { width: number; height: number };

export const BLOCK_SIZE: Size = { width: 248, height: 58 };
export const METHOD_SIZE: Size = { width: 300, height: 62 };
export const EXTERNAL_SIZE: Size = { width: 248, height: 46 };
/** Отступы контейнера: сверху место под заголовок. */
export const CONTAINER_PADDING = { top: 52, left: 20, bottom: 20, right: 20 };

export type VisibleFlow = {
  model: FlowModel;
  collapsed: ReadonlySet<string>;
  /** Видимые блоки: предок раньше потомков. */
  order: string[];
  /** Видимый представитель каждого блока модели (null — блок скрыт совсем). */
  representative: Map<string, string | null>;
  /** Рёбра между видимыми блоками без дублей. */
  edges: BlockEdge[];
};

export function sizeOf(block: Block): Size {
  if (block.kind === "method") return METHOD_SIZE;
  if (block.kind === "external") return EXTERNAL_SIZE;
  return BLOCK_SIZE;
}

/** Контейнер, который сейчас развёрнут и содержит видимые блоки. */
export function isExpanded(vis: VisibleFlow, id: string): boolean {
  const block = vis.model.blocks.get(id);
  if (!block || !isContainer(block) || vis.collapsed.has(id)) return false;
  return block.children.some((child) => vis.representative.get(child) === child);
}

export function buildVisibleFlow(model: FlowModel, settings: LayoutSettings): VisibleFlow {
  const { blocks } = model;
  const representative = new Map<string, string | null>();

  const repOf = (id: string): string | null => {
    const cached = representative.get(id);
    if (cached !== undefined) return cached;
    const block = blocks.get(id);
    let result: string | null = id;
    if (!block) result = null;
    else if (block.kind === "external" && !settings.showCalls) result = null;
    else if (block.parentId !== null) {
      const parentRep = repOf(block.parentId);
      if (parentRep === null) result = null;
      else if (parentRep !== block.parentId) result = parentRep;
      else if (settings.collapsed.has(block.parentId)) result = block.parentId;
    }
    representative.set(id, result);
    return result;
  };

  const order = model.order.filter((id) => repOf(id) === id);

  const seen = new Set<string>();
  const edges: BlockEdge[] = [];
  for (const edge of model.edges) {
    const source = repOf(edge.source);
    const target = repOf(edge.target);
    if (source === null || target === null || source === target) continue;
    const key = `${source}>${target}>${edge.kind}>${edge.label ?? ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    edges.push({ ...edge, source, target });
  }
  return { model, collapsed: settings.collapsed, order, representative, edges };
}

const LAYERED_BASE: Record<string, string> = {
  "elk.algorithm": "layered",
  "elk.direction": "DOWN",
  "elk.spacing.nodeNode": "36",
  "elk.layered.spacing.nodeNodeBetweenLayers": "44",
  "elk.layered.spacing.edgeNodeBetweenLayers": "24",
  "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
  "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
  "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
  "elk.edgeRouting": "POLYLINE",
};

function padding(p: { top: number; left: number; bottom: number; right: number }): string {
  return `[top=${p.top},left=${p.left},bottom=${p.bottom},right=${p.right}]`;
}

const ROOT_OPTIONS: Record<string, string> = {
  ...LAYERED_BASE,
  "elk.hierarchyHandling": "SEPARATE_CHILDREN",
  "elk.padding": padding({ top: 24, left: 24, bottom: 24, right: 24 }),
};

const CONTAINER_OPTIONS: Record<string, string> = {
  ...LAYERED_BASE,
  "elk.padding": padding(CONTAINER_PADDING),
};

// Ветки switch не связаны между собой рёбрами: в «layered» они выстроились бы в одну линию,
// поэтому их упаковываем сеткой с пропорциями, близкими к экрану.
const SWITCH_OPTIONS: Record<string, string> = {
  "elk.algorithm": "box",
  "elk.aspectRatio": "1.8",
  "elk.spacing.nodeNode": "28",
  "elk.padding": padding(CONTAINER_PADDING),
};

function optionsFor(block: Block): Record<string, string> {
  return block.kind === "switch" ? SWITCH_OPTIONS : CONTAINER_OPTIONS;
}

/**
 * Граф для ELK. Каждый развёрнутый контейнер раскладывается отдельно (SEPARATE_CHILDREN), а ребро
 * между блоками разных уровней поднимается до пары соседей под ближайшим общим предком.
 * Петли (`back`, `continue`) в раскладке не участвуют: они не меняют порядок блоков.
 */
export function buildElkGraph(vis: VisibleFlow): ElkNode {
  const { blocks } = vis.model;
  const visible = new Set(vis.order);

  // Цепочка видимых предков блока: [сам блок, родитель, …, null (корень)].
  const chainOf = (id: string): Array<string | null> => {
    const chain: Array<string | null> = [];
    let current: string | null = id;
    while (current !== null) {
      chain.push(current);
      current = blocks.get(current)?.parentId ?? null;
    }
    chain.push(null);
    return chain;
  };

  const edgesByParent = new Map<string | null, ElkExtendedEdge[]>();
  const seenLifted = new Set<string>();
  for (const edge of vis.edges) {
    if (isLoopEdge(edge.kind)) continue;
    const sourceChain = chainOf(edge.source);
    const targetChain = chainOf(edge.target);
    const common = sourceChain.find((id) => targetChain.includes(id));
    if (common === undefined) continue;
    const a = sourceChain[sourceChain.indexOf(common) - 1];
    const b = targetChain[targetChain.indexOf(common) - 1];
    // Один из блоков — предок другого: связь выражена вложенностью.
    if (a === undefined || b === undefined || a === null || b === null || a === b) continue;
    const key = `${common}|${a}>${b}`;
    if (seenLifted.has(key)) continue;
    seenLifted.add(key);
    const list = edgesByParent.get(common) ?? [];
    list.push({ id: `e:${key}`, sources: [a], targets: [b] });
    edgesByParent.set(common, list);
  }

  const toElk = (id: string): ElkNode => {
    const block = blocks.get(id)!;
    if (!isExpanded(vis, id)) return { id, ...sizeOf(block) };
    const children = block.children.filter((child) => visible.has(child)).map(toElk);
    return {
      id,
      layoutOptions: optionsFor(block),
      children,
      edges: edgesByParent.get(id) ?? [],
    };
  };

  const roots = vis.order.filter((id) => blocks.get(id)?.parentId == null).map(toElk);
  return {
    id: "root",
    layoutOptions: ROOT_OPTIONS,
    children: roots,
    edges: edgesByParent.get(null) ?? [],
  };
}

export type Placement = { x: number; y: number; width: number; height: number };

/** Координаты ELK в виде плоской карты; `x`, `y` у вложенных блоков — относительно контейнера. */
export function collectPlacements(root: ElkNode): Map<string, Placement> {
  const result = new Map<string, Placement>();
  const walk = (node: ElkNode) => {
    for (const child of node.children ?? []) {
      result.set(child.id, {
        x: child.x ?? 0,
        y: child.y ?? 0,
        width: child.width ?? 0,
        height: child.height ?? 0,
      });
      walk(child);
    }
  };
  walk(root);
  return result;
}
