"use client";

import { Search } from "lucide-react";
import { useState } from "react";

import { blockStyle, colorVar } from "@/components/graph/block-style";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { beginDrag, endDrag } from "@/lib/edit/drag";
import { useInsertBlock } from "@/lib/edit/use-insert";
import type { NodeKind } from "@/lib/graph/flow-model";
import { ru } from "@/lib/i18n/ru";

const t = ru.palette;

// Блоки макета, которых нет среди вставляемых backend'ом: показываются отключёнными (их ещё нельзя вставить).
const NOT_INSERTABLE: NodeKind[] = ["switch", "method"];

type Item = { kind: NodeKind; label: string; insertable: boolean; template?: string };

export function BlockPalette({ projectId }: { projectId: number }) {
  const [search, setSearch] = useState("");
  const { insertBlock, blocks, loading } = useInsertBlock(projectId);
  const needle = search.trim().toLowerCase();

  const fromApi: Item[] = (blocks ?? []).map((block) => ({
    kind: block.kind,
    label: block.title,
    insertable: true,
    template: block.template,
  }));
  const known = new Set(fromApi.map((item) => item.kind));
  const rest: Item[] = NOT_INSERTABLE.filter((kind) => !known.has(kind)).map((kind) => ({
    kind,
    label: t.items[kind as keyof typeof t.items],
    insertable: false,
  }));
  const items = [...fromApi, ...rest].filter((item) => item.label.toLowerCase().includes(needle));

  return (
    <section className="flex h-full min-h-0 flex-col" aria-label={t.title}>
      <div className="flex items-center border-b border-t px-3 py-2">
        <h2 className="text-sm font-semibold">{t.title}</h2>
      </div>
      <div className="relative px-2 py-2">
        <Search className="pointer-events-none absolute top-1/2 left-4 size-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder={t.search}
          aria-label={t.search}
          className="h-8 pl-7 text-xs"
        />
      </div>
      <ScrollArea className="min-h-0 flex-1">
        {loading && <Skeleton className="mx-2 h-24" />}
        {!loading && items.length === 0 && <p className="px-3 py-2 text-xs text-muted-foreground">{t.nothing}</p>}
        <ul className="space-y-1 px-2 pb-2">
          {items.map(({ kind, label, insertable, template }) => {
            const { icon: Icon, color } = blockStyle(kind);
            return (
              <li key={kind}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      aria-disabled={!insertable || undefined}
                      draggable={insertable}
                      data-testid={`palette-${kind}`}
                      onClick={() => insertable && void insertBlock(kind)}
                      onDragStart={(event) => insertable && beginDrag(event, { type: "palette", kind })}
                      onDragEnd={endDrag}
                      className={
                        insertable
                          ? "flex w-full cursor-grab items-center gap-2 rounded-md border bg-card px-2 py-1.5 text-left text-xs hover:bg-accent/60 active:cursor-grabbing"
                          : "flex w-full cursor-not-allowed items-center gap-2 rounded-md border bg-card px-2 py-1.5 text-left text-xs opacity-60"
                      }
                      style={{ borderLeft: `3px solid ${colorVar(color)}` }}
                    >
                      <Icon className="size-3.5" style={{ color: colorVar(color) }} aria-hidden />
                      {label}
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="right" className="max-w-64">
                    {insertable ? (
                      <>
                        {template && <code className="block font-mono text-[11px]">{template}</code>}
                        <span className="text-[11px] opacity-80">{t.hint}</span>
                      </>
                    ) : (
                      t.notInsertable
                    )}
                  </TooltipContent>
                </Tooltip>
              </li>
            );
          })}
        </ul>
      </ScrollArea>
    </section>
  );
}
