"use client";

import { useQuery } from "@tanstack/react-query";
import { History as HistoryIcon, RotateCcw } from "lucide-react";
import dynamic from "next/dynamic";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import type { VersionInfo } from "@/lib/api/mutations";
import { diffQuery, projectQuery, versionsQuery } from "@/lib/api/queries";
import { useEditing } from "@/lib/edit/use-editing";
import { ru } from "@/lib/i18n/ru";
import { cn } from "@/lib/utils";

const t = ru.history;

const DiffView = dynamic(() => import("@/components/code/diff-view"), {
  ssr: false,
  loading: () => <Skeleton className="m-3 h-40" />,
});

type DiffRequest = { from: number; to: number; title: string };

function formatTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

/** Различия двух версий: список файлов слева, сравнение рядом справа. */
function DiffDialog({ projectId, request, onClose }: { projectId: number; request: DiffRequest | null; onClose: () => void }) {
  const diff = useQuery({
    ...diffQuery(projectId, request?.from ?? 0, request?.to ?? 0),
    enabled: request !== null,
  });
  const [picked, setPicked] = useState<string | null>(null);
  const files = diff.data?.files ?? [];
  const current = files.find((f) => f.path === picked) ?? files[0];

  return (
    <Dialog open={request !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex h-[80vh] flex-col gap-3 sm:max-w-5xl" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>{request?.title}</DialogTitle>
          <DialogDescription className="sr-only">{t.compare}</DialogDescription>
        </DialogHeader>
        {diff.isLoading && <Skeleton className="h-64" />}
        {diff.error && <p className="text-sm text-destructive">{t.diffFailed}: {diff.error.message}</p>}
        {diff.data && files.length === 0 && <p className="text-sm text-muted-foreground">{t.noDiff}</p>}
        {current && (
          <>
            {files.length > 1 && (
              <div className="flex flex-wrap gap-1.5">
                {files.map((file) => (
                  <Button
                    key={file.path}
                    size="xs"
                    variant={file.path === current.path ? "secondary" : "outline"}
                    onClick={() => setPicked(file.path)}
                  >
                    {file.path}
                  </Button>
                ))}
              </div>
            )}
            <div className="min-h-0 flex-1 overflow-hidden rounded-md border" data-testid="diff-view">
              <DiffView path={current.path} before={current.before ?? ""} after={current.after ?? ""} />
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

function VersionRow({
  version,
  parent,
  isHead,
  isOriginal,
  disabled,
  onRestore,
  onCompare,
}: {
  version: VersionInfo;
  parent: VersionInfo | undefined;
  isHead: boolean;
  isOriginal: boolean;
  disabled: boolean;
  onRestore: () => void;
  onCompare: (request: DiffRequest) => void;
}) {
  return (
    <li
      className={cn("rounded-md border bg-card p-2.5 text-xs", isHead && "border-primary/60 bg-primary/5")}
      data-testid={`version-${version.number}`}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant="secondary" className="font-mono text-[11px]">
          v{version.number}
        </Badge>
        <span className="font-medium">{t.origin[version.origin]}</span>
        {isHead && <Badge className="text-[10px]">{t.current}</Badge>}
        {version.status !== "active" && (
          <Badge variant="outline" className="text-[10px]">
            {version.status === "rejected" ? t.rejected : t.candidate}
          </Badge>
        )}
        {version.gate_status !== "unknown" && (
          <Badge
            variant="outline"
            className={cn(
              "text-[10px]",
              version.gate_status === "passed" ? "text-node-return" : "text-severity-high",
            )}
          >
            {t.gate[version.gate_status]}
          </Badge>
        )}
        <span className="ml-auto text-muted-foreground">{formatTime(version.created_at)}</span>
      </div>
      {version.description && <p className="mt-1 text-muted-foreground">{version.description}</p>}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {parent && (
          <Button
            size="xs"
            variant="outline"
            onClick={() =>
              onCompare({ from: parent.id, to: version.id, title: `v${parent.number} → v${version.number}` })
            }
          >
            {t.compare}
          </Button>
        )}
        {!isOriginal && (
          <Button
            size="xs"
            variant="outline"
            onClick={() => onCompare({ from: 0, to: version.id, title: `${ru.header.original} → v${version.number}` })}
          >
            {t.compareOriginal}
          </Button>
        )}
        {!isHead && (
          <Button size="xs" variant="outline" disabled={disabled} onClick={onRestore}>
            <RotateCcw /> {t.restore}
          </Button>
        )}
      </div>
    </li>
  );
}

type Props = { projectId: number; open: boolean; onOpenChange: (open: boolean) => void };

/** История версий (docs/09 § 3.7): происхождение, статус проверки, сравнение и восстановление. */
export function HistorySheet({ projectId, open, onOpenChange }: Props) {
  const project = useQuery(projectQuery(projectId));
  const versions = useQuery({ ...versionsQuery(projectId), enabled: open });
  const { restore, working } = useEditing(projectId);
  const [diff, setDiff] = useState<DiffRequest | null>(null);

  const list = useMemo(() => [...(versions.data?.versions ?? [])].sort((a, b) => b.number - a.number), [versions.data]);
  const byId = useMemo(() => new Map(list.map((v) => [v.id, v])), [list]);
  const originalId = project.data?.original_version_id ?? 0;

  const compare = (request: DiffRequest) => setDiff({ ...request, from: request.from || originalId });

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent className="flex w-full flex-col gap-0 sm:max-w-md" aria-describedby={undefined}>
          <SheetHeader className="border-b">
            <SheetTitle className="flex items-center gap-2">
              <HistoryIcon className="size-4" aria-hidden /> {t.title}
            </SheetTitle>
            <SheetDescription>{t.description}</SheetDescription>
          </SheetHeader>
          <ScrollArea className="min-h-0 flex-1">
            <div className="p-4">
              {versions.isLoading && <Skeleton className="h-40" />}
              {versions.data && list.length === 0 && <p className="text-sm text-muted-foreground">{t.empty}</p>}
              <ul className="space-y-2">
                {list.map((version) => (
                  <VersionRow
                    key={version.id}
                    version={version}
                    parent={version.parent_id === null ? undefined : byId.get(version.parent_id)}
                    isHead={version.id === project.data?.head_version_id}
                    isOriginal={version.id === originalId}
                    disabled={working}
                    onRestore={() => void restore(version.id)}
                    onCompare={compare}
                  />
                ))}
              </ul>
            </div>
          </ScrollArea>
        </SheetContent>
      </Sheet>
      <DiffDialog projectId={projectId} request={diff} onClose={() => setDiff(null)} />
    </>
  );
}
