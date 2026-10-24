"use client";

import { Panel, useReactFlow, useStore } from "@xyflow/react";
import { Layers, Map as MapIcon, Maximize, Minus, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Toggle } from "@/components/ui/toggle";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ru } from "@/lib/i18n/ru";
import { type Overlays, useWorkspaceStore } from "@/stores/workspace-store";

const t = ru.graph;

function IconButton({
  label,
  onClick,
  children,
  pressed,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
  pressed?: boolean;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant={pressed ? "secondary" : "ghost"}
          size="icon-sm"
          aria-label={label}
          aria-pressed={pressed}
          onClick={onClick}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

const OVERLAY_ITEMS: Array<{ key: keyof Overlays; label: string; disabledHint?: string }> = [
  { key: "findings", label: t.overlayFindings },
  { key: "complexity", label: t.overlayComplexity },
  { key: "calls", label: t.overlayCalls },
  { key: "coverage", label: t.overlayCoverage, disabledHint: t.overlayCoverageSoon },
];

type ZoomProps = { showMiniMap: boolean; onToggleMiniMap: () => void; onFit: () => void };

/** Масштаб `−/+/100%`, «вписать», мини-карта: общая для схемы метода и графа структуры. */
export function ZoomPanel({ showMiniMap, onToggleMiniMap, onFit }: ZoomProps) {
  const { zoomIn, zoomOut, zoomTo } = useReactFlow();
  const zoom = useStore((state) => state.transform[2]);
  return (
    <Panel position="bottom-right" className="!m-2">
      <div className="flex items-center gap-0.5 rounded-lg border bg-card/90 p-1 shadow-sm backdrop-blur">
        <IconButton label={t.zoomOut} onClick={() => void zoomOut({ duration: 150 })}>
          <Minus />
        </IconButton>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 min-w-14 px-2 font-mono text-xs"
              aria-label={t.zoomReset}
              onClick={() => void zoomTo(1, { duration: 150 })}
            >
              {Math.round(zoom * 100)}%
            </Button>
          </TooltipTrigger>
          <TooltipContent>{t.zoomReset}</TooltipContent>
        </Tooltip>
        <IconButton label={t.zoomIn} onClick={() => void zoomIn({ duration: 150 })}>
          <Plus />
        </IconButton>
        <IconButton label={t.fit} onClick={onFit}>
          <Maximize />
        </IconButton>
        <IconButton label={t.minimap} onClick={onToggleMiniMap} pressed={showMiniMap}>
          <MapIcon />
        </IconButton>
      </div>
    </Panel>
  );
}

type Props = ZoomProps & { blocks: number };

/** Панель управления холстом схемы: оверлеи, масштаб, счётчик блоков (docs/09 § 3.3). */
export function CanvasToolbar({ blocks, showMiniMap, onToggleMiniMap, onFit }: Props) {
  const overlays = useWorkspaceStore((s) => s.overlays);
  const setOverlay = useWorkspaceStore((s) => s.setOverlay);

  return (
    <>
      <Panel position="top-left" className="!m-2">
        <div
          className="flex items-center gap-1 rounded-lg border bg-card/90 p-1 shadow-sm backdrop-blur"
          role="group"
          aria-label={t.overlays}
        >
          <Layers className="mx-1 size-4 text-muted-foreground" aria-hidden />
          {OVERLAY_ITEMS.map((item) => (
            <Tooltip key={item.key}>
              <TooltipTrigger asChild>
                <span>
                  <Toggle
                    size="sm"
                    pressed={overlays[item.key]}
                    disabled={item.disabledHint !== undefined}
                    onPressedChange={(value) => setOverlay(item.key, value)}
                    className="h-7 px-2 text-xs"
                  >
                    {item.label}
                  </Toggle>
                </span>
              </TooltipTrigger>
              {item.disabledHint && <TooltipContent>{item.disabledHint}</TooltipContent>}
            </Tooltip>
          ))}
        </div>
      </Panel>

      <ZoomPanel showMiniMap={showMiniMap} onToggleMiniMap={onToggleMiniMap} onFit={onFit} />

      <Panel position="bottom-left" className="!m-2">
        <span className="rounded-md border bg-card/90 px-2 py-1 text-xs text-muted-foreground">
          {blocks} {t.blocks}
        </span>
      </Panel>
    </>
  );
}
