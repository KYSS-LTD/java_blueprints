"use client";

import { Loader2, Square, Trash2 } from "lucide-react";
import { useEffect, useRef } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import { ru } from "@/lib/i18n/ru";
import { type ConsoleLine, type Diagnostic, isRunning, type RunState } from "@/lib/run/run-state";
import { cn } from "@/lib/utils";
import { type RunEntry, useRunStore } from "@/stores/run-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

const t = ru.console;

const STREAM_CLASS: Record<ConsoleLine["stream"], string> = {
  stdout: "text-foreground",
  stderr: "text-severity-high",
  build: "text-node-variable",
  info: "text-muted-foreground italic",
};

function numberOf(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function exitInfo(state: RunState): { exitCode: number | null; durationMs: number | null } {
  const data = state.result?.data;
  return { exitCode: numberOf(data?.exit_code), durationMs: numberOf(data?.duration_ms) };
}

function DiagnosticList({ diagnostics }: { diagnostics: Diagnostic[] }) {
  const setActiveFile = useWorkspaceStore((s) => s.setActiveFile);
  const selectNode = useWorkspaceStore((s) => s.selectNode);
  if (diagnostics.length === 0) return null;
  return (
    <ul className="mt-1 space-y-0.5" aria-label={t.diagnostics}>
      {diagnostics.map((d, index) => (
        <li key={`${d.file}:${d.line}:${index}`}>
          <button
            type="button"
            disabled={d.line <= 0}
            className="w-full rounded px-1 text-left text-severity-high hover:bg-accent disabled:cursor-default disabled:hover:bg-transparent"
            onClick={() => {
              setActiveFile(d.file);
              selectNode(d.node_id ?? `diagnostic:${d.file}:${d.line}`, "findings", {
                file: d.file,
                start_line: d.line,
                start_col: d.column,
                end_line: d.end_line ?? d.line,
                end_col: d.end_column ?? d.column + 1,
              });
            }}
          >
            {d.file ? `${d.file}:${d.line}:${d.column} ` : ""}
            {d.message}
          </button>
        </li>
      ))}
    </ul>
  );
}

function RunBlock({ entry }: { entry: RunEntry }) {
  const { state } = entry;
  const running = isRunning(state);
  const { exitCode, durationMs } = exitInfo(state);
  const failed = state.status === "failed" || state.status === "error" || state.error !== null;

  const cancel = () => void api.POST("/api/runs/{run_id}/cancel", { params: { path: { run_id: entry.id } } });

  return (
    <article className="border-b border-border/60 px-3 py-2 last:border-b-0" aria-label={entry.label}>
      <header className="mb-1 flex items-center gap-2 text-[11px] text-muted-foreground">
        <span className="font-semibold text-foreground">▶ {entry.label}</span>
        {running && (
          <>
            <Loader2 className="size-3 animate-spin" aria-hidden />
            <span>{state.status === "queued" ? t.queued : t.running}</span>
            <Button variant="ghost" size="xs" className="ml-auto h-5" onClick={cancel}>
              <Square /> {t.cancel}
            </Button>
          </>
        )}
        {!running && state.status === "cancelled" && <Badge variant="outline">{t.cancelled}</Badge>}
      </header>

      {(entry.stdin || entry.args.length > 0) && (
        <details className="mb-1 text-[11px] text-muted-foreground">
          <summary className="cursor-pointer select-none">
            {entry.stdin ? `${t.stdin}: ${entry.stdin.split("\n").length} стр.` : ""}
            {entry.args.length > 0 ? ` ${t.args}: ${entry.args.join(" ")}` : ""}
          </summary>
          {entry.stdin && <pre className="mt-1 whitespace-pre-wrap">{entry.stdin}</pre>}
        </details>
      )}

      <pre className="font-mono text-xs leading-5 break-words whitespace-pre-wrap" data-testid="console-output">
        {state.lines.map((line, index) => (
          <span key={index} className={cn("block", STREAM_CLASS[line.stream])} data-stream={line.stream}>
            {line.text.endsWith("\n") ? line.text.slice(0, -1) : line.text}
          </span>
        ))}
      </pre>

      <DiagnosticList diagnostics={state.diagnostics} />

      {state.error && (
        <p className="mt-1 text-xs text-severity-high" role="alert">
          {state.error.message}
        </p>
      )}

      {state.done && !failed && (exitCode !== null || durationMs !== null) && (
        <footer className="mt-1 flex items-center gap-3 text-[11px] text-muted-foreground">
          {exitCode !== null && (
            <span>
              {t.exitCode}: <b className={exitCode === 0 ? "text-node-return" : "text-severity-high"}>{exitCode}</b>
            </span>
          )}
          {durationMs !== null && (
            <span>
              {t.duration}: {durationMs} мс
            </span>
          )}
        </footer>
      )}
    </article>
  );
}

export function ConsolePanel() {
  const order = useRunStore((s) => s.order);
  const entries = useRunStore((s) => s.entries);
  const clear = useRunStore((s) => s.clear);
  const scroller = useRef<HTMLDivElement>(null);
  const last = entries[order[order.length - 1]];
  const size = last ? last.state.lines.length + order.length : 0;

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [size]);

  return (
    <section className="flex h-full min-h-0 flex-col" aria-label={t.title}>
      <div className="flex items-center gap-2 border-b px-3 py-1.5">
        <h2 className="text-sm font-semibold">{t.title}</h2>
        <Button
          variant="ghost"
          size="icon-xs"
          className="ml-auto"
          aria-label={t.clear}
          title={t.clear}
          disabled={order.length === 0}
          onClick={clear}
        >
          <Trash2 />
        </Button>
      </div>
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto">
        {order.length === 0 ? (
          <div className="p-4 text-sm text-muted-foreground">
            <p>{t.empty}</p>
            <p className="text-xs">{t.emptyHint}</p>
          </div>
        ) : (
          order.map((id) => <RunBlock key={id} entry={entries[id]} />)
        )}
      </div>
    </section>
  );
}
