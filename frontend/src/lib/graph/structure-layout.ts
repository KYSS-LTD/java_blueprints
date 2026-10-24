import type { ElkExtendedEdge, ElkNode } from "elkjs/lib/elk-api";

import type { components } from "@/lib/api/schema";

type Schemas = components["schemas"];
export type StructureGraph = Schemas["StructureGraph"];
export type StructureNode = Schemas["StructureNode"];
export type StructureEdge = Schemas["StructureEdge"];

/** Режимы вкладки «Структура»: типы с наследованием и типы с методами. */
export type StructureView = "types" | "methods";

export const TYPE_SIZE = { width: 236, height: 70 };
export const MEMBER_SIZE = { width: 216, height: 34 };

const PADDING = "[top=48,left=16,bottom=16,right=16]";

export type StructureLayoutInput = {
  elk: ElkNode;
  /** Видимые узлы в порядке обхода (предок раньше потомков). */
  nodes: StructureNode[];
  /** Рёбра иерархии типов (extends, implements) между видимыми узлами. */
  hierarchy: StructureEdge[];
  /** Рёбра вызовов и использований между видимыми узлами: их показываем для выбранного члена. */
  links: StructureEdge[];
};

export function isTypeNode(node: StructureNode): boolean {
  return node.node_type === "type";
}

/** Число членов у типа (прямых потомков). */
export function memberCounts(graph: StructureGraph): Map<string, number> {
  const counts = new Map<string, number>();
  for (const node of graph.nodes) {
    if (node.parent_id !== null && !isTypeNode(node)) {
      counts.set(node.parent_id, (counts.get(node.parent_id) ?? 0) + 1);
    }
  }
  return counts;
}

/**
 * Граф структуры для ELK. «Типы»: плоские карточки с рёбрами наследования, супертипы выше.
 * «Методы»: типы-контейнеры с членами внутри, члены упакованы сеткой.
 */
export function buildStructureLayout(graph: StructureGraph, view: StructureView): StructureLayoutInput {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const visible = graph.nodes.filter((n) => (view === "methods" ? true : isTypeNode(n)));
  const visibleIds = new Set(visible.map((n) => n.id));

  const parentOf = (node: StructureNode): string | null =>
    view === "methods" && node.parent_id !== null && visibleIds.has(node.parent_id) ? node.parent_id : null;

  const children = new Map<string | null, StructureNode[]>();
  for (const node of visible) {
    const key = parentOf(node);
    children.set(key, [...(children.get(key) ?? []), node]);
  }

  const hierarchy = graph.edges.filter(
    (e) => (e.kind === "extends" || e.kind === "implements") && visibleIds.has(e.source) && visibleIds.has(e.target),
  );
  const links = graph.edges.filter(
    (e) => (e.kind === "calls" || e.kind === "uses") && visibleIds.has(e.source) && visibleIds.has(e.target),
  );

  // Рёбра иерархии на уровне типов: в слое сверху стоит супертип, поэтому направление в раскладке обратное.
  const typeEdges = new Map<string | null, ElkExtendedEdge[]>();
  for (const edge of hierarchy) {
    const a = byId.get(edge.target);
    const b = byId.get(edge.source);
    if (!a || !b) continue;
    const parent = parentOf(a);
    if (parent !== parentOf(b)) continue;
    typeEdges.set(parent, [...(typeEdges.get(parent) ?? []), { id: `h:${edge.id}`, sources: [a.id], targets: [b.id] }]);
  }

  const order: StructureNode[] = [];
  const toElk = (node: StructureNode): ElkNode => {
    order.push(node);
    const inner = children.get(node.id);
    if (view === "methods" && isTypeNode(node) && inner && inner.length > 0) {
      return {
        id: node.id,
        layoutOptions: {
          "elk.algorithm": "box",
          "elk.aspectRatio": "1.3",
          "elk.spacing.nodeNode": "10",
          "elk.padding": PADDING,
        },
        children: inner.map(toElk),
        edges: typeEdges.get(node.id) ?? [],
      };
    }
    return { id: node.id, ...(isTypeNode(node) ? TYPE_SIZE : MEMBER_SIZE) };
  };

  const roots = (children.get(null) ?? []).map(toElk);
  return {
    elk: {
      id: "root",
      layoutOptions: {
        "elk.algorithm": "layered",
        "elk.direction": "DOWN",
        "elk.hierarchyHandling": "SEPARATE_CHILDREN",
        "elk.spacing.nodeNode": "40",
        "elk.layered.spacing.nodeNodeBetweenLayers": "56",
        "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
        "elk.padding": "[top=24,left=24,bottom=24,right=24]",
      },
      children: roots,
      edges: typeEdges.get(null) ?? [],
    },
    nodes: order,
    hierarchy,
    links,
  };
}
