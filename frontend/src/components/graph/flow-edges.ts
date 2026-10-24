import { type Edge, MarkerType } from "@xyflow/react";

import { ru } from "@/lib/i18n/ru";
import { type BlockEdge, type FlowEdgeKind, isLoopEdge } from "@/lib/graph/flow-model";

// Свойство pathOptions есть только у встроенного ребра smoothstep, но тип для него не экспортируется.
type SmoothStepEdge = Edge & { pathOptions?: { borderRadius?: number; offset?: number } };

type EdgeLook = {
  color: string;
  dashed?: boolean;
  /** Длинные «прыжки» (break, continue, петля) приглушены, пока не выбран их конец. */
  quiet?: boolean;
  width?: number;
};

const LOOKS: Record<FlowEdgeKind, EdgeLook> = {
  next: { color: "var(--muted-foreground)" },
  return: { color: "var(--node-return)" },
  true: { color: "var(--node-return)", width: 2 },
  false: { color: "var(--severity-high)", width: 2 },
  case: { color: "var(--node-condition)" },
  body: { color: "var(--muted-foreground)" },
  back: { color: "var(--node-loop)", dashed: true, quiet: true },
  continue: { color: "var(--node-condition)", dashed: true, quiet: true },
  break: { color: "var(--node-condition)", dashed: true, quiet: true },
  exception: { color: "var(--severity-high)", dashed: true },
  call: { color: "var(--node-call)", dashed: true },
};

export function edgeLabel(edge: BlockEdge): string | undefined {
  if (edge.kind === "true") return ru.graph.yes;
  if (edge.kind === "false") return ru.graph.no;
  if (edge.kind === "back") return "↻";
  if (edge.label && edge.kind !== "call") return edge.label.length > 18 ? `${edge.label.slice(0, 17)}…` : edge.label;
  return undefined;
}

/** Ребро React Flow для ребра блок-схемы; `focused` — выбран один из концов ребра. */
export function toRfEdge(edge: BlockEdge, focused: boolean): SmoothStepEdge {
  const look = LOOKS[edge.kind];
  const loop = isLoopEdge(edge.kind);
  const quiet = look.quiet && !focused;
  const label = edgeLabel(edge);
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    // петли идут по левому краю контейнера: заголовок контейнера — вход, хвост тела — выход
    sourceHandle: loop ? "ls" : "b",
    targetHandle: loop ? "lt" : "t",
    type: "smoothstep",
    pathOptions: { borderRadius: 10, offset: loop ? 28 : 20 },
    label,
    labelShowBg: label !== undefined,
    labelBgPadding: [6, 2],
    labelBgBorderRadius: 9,
    labelBgStyle: { fill: "var(--card)", stroke: look.color, strokeWidth: 1, opacity: quiet ? 0.5 : 1 },
    labelStyle: { fill: look.color, fontSize: 11, fontWeight: 600 },
    markerEnd: { type: MarkerType.ArrowClosed, color: look.color, width: 16, height: 16 },
    style: {
      stroke: look.color,
      strokeWidth: focused ? (look.width ?? 1.5) + 1 : (look.width ?? 1.5),
      strokeDasharray: look.dashed ? "6 4" : undefined,
      opacity: quiet ? 0.4 : 1,
    },
    interactionWidth: 10,
    selectable: false,
    focusable: false,
    data: { kind: edge.kind },
  };
}
