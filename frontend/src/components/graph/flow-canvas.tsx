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

import { currentDrag, endDrag } from "@/lib/edit/drag";
import { dropPoint, isInside, isMovable, isSamePlace, methodBodyEnd } from "@/lib/edit/slots";
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
import { isEditableTarget } from "@/lib/edit/use-hotkeys";
import { ru } from "@/lib/i18n/ru";
import { useEditorStore } from "@/stores/editor-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

import { blockStyle } from "./block-style";
import { CanvasToolbar } from "./canvas-toolbar";
import { toRfEdge } from "./flow-edges";
import { CanvasContext, type CanvasActions, type DropHint, flowNodeTypes, type FlowRfNode } from "./flow-nodes";
import { DeleteBlockDialog, type MenuState, NodeMenu } from "./node-menu";
import { capabilitiesOf, useCanvasEditing } from "./use-canvas-editing";

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

/** Контейнеры, в тело которых можно бросить блок. */
function isContainerKind(kind: string): boolean {
  return ["loop_for", "loop_foreach", "loop_while", "try", "catch", "finally"].includes(kind);
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
  versionId?: number;
  flow: FlowGraph;
  /** Правка разрешена: нет синтаксических ошибок, версия известна. */
  editable?: boolean;
  onOpenMethod: (methodId: string) => void;
};

function FlowCanvasInner({ projectId, flow, editable = false, onOpenMethod }: Props) {
  const reactFlow = useReactFlow();
  const model = useMemo(() => modelOf(flow), [flow]);
  const overrides = useWorkspaceStore((s) => s.collapsedOverrides[flow.method_id]);
  const overlays = useWorkspaceStore((s) => s.overlays);
  const selection = useWorkspaceStore((s) => s.selection);
  const setCollapsed = useWorkspaceStore((s) => s.setCollapsed);
  const selectNode = useWorkspaceStore((s) => s.selectNode);
  const viewportKey = `${projectId}:${flow.method_id}`;
  const wrapper = useRef<HTMLDivElement>(null);
  const editing = useCanvasEditing(projectId, model);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [dropHint, setDropHint] = useState<DropHint | null>(null);

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

  // Куда упадёт перетаскиваемый блок: самый глубокий блок под указателем и его половина (верх/низ).
  const resolveDrop = useCallback(
    (clientX: number, clientY: number) => {
      const payload = currentDrag();
      if (!payload || !editable) return null;
      let target: { id: string; half: "top" | "bottom" } | null = null;
      let depth = -1;
      if (typeof document.elementsFromPoint === "function") {
        for (const element of document.elementsFromPoint(clientX, clientY)) {
          const nodeElement = element.closest(".react-flow__node");
          const id = nodeElement?.getAttribute("data-id");
          const block = id ? model.blocks.get(id) : undefined;
          if (!nodeElement || !block || block.kind === "external" || block.depth <= depth) continue;
          const rect = nodeElement.getBoundingClientRect();
          depth = block.depth;
          target = { id: block.id, half: clientY < rect.top + rect.height / 2 ? "top" : "bottom" };
        }
      }
      const point = target ? dropPoint(model, target.id, target.half) : methodBodyEnd(model);
      if (!point) return null;
      if (
        payload.type === "node" &&
        (isInside(model, point.parentId, payload.nodeId) || isSamePlace(model, payload.nodeId, point))
      ) {
        return null;
      }
      const block = target ? model.blocks.get(target.id) : undefined;
      const into = block !== undefined && isContainerKind(block.kind);
      const hint: DropHint | null = target
        ? { blockId: target.id, mode: into ? "into" : target.half === "top" ? "before" : "after" }
        : null;
      return { payload, point, hint };
    },
    [editable, model],
  );

  const onDragOver = (event: React.DragEvent) => {
    const drop = resolveDrop(event.clientX, event.clientY);
    if (!drop) {
      setDropHint(null);
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = drop.payload.type === "node" ? "move" : "copy";
    setDropHint((previous) =>
      previous?.blockId === drop.hint?.blockId && previous?.mode === drop.hint?.mode ? previous : drop.hint,
    );
  };

  const onDrop = (event: React.DragEvent) => {
    const drop = resolveDrop(event.clientX, event.clientY);
    setDropHint(null);
    if (!drop) return;
    event.preventDefault();
    const { payload, point } = drop;
    endDrag();
    if (payload.type === "palette") void editing.insert(payload.kind, point);
    else editing.move(payload.nodeId, point);
  };

  // Клавиши холста: Delete, Ctrl+D, Enter (docs/09 § 3.3). Поля ввода и редактор кода забирают клавиши себе.
  const onKeyDown = (event: React.KeyboardEvent) => {
    // Меню и диалоги рисуются порталом, но события React всплывают через него: чужие клавиши не наши
    if (!editable || isEditableTarget(event.target) || !event.currentTarget.contains(event.target as Node)) return;
    if (event.key === "Escape") {
      useEditorStore.getState().openInspector(null);
      return;
    }
    const block = selection ? model.blocks.get(selection.nodeId) : undefined;
    if (!block) return;
    const caps = capabilitiesOf(model, block);
    if ((event.key === "Delete" || event.key === "Backspace") && caps.remove) {
      event.preventDefault();
      editing.requestDelete(block.id);
    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "d" && caps.duplicate) {
      event.preventDefault();
      editing.duplicate(block.id);
    } else if (event.key === "Enter" && caps.edit) {
      event.preventDefault();
      editing.edit(block.id);
    }
  };

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
            movable: editable && isMovable(laid.model, id),
          },
        } satisfies FlowRfNode,
      ];
    });
  }, [layout, editable]);

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
    if (selection.source === "edit") {
      // после правки схема не прыгает: камера двигается, только если блок вышел за границы окна
      const { x, y, zoom } = reactFlow.getViewport();
      const box = wrapper.current?.getBoundingClientRect();
      const screenX = center.x * zoom + x;
      const screenY = center.y * zoom + y;
      const inside = box !== undefined && screenX > 40 && screenY > 40 && screenX < box.width - 40 && screenY < box.height - 40;
      if (!inside) void reactFlow.setCenter(center.x, center.y, { zoom, duration: 300 });
      return;
    }
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
    <CanvasContext.Provider value={{ actions, overlays, dropHint }}>
      <div
        ref={wrapper}
        className="relative h-full w-full outline-none"
        data-testid="flow-canvas"
        data-editable={editable}
        onKeyDown={onKeyDown}
        onDragOver={onDragOver}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropHint(null);
        }}
        onDrop={onDrop}
      >
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
            else if (editable && capabilitiesOf(model, block).edit) editing.edit(block.id);
          }}
          onNodeContextMenu={(event, node) => {
            const block = node.data.block;
            if (!editable || block.kind === "external") return;
            event.preventDefault();
            selectNode(block.id, "graph", block.range);
            setMenu({ x: event.clientX, y: event.clientY, blockId: block.id });
          }}
          deleteKeyCode={null}
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
        <NodeMenu model={model} state={menu} actions={editing} onClose={() => setMenu(null)} />
        <DeleteBlockDialog
          model={model}
          blockId={editing.pendingDelete}
          onCancel={editing.cancelDelete}
          onDelete={(id, mode) => void editing.remove(id, mode)}
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
