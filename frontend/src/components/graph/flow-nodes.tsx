"use client";

import { Handle, type Node, type NodeProps, Position } from "@xyflow/react";
import { ChevronDown, ChevronRight, ExternalLink, TriangleAlert } from "lucide-react";
import { createContext, memo, useContext, useMemo } from "react";

import { Badge } from "@/components/ui/badge";
import { blockHeat, type BlockHeat, formatCount } from "@/lib/debug/trace";
import { beginDrag, endDrag } from "@/lib/edit/drag";
import { ru } from "@/lib/i18n/ru";
import type { Block } from "@/lib/graph/flow-model";
import { cn } from "@/lib/utils";
import { useDebugStore } from "@/stores/debug-store";
import { type Overlays, useWorkspaceStore } from "@/stores/workspace-store";

import { blockStyle, colorVar } from "./block-style";

export type BlockNodeData = {
  block: Block;
  /** Контейнер свёрнут и показывается как обычный блок. */
  collapsed: boolean;
  /** Сколько блоков скрыто внутри свёрнутого контейнера. */
  hiddenCount: number;
  /** Блок можно перетащить на другое место схемы. */
  movable: boolean;
  [key: string]: unknown;
};

export type FlowRfNode = Node<BlockNodeData, "block" | "container" | "external">;

export type CanvasActions = {
  toggleCollapse: (id: string) => void;
  openMethod: (methodId: string) => void;
};

/** Куда упадёт перетаскиваемый блок: перед блоком, после него или внутрь контейнера. */
export type DropHint = { blockId: string; mode: "before" | "after" | "into" };

type CanvasContextValue = { actions: CanvasActions; overlays: Overlays; dropHint: DropHint | null };

export const CanvasContext = createContext<CanvasContextValue>({
  actions: { toggleCollapse: () => {}, openMethod: () => {} },
  overlays: { findings: true, complexity: true, coverage: false, calls: true },
  dropHint: null,
});

function useIsSelected(id: string): boolean {
  return useWorkspaceStore((state) => state.selection?.nodeId === id);
}

const NO_HEAT: BlockHeat = { state: "none" };

/** Счётчики трассировки для блока (если включён оверлей «Покрытие» и «Дебаг» уже отработал). */
function useHeat(id: string): BlockHeat {
  const { overlays } = useContext(CanvasContext);
  const trace = useDebugStore((s) => s.trace);
  return useMemo(() => (overlays.coverage ? blockHeat(trace, id) : NO_HEAT), [overlays.coverage, trace, id]);
}

/** Подсказка к счётчику: исходы условия, входы в цикл. */
function heatTitle(heat: BlockHeat, kind: string): string {
  const d = ru.graph.debug;
  if (heat.state === "idle") return d.idle;
  if (heat.state !== "hit") return "";
  const what = kind.startsWith("loop") ? d.iterations : kind === "condition" ? d.conditions : d.executions;
  const parts = [`${what}: ${heat.count}`];
  if (heat.entries !== undefined) parts.push(`${d.entered}: ${heat.entries}`);
  if (heat.branch) parts.push(`${d.yes} ${heat.branch.true} / ${d.no} ${heat.branch.false}`);
  return parts.join(" · ");
}

/** Фон блока: чем чаще он выполнялся, тем насыщеннее (логарифмическая шкала). */
function heatBackground(heat: BlockHeat): string | undefined {
  if (heat.state !== "hit") return undefined;
  return `color-mix(in oklab, var(--primary) ${Math.round(heat.heat * 22)}%, var(--card))`;
}

const HIDDEN_HANDLE = "pointer-events-none !size-1 !min-h-0 !min-w-0 !border-0 !bg-transparent";

function Handles({ headerOffset }: { headerOffset?: number }) {
  return (
    <>
      <Handle id="t" type="target" position={Position.Top} isConnectable={false} className={HIDDEN_HANDLE} />
      <Handle id="b" type="source" position={Position.Bottom} isConnectable={false} className={HIDDEN_HANDLE} />
      <Handle
        id="lt"
        type="target"
        position={Position.Left}
        isConnectable={false}
        className={HIDDEN_HANDLE}
        style={headerOffset === undefined ? undefined : { top: headerOffset }}
      />
      <Handle id="ls" type="source" position={Position.Left} isConnectable={false} className={HIDDEN_HANDLE} />
    </>
  );
}

