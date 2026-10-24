"use client";

import "@xyflow/react/dist/style.css";

import {
  Background,
  BackgroundVariant,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Viewport,
} from "@xyflow/react";
import { Loader2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { layoutWithElk } from "@/lib/graph/elk-client";
import {
  buildElkGraph,
  buildVisibleFlow,
  collectPlacements,
  isExpanded,
  type Placement,
  type VisibleFlow,
} from "@/lib/graph/flow-layout";
import { modelOf } from "@/lib/graph/flow-cache";
import { ancestorsOf, defaultCollapsed, type FlowGraph, type FlowModel } from "@/lib/graph/flow-model";
import { ru } from "@/lib/i18n/ru";
import { useWorkspaceStore } from "@/stores/workspace-store";

import { blockStyle } from "./block-style";
import { CanvasToolbar } from "./canvas-toolbar";
import { toRfEdge } from "./flow-edges";
import { CanvasContext, type CanvasActions, flowNodeTypes, type FlowRfNode } from "./flow-nodes";

type Layout = { vis: VisibleFlow; placements: Map<string, Placement> };

// Положение камеры по методам: при возврате на вкладку схема остаётся там, где её оставили.
const viewportCache = new Map<string, Viewport>();

function absoluteCenter(model: FlowModel, placements: Map<string, Placement>, id: string) {
  const own = placements.get(id);
  if (!own) return null;
  let x = own.x;
  let y = own.y;
  for (const parent of ancestorsOf(model, id)) {
    const box = placements.get(parent);
    if (!box) break;
    x += box.x;
    y += box.y;
  }
  return { x: x + own.width / 2, y: y + own.height / 2 };
}

function countDescendants(model: FlowModel, id: string): number {
  let count = 0;
  const stack = [...(model.blocks.get(id)?.children ?? [])];
  while (stack.length > 0) {
    const next = model.blocks.get(stack.pop()!);
    if (!next) continue;
    count += 1;
    stack.push(...next.children);
  }
  return count;
}

type Props = {
  projectId: number;
  flow: FlowGraph;
  onOpenMethod: (methodId: string) => void;
};

function FlowCanvasInner({ projectId, flow, onOpenMethod }: Props) {
  const reactFlow = useReactFlow();
  const model = useMemo(() => modelOf(flow), [flow]);
  const overrides = useWorkspaceStore((s) => s.collapsedOverrides[flow.method_id]);
  const overlays = useWorkspaceStore((s) => s.overlays);
  const selection = useWorkspaceStore((s) => s.selection);
  const setCollapsed = useWorkspaceStore((s) => s.setCollapsed);
  const selectNode = useWorkspaceStore((s) => s.selectNode);
  const viewportKey = `${projectId}:${flow.method_id}`;

  const collapsed = useMemo(() => {
    const result = defaultCollapsed(model);
    for (const [id, value] of Object.entries(overrides ?? {})) {
      if (value) result.add(id);
      else result.delete(id);
    }
    return result;
  }, [model, overrides]);

  const vis = useMemo(
    () => buildVisibleFlow(model, { collapsed, showCalls: overlays.calls }),
    [model, collapsed, overlays.calls],
  );

  const [layout, setLayout] = useState<Layout | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showMiniMap, setShowMiniMap] = useState(false);
  const fitted = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    layoutWithElk(buildElkGraph(vis))
      .then((result) => {
        if (cancelled) return;
        setLayout({ vis, placements: collectPlacements(result) });
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [vis]);
  // раскладка ещё считается, пока показанная схема построена не для текущего вида
  const pending = layout?.vis !== vis;

  const toggleCollapse = useCallback(
    (id: string) => setCollapsed(flow.method_id, id, !collapsed.has(id)),
    [collapsed, flow.method_id, setCollapsed],
  );
  const actions = useMemo<CanvasActions>(
    () => ({ toggleCollapse, openMethod: onOpenMethod }),
    [toggleCollapse, onOpenMethod],
  );

  const nodes = useMemo<FlowRfNode[]>(() => {
    if (!layout) return [];
    const { vis: laid, placements } = layout;
    return laid.order.flatMap((id) => {
      const block = laid.model.blocks.get(id);
      const box = placements.get(id);
      if (!block || !box) return [];
      const expanded = isExpanded(laid, id);
      const isCollapsed = !expanded && laid.collapsed.has(id);
      return [
        {
          id,
          type: block.kind === "external" ? "external" : expanded ? "container" : "block",
          position: { x: box.x, y: box.y },
          parentId: block.parentId ?? undefined,
          width: box.width,
          height: box.height,
          style: { width: box.width, height: box.height },
          draggable: false,
          connectable: false,
          data: {
            block,
            collapsed: isCollapsed,
            hiddenCount: isCollapsed ? countDescendants(laid.model, id) : 0,
          },
        } satisfies FlowRfNode,
      ];
    });
  }, [layout]);

  const selectedId = selection?.nodeId ?? null;
  const edges = useMemo(() => {
    if (!layout) return [];
    return layout.vis.edges.map((edge) =>
      toRfEdge(edge, selectedId !== null && (edge.source === selectedId || edge.target === selectedId)),
    );
  }, [layout, selectedId]);

  // Камера: после первой раскладки метода — вписать схему (если камера не сохранена).
  useEffect(() => {
    if (!layout || fitted.current === viewportKey) return;
    fitted.current = viewportKey;
    const saved = viewportCache.get(viewportKey);
    if (saved) void reactFlow.setViewport(saved);
    else requestAnimationFrame(() => void reactFlow.fitView({ padding: 0.12, maxZoom: 1, duration: 0 }));
  }, [layout, reactFlow, viewportKey]);

  // Выбор из кода, дерева или находок: раскрыть свёрнутых предков и показать блок.
  const handled = useRef(0);
  useEffect(() => {
    if (!selection || selection.source === "graph" || handled.current === selection.seq) return;
    if (!model.blocks.has(selection.nodeId)) return;
    const hiddenBy = ancestorsOf(model, selection.nodeId).filter((id) => collapsed.has(id));
    if (hiddenBy.length > 0) {
      for (const id of hiddenBy) setCollapsed(flow.method_id, id, false);
      return; // раскладка пересчитается, и эффект сработает снова
    }
    if (!layout || layout.vis.model !== model) return;
    const center = absoluteCenter(model, layout.placements, selection.nodeId);
    if (!center) return;
    handled.current = selection.seq;
    void reactFlow.setCenter(center.x, center.y, { zoom: Math.max(reactFlow.getZoom(), 0.9), duration: 300 });
  }, [selection, model, collapsed, layout, reactFlow, setCollapsed, flow.method_id]);

  const fitAll = useCallback(() => void reactFlow.fitView({ padding: 0.12, maxZoom: 1, duration: 250 }), [reactFlow]);

  if (error) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-destructive" role="alert">
        {ru.graph.layoutFailed}: {error}
      </div>
    );
  }

  return (
    <CanvasContext.Provider value={{ actions, overlays }}>
      <div className="relative h-full w-full" data-testid="flow-canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={flowNodeTypes}
          colorMode="dark"
          minZoom={0.1}
          maxZoom={2}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable
          onlyRenderVisibleElements={nodes.length > 120}
          proOptions={{ hideAttribution: true }}
          onNodeClick={(_, node) => {
            const block = node.data.block;
            if (block.kind === "external") return;
            selectNode(block.id, "graph", block.range);
          }}
          onNodeDoubleClick={(_, node) => {
            const block = node.data.block;
            if (block.kind === "external" && block.target) onOpenMethod(block.target);
          }}
          onMoveEnd={(_, viewport) => viewportCache.set(viewportKey, viewport)}
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} color="var(--border)" />
          {showMiniMap && (
            <MiniMap
              pannable
              zoomable
              className="!bg-card"
              maskColor="color-mix(in oklab, var(--background) 70%, transparent)"
              nodeColor={(node) => {
                const kind = (node as FlowRfNode).data?.block?.kind;
                return kind ? `var(${blockStyle(kind).color})` : "var(--muted-foreground)";
              }}
            />
          )}
        </ReactFlow>
        <CanvasToolbar
          blocks={model.blocks.size}
          showMiniMap={showMiniMap}
          onToggleMiniMap={() => setShowMiniMap((value) => !value)}
          onFit={fitAll}
        />
        {pending && (
          <div className="pointer-events-none absolute top-3 left-1/2 flex -translate-x-1/2 items-center gap-2 rounded-md border bg-card/90 px-2.5 py-1 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
            {layout ? ru.graph.layouting : ru.graph.loading}
          </div>
        )}
      </div>
    </CanvasContext.Provider>
  );
}

export function FlowCanvas(props: Props) {
  return (
    <ReactFlowProvider>
      <FlowCanvasInner {...props} />
    </ReactFlowProvider>
  );
}
