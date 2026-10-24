"use client";

import "@xyflow/react/dist/style.css";

import {
  Background,
  BackgroundVariant,
  type Edge,
  Handle,
  MarkerType,
  MiniMap,
  type Node,
  type NodeProps,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import { Box, Hash, Loader2, SquareFunction } from "lucide-react";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { layoutWithElk } from "@/lib/graph/elk-client";
import { collectPlacements, type Placement } from "@/lib/graph/flow-layout";
import {
  buildStructureLayout,
  isTypeNode,
  memberCounts,
  type StructureGraph,
  type StructureLayoutInput,
  type StructureNode,
  type StructureView,
} from "@/lib/graph/structure-layout";
import { ru } from "@/lib/i18n/ru";
import { cn } from "@/lib/utils";
import { useWorkspaceStore } from "@/stores/workspace-store";

import { colorVar } from "./block-style";
import { ZoomPanel } from "./canvas-toolbar";

const t = ru.graph;

type NodeData = {
  node: StructureNode;
  members: number;
  [key: string]: unknown;
};
type StructRfNode = Node<NodeData, "type" | "typeContainer" | "member">;

const HIDDEN = "pointer-events-none !size-1 !min-h-0 !min-w-0 !border-0 !bg-transparent";

function Handles() {
  return (
    <>
      <Handle type="target" position={Position.Top} isConnectable={false} className={HIDDEN} />
      <Handle type="source" position={Position.Bottom} isConnectable={false} className={HIDDEN} />
    </>
  );
}

function useSelected(id: string): boolean {
  return useWorkspaceStore((s) => s.selection?.nodeId === id);
}

const TypeCard = memo(function TypeCard({ id, data }: NodeProps<StructRfNode>) {
  const { node, members } = data;
  const selected = useSelected(id);
  return (
    <div
      className={cn(
        "flex h-full w-full flex-col justify-center gap-0.5 rounded-lg border bg-card px-3 shadow-sm",
        selected && "ring-2 ring-primary ring-offset-1 ring-offset-background",
      )}
      style={{ borderColor: colorVar("--node-method", 0.6), borderLeft: `4px solid ${colorVar("--node-method")}` }}
      data-testid="struct-type"
    >
      <Handles />
      <div className="flex items-center gap-1.5">
        <Box className="size-3.5 shrink-0 text-node-method" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-sm font-semibold">{node.name}</span>
        {node.findings_count > 0 && (
          <span className="rounded bg-severity-high/20 px-1.5 text-[10px] text-severity-high">
            {node.findings_count}
          </span>
        )}
      </div>
      <p className="text-[11px] text-muted-foreground">
        {node.kind} · {members} {t.members}
      </p>
    </div>
  );
});

const TypeContainer = memo(function TypeContainer({ id, data }: NodeProps<StructRfNode>) {
  const { node, members } = data;
  const selected = useSelected(id);
  return (
    <div
      className={cn("relative h-full w-full rounded-xl border-2 border-dashed", selected && "ring-2 ring-primary")}
      style={{ borderColor: colorVar("--node-method", 0.6), background: colorVar("--node-method", 0.06) }}
      data-testid="struct-container"
    >
      <Handles />
      <div
        className="absolute inset-x-0 top-0 flex h-10 items-center gap-2 rounded-t-[10px] px-3"
        style={{ background: colorVar("--node-method", 0.14) }}
      >
        <Box className="size-4 shrink-0 text-node-method" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-sm font-semibold">{node.name}</span>
        <span className="text-[11px] text-muted-foreground">
          {node.kind} · {members}
        </span>
      </div>
    </div>
  );
});

const MemberNode = memo(function MemberNode({ id, data }: NodeProps<StructRfNode>) {
  const { node } = data;
  const selected = useSelected(node.method_id ?? id);
  const Icon = node.method_id ? SquareFunction : Hash;
  return (
    <div
      className={cn(
        "flex h-full w-full items-center gap-1.5 rounded-md border bg-card px-2 text-xs",
        selected && "ring-2 ring-primary",
      )}
      title={node.signature ?? node.name}
      data-testid="struct-member"
    >
      <Handles />
      <Icon
        className={cn("size-3 shrink-0", node.method_id ? "text-node-operation" : "text-muted-foreground")}
        aria-hidden
      />
      <span className="min-w-0 flex-1 truncate">{node.name}</span>
      {node.complexity && (
        <span className="rounded bg-primary/15 px-1 font-mono text-[10px] text-primary">{node.complexity}</span>
      )}
      {node.findings_count > 0 && <span className="size-2 rounded-full bg-severity-high" aria-hidden />}
    </div>
  );
});

const nodeTypes = { type: TypeCard, typeContainer: TypeContainer, member: MemberNode };

type Layout = { input: StructureLayoutInput; placements: Map<string, Placement> };

type Props = {
  graph: StructureGraph;
  view: StructureView;
  onViewChange: (view: StructureView) => void;
  onOpenMethod: (methodId: string) => void;
};

function StructureCanvasInner({ graph, view, onViewChange, onOpenMethod }: Props) {
  const reactFlow = useReactFlow();
  const selectNode = useWorkspaceStore((s) => s.selectNode);
  const setActiveFile = useWorkspaceStore((s) => s.setActiveFile);
  const selection = useWorkspaceStore((s) => s.selection);
  const [layout, setLayout] = useState<Layout | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showMiniMap, setShowMiniMap] = useState(false);
  const fitted = useRef<string | null>(null);

  const counts = useMemo(() => memberCounts(graph), [graph]);
  const input = useMemo(() => buildStructureLayout(graph, view), [graph, view]);

  useEffect(() => {
    let cancelled = false;
    layoutWithElk(input.elk)
      .then((result) => {
        if (cancelled) return;
        setLayout({ input, placements: collectPlacements(result) });
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [input]);
  const pending = layout?.input !== input;

  useEffect(() => {
    if (!layout) return;
    const key = `${view}:${graph.content_hash}`;
    if (fitted.current === key) return;
    fitted.current = key;
    requestAnimationFrame(() => void reactFlow.fitView({ padding: 0.1, maxZoom: 1, duration: 0 }));
  }, [layout, view, graph.content_hash, reactFlow]);

  const nodes = useMemo<StructRfNode[]>(() => {
    if (!layout) return [];
    const containers = new Set<string>();
    const collect = (list: typeof layout.input.elk.children) => {
      for (const child of list ?? []) {
        if (child.children?.length) containers.add(child.id);
        collect(child.children);
      }
    };
    collect(layout.input.elk.children);
    return layout.input.nodes.flatMap((node) => {
      const box = layout.placements.get(node.id);
      if (!box) return [];
      const type = isTypeNode(node) ? (containers.has(node.id) ? "typeContainer" : "type") : "member";
      const parent = view === "methods" && node.parent_id && layout.placements.has(node.parent_id) ? node.parent_id : undefined;
      return [
        {
          id: node.id,
          type,
          position: { x: box.x, y: box.y },
          parentId: parent,
          width: box.width,
          height: box.height,
          style: { width: box.width, height: box.height },
          draggable: false,
          connectable: false,
          data: { node, members: counts.get(node.id) ?? 0 },
        } satisfies StructRfNode,
      ];
    });
  }, [layout, counts, view]);

  // Узлы графа структуры выбираются по id члена; у метода он совпадает с id блока «Метод».
  const selectedId = selection?.nodeId ?? null;
  const edges = useMemo<Edge[]>(() => {
    if (!layout) return [];
    const result: Edge[] = layout.input.hierarchy.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "smoothstep",
      label: edge.kind === "extends" ? t.extends : t.implements,
      labelStyle: { fill: "var(--muted-foreground)", fontSize: 11 },
      labelBgStyle: { fill: "var(--card)" },
      markerEnd: { type: MarkerType.ArrowClosed, color: "var(--node-method)", width: 16, height: 16 },
      style: {
        stroke: "var(--node-method)",
        strokeWidth: 1.6,
        strokeDasharray: edge.kind === "implements" ? "6 4" : undefined,
      },
      selectable: false,
    }));
    if (selectedId) {
      const shown = layout.input.links
        .filter((e) => e.source === selectedId || e.target === selectedId)
        .slice(0, 60);
      for (const edge of shown) {
        const color = edge.kind === "calls" ? "var(--node-call)" : "var(--node-operation)";
        result.push({
          id: edge.id,
          source: edge.source,
          target: edge.target,
          type: "default",
          style: { stroke: color, strokeWidth: 1.4, strokeDasharray: "5 4" },
          markerEnd: { type: MarkerType.ArrowClosed, color },
          selectable: false,
        });
      }
    }
    return result;
  }, [layout, selectedId]);

  const fit = useCallback(() => void reactFlow.fitView({ padding: 0.1, maxZoom: 1, duration: 250 }), [reactFlow]);

  if (error) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-destructive" role="alert">
        {t.layoutFailed}: {error}
      </div>
    );
  }

  return (
    <div className="relative h-full w-full" data-testid="structure-canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        colorMode="dark"
        minZoom={0.05}
        maxZoom={2}
        nodesDraggable={false}
        nodesConnectable={false}
        onlyRenderVisibleElements={nodes.length > 120}
        proOptions={{ hideAttribution: true }}
        onNodeClick={(_, rfNode) => {
          const node = (rfNode as StructRfNode).data.node;
          setActiveFile(node.range.file);
          selectNode(node.id, "structure", node.range);
        }}
        onNodeDoubleClick={(_, rfNode) => {
          const node = (rfNode as StructRfNode).data.node;
          if (node.method_id) onOpenMethod(node.method_id);
        }}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} color="var(--border)" />
        {showMiniMap && (
          <MiniMap
            pannable
            zoomable
            className="!bg-card"
            maskColor="color-mix(in oklab, var(--background) 70%, transparent)"
          />
        )}
        <Panel position="top-left" className="!m-2">
          <ToggleGroup
            type="single"
            value={view}
            onValueChange={(value) => value && onViewChange(value as StructureView)}
            variant="outline"
            size="sm"
            className="rounded-lg bg-card/90 shadow-sm backdrop-blur"
            aria-label={ru.tabs.structure}
          >
            <ToggleGroupItem value="types">{t.structureModes.types}</ToggleGroupItem>
            <ToggleGroupItem value="methods">{t.structureModes.methods}</ToggleGroupItem>
          </ToggleGroup>
        </Panel>
        <ZoomPanel showMiniMap={showMiniMap} onToggleMiniMap={() => setShowMiniMap((v) => !v)} onFit={fit} />
      </ReactFlow>
      {pending && (
        <div className="pointer-events-none absolute top-3 left-1/2 flex -translate-x-1/2 items-center gap-2 rounded-md border bg-card/90 px-2.5 py-1 text-xs text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
          {t.layouting}
        </div>
      )}
    </div>
  );
}

export function StructureCanvas(props: Props) {
  return (
    <ReactFlowProvider>
      <StructureCanvasInner {...props} />
    </ReactFlowProvider>
  );
}
