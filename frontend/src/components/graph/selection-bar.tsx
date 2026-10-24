"use client";

import { Scissors, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ru } from "@/lib/i18n/ru";

const t = ru.edit.selection;

/** Панель действий над несколькими выбранными блоками (docs/09 § 3.3). */
export function SelectionBar({
  count,
  onExtract,
  onClear,
}: {
  count: number;
  onExtract: () => void;
  onClear: () => void;
}) {
  return (
    <div
      className="absolute bottom-3 left-1/2 z-10 flex -translate-x-1/2 items-center gap-2 rounded-md border bg-card/95 px-2.5 py-1.5 text-xs shadow-md"
      role="toolbar"
      aria-label={t.title}
      data-testid="selection-bar"
    >
      <span className="text-muted-foreground" data-testid="selection-count">
        {t.count}: {count}
      </span>
      <Button size="xs" variant="secondary" onClick={onExtract} data-testid="selection-extract">
        <Scissors /> {t.extract}
      </Button>
      <Button size="icon-xs" variant="ghost" onClick={onClear} aria-label={t.clear} title={t.clear}>
        <X />
      </Button>
    </div>
  );
}