const CHIP = "rounded px-1.5 py-px text-[10px] leading-4 font-medium whitespace-nowrap";

function BlockChips({ block, heat }: { block: Block; heat: BlockHeat }) {
  const { overlays } = useContext(CanvasContext);
  const defect = block.badges.find((b) => b.kind === "defect");
  const io = block.badges.filter((b) => b.kind === "output" || b.kind === "input" || b.kind === "error_output");
  const calls = block.badges.filter((b) => b.kind === "call");
  return (
    <div className="flex shrink-0 items-center gap-1">
      {heat.state !== "none" && (
        <span
          className={cn(
            CHIP,
            "font-mono",
            heat.state === "hit" ? "bg-primary/20 text-primary" : "bg-muted text-muted-foreground",
          )}
          title={heatTitle(heat, block.kind)}
          data-testid="heat-chip"
        >
          ×{heat.state === "hit" ? formatCount(heat.count) : 0}
        </span>
      )}
      {overlays.findings && defect && (
        <span
          className={cn(CHIP, "flex items-center gap-1 bg-severity-high/20 text-severity-high")}
          title={defect.label}
        >
          <TriangleAlert className="size-3" aria-hidden />
          {defect.label}
        </span>
      )}
      {io.map((badge) => (
        <span key={badge.kind} className={cn(CHIP, "bg-muted text-muted-foreground")}>
          {badge.label}
        </span>
      ))}
      {!overlays.calls && calls.length > 0 && (
        <span className={cn(CHIP, "bg-node-call/20 text-node-call")} title={calls.map((c) => c.label).join(", ")}>
          ⇢ {calls.length}
        </span>
      )}
      {overlays.complexity && block.complexity && (
        <span className={cn(CHIP, "bg-primary/20 font-mono text-primary")} title="Оценка сложности">
          {block.complexity}
        </span>
      )}
    </div>
  );
}

function selectionRing(selected: boolean): string {
  return selected ? "ring-2 ring-primary ring-offset-1 ring-offset-background" : "";
}

/** Подсветка места, куда упадёт блок: полоса сверху или снизу, для контейнера — рамка вокруг. */
function dropStyle(hint: DropHint | null, id: string): React.CSSProperties | undefined {
  if (!hint || hint.blockId !== id) return undefined;
  if (hint.mode === "before") return { boxShadow: "0 -4px 0 0 var(--primary)" };
  if (hint.mode === "after") return { boxShadow: "0 4px 0 0 var(--primary)" };
  return { outline: "2px dashed var(--primary)", outlineOffset: 2 };
}

/** Свойства перетаскиваемого блока: `nopan` отключает панорамирование холста, иначе оно отменяет перетаскивание. */
function dragProps(id: string, movable: boolean) {
  return movable
    ? {
        draggable: true,
        onDragStart: (event: React.DragEvent) => beginDrag(event, { type: "node", nodeId: id }),
        onDragEnd: endDrag,
      }
    : {};
}

/** Обычный блок: заголовок вида, код оператора, бейджи. */
export const BlockNode = memo(function BlockNode({ id, data }: NodeProps<FlowRfNode>) {
  const { block, collapsed, hiddenCount, movable } = data;
  const { actions, dropHint } = useContext(CanvasContext);
  const selected = useIsSelected(id);
  const heat = useHeat(id);
  const { icon: Icon, color } = blockStyle(block.kind);
  const hasError = block.errors.length > 0;

  return (
    <div
      className={cn(
        "relative flex h-full w-full flex-col justify-center gap-0.5 overflow-hidden rounded-lg border bg-card py-1.5 pr-2.5 pl-3.5 text-card-foreground shadow-sm transition-shadow hover:shadow-md",
        selectionRing(selected),
        hasError && "border-destructive",
        movable && "nopan cursor-grab",
        heat.state === "idle" && "opacity-45 grayscale",
      )}
      style={{
        borderColor: hasError ? undefined : colorVar(color, 0.55),
        background: heatBackground(heat),
        ...dropStyle(dropHint, id),
      }}
      title={block.code}
      data-testid={`block-${block.kind}`}
      {...dragProps(id, movable)}
    >
      <Handles />
      <span className="absolute inset-y-0 left-0 w-1.5" style={{ background: colorVar(color) }} aria-hidden />
      <div className="flex items-center gap-1.5">
        <Icon className="size-3.5 shrink-0" style={{ color: colorVar(color) }} aria-hidden />
        <span className="min-w-0 flex-1 truncate text-xs font-semibold">{block.title}</span>
        <BlockChips block={block} heat={heat} />
        {collapsed && (
          <button
            type="button"
            className="nodrag nopan -mr-1 flex shrink-0 items-center gap-0.5 rounded px-1 text-[10px] text-muted-foreground hover:bg-accent"
            aria-label={`${ru.graph.expand}: ${block.title}`}
            onClick={(event) => {
              event.stopPropagation();
              actions.toggleCollapse(id);
            }}
          >
            <ChevronRight className="size-3" />+{hiddenCount}
          </button>
        )}
      </div>
      <p className="truncate font-mono text-[11px] leading-4 text-muted-foreground">{block.code || "—"}</p>
    </div>
  );
});

