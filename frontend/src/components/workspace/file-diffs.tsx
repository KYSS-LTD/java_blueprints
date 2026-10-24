"use client";

import dynamic from "next/dynamic";
import { useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import type { FileDiff } from "@/lib/api/actions";
import { cn } from "@/lib/utils";

const DiffView = dynamic(() => import("@/components/code/diff-view"), {
  ssr: false,
  loading: () => <Skeleton className="m-3 h-40" />,
});

const STATUS_MARK: Record<FileDiff["status"], string> = { added: "+", removed: "−", modified: "~" };

/** Правки нескольких файлов: список слева, Monaco DiffEditor справа. */
export function FileDiffs({
  diffs,
  height = "h-72",
  language,
}: {
  diffs: FileDiff[];
  height?: string;
  language?: string;
}) {
  const [picked, setPicked] = useState<string | null>(null);
  const current = diffs.find((d) => d.path === picked) ?? diffs[0];
  if (!current) return null;
  return (
    <div className="flex min-h-0 flex-col gap-2 sm:flex-row" data-testid="file-diffs">
      <ul
        className="flex shrink-0 gap-1 overflow-x-auto sm:w-44 sm:flex-col"
        aria-label="Изменённые файлы"
      >
        {diffs.map((diff) => (
          <li key={diff.path}>
            <button
              type="button"
              onClick={() => setPicked(diff.path)}
              aria-pressed={diff.path === current.path}
              className={cn(
                "w-full truncate rounded px-2 py-1 text-left font-mono text-xs hover:bg-accent",
                diff.path === current.path && "bg-accent",
              )}
              title={diff.path}
            >
              <span className="mr-1 text-muted-foreground">{STATUS_MARK[diff.status]}</span>
              {diff.path}
            </button>
          </li>
        ))}
      </ul>
      <div className={cn("min-w-0 flex-1 overflow-hidden rounded-md border", height)}>
        <DiffView
          path={current.path}
          before={current.before ?? ""}
          after={current.after ?? ""}
          language={language}
        />
      </div>
    </div>
  );
}
