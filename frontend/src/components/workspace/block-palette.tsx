"use client";

import { Search } from "lucide-react";
import { useState } from "react";

import { blockStyle, colorVar } from "@/components/graph/block-style";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { BlockKind } from "@/lib/graph/flow-model";
import { ru } from "@/lib/i18n/ru";

const t = ru.palette;

// Порядок и состав — как в макете; вставка блоков (перетаскивание, клик) приходит в S5.
const ITEMS: BlockKind[] = [
  "var_decl",
  "condition",
  "loop_foreach",
  "loop_for",
  "loop_while",
  "method",
  "operation",
  "return",
  "assign",
  "switch",
  "try",
  "throw",
  "break",
  "continue",
];

export function BlockPalette() {
  const [search, setSearch] = useState("");
  const needle = search.trim().toLowerCase();
  const items = ITEMS.map((kind) => ({ kind, label: t.items[kind as keyof typeof t.items] })).filter((item) =>
    item.label.toLowerCase().includes(needle),
  );

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
        {items.length === 0 && <p className="px-3 py-2 text-xs text-muted-foreground">{t.nothing}</p>}
        <ul className="space-y-1 px-2 pb-2">
          {items.map(({ kind, label }) => {
            const { icon: Icon, color } = blockStyle(kind);
            return (
              <li key={kind}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      aria-disabled
                      className="flex w-full cursor-not-allowed items-center gap-2 rounded-md border bg-card px-2 py-1.5 text-left text-xs opacity-80 hover:opacity-100"
                      style={{ borderLeft: `3px solid ${colorVar(color)}` }}
                    >
                      <Icon className="size-3.5" style={{ color: colorVar(color) }} aria-hidden />
                      {label}
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="right">{t.soon}</TooltipContent>
                </Tooltip>
              </li>
            );
          })}
        </ul>
      </ScrollArea>
    </section>
  );
}