/** Развёрнутый контейнер: цикл, try, catch, switch, лямбда. Содержимое рисует React Flow внутри. */
export const ContainerNode = memo(function ContainerNode({ id, data }: NodeProps<FlowRfNode>) {
  const { block, movable } = data;
  const { actions, dropHint } = useContext(CanvasContext);
  const selected = useIsSelected(id);
  const heat = useHeat(id);
  const { icon: Icon, color } = blockStyle(block.kind);
  const subtitle = block.kind === "catch" && block.exception ? block.exception : block.code;

  return (
    <div
      className={cn(
        "relative h-full w-full rounded-xl border-2 border-dashed",
        selectionRing(selected),
        heat.state === "idle" && "opacity-60 grayscale",
      )}
      style={{ borderColor: colorVar(color, 0.6), background: colorVar(color, 0.06), ...dropStyle(dropHint, id) }}
      data-testid={`container-${block.kind}`}
    >
      <Handles headerOffset={22} />
      {/* тащить контейнер можно только за заголовок: пустое поле внутри нужно для панорамирования и сброса блоков */}
      <div
        className={cn(
          "absolute inset-x-0 top-0 flex h-11 items-center gap-2 rounded-t-[10px] px-3",
          movable && "nopan cursor-grab",
        )}
        style={{ background: colorVar(color, 0.14) }}
        title={block.code}
        {...dragProps(id, movable)}
      >
        <Icon className="size-4 shrink-0" style={{ color: colorVar(color) }} aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-semibold">{block.title}</p>
          <p className="truncate font-mono text-[11px] leading-4 text-muted-foreground">{subtitle}</p>
        </div>
        <BlockChips block={block} heat={heat} />
        <button
          type="button"
          className="nodrag nopan flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
          aria-label={`${ru.graph.collapse}: ${block.title}`}
          onClick={(event) => {
            event.stopPropagation();
            actions.toggleCollapse(id);
          }}
        >
          <ChevronDown className="size-4" />
        </button>
      </div>
    </div>
  );
});

/** Вызов метода, которого нет в этой схеме: жёлтый пунктир, двойной клик открывает схему вызываемого. */
export const ExternalNode = memo(function ExternalNode({ id, data }: NodeProps<FlowRfNode>) {
  const { block } = data;
  const { actions } = useContext(CanvasContext);
  const selected = useIsSelected(id);

  return (
    <div
      className={cn(
        "relative flex h-full w-full items-center gap-2 rounded-lg border-2 border-dashed bg-node-call/10 px-2.5",
        selectionRing(selected),
      )}
      style={{ borderColor: colorVar("--node-call", 0.8) }}
      title={`${ru.graph.openCallee}: ${block.code}`}
      data-testid="block-external"
    >
      <Handles />
      <ExternalLink className="size-3.5 shrink-0 text-node-call" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="truncate text-[10px] leading-3 text-node-call">{ru.graph.externalCall}</p>
        <p className="truncate font-mono text-[11px] leading-4">{block.code}</p>
      </div>
      <button
        type="button"
        className="nodrag nopan rounded px-1 text-[10px] text-muted-foreground hover:bg-accent hover:text-foreground"
        aria-label={`${ru.graph.openCallee}: ${block.code}`}
        onClick={(event) => {
          event.stopPropagation();
          if (block.target) actions.openMethod(block.target);
        }}
      >
        <Badge variant="outline" className="px-1 py-0 text-[10px]">
          {ru.graph.open}
        </Badge>
      </button>
    </div>
  );
});

export const flowNodeTypes = {
  block: BlockNode,
  container: ContainerNode,
  external: ExternalNode,
};
