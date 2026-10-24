"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef } from "react";

import type { EditorMarker } from "@/components/code/code-editor";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api/client";
import { parseLive } from "@/lib/api/mutations";
import { fileQuery } from "@/lib/api/queries";
import { flushDrafts, flushDraftsOnExit } from "@/lib/edit/session";
import { findCachedBlock, locateInCache } from "@/lib/graph/flow-cache";
import type { SourceRange } from "@/lib/graph/flow-model";
import { useDebouncedCallback } from "@/lib/hooks/use-debounced-callback";
import { ru } from "@/lib/i18n/ru";
import { useEditorStore } from "@/stores/editor-store";
import { useRunStore } from "@/stores/run-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

const t = ru.code;

/** Пауза после ввода до живого разбора и до сохранения (docs/09 § 4). */
const PARSE_DELAY_MS = 400;
const SAVE_DELAY_MS = 800;

// Monaco тяжёлый и не работает на сервере: грузим только в браузере (docs/09 § 8).
const CodeEditor = dynamic(() => import("@/components/code/code-editor"), {
  ssr: false,
  loading: () => <Skeleton className="m-3 h-32" />,
});

type Props = { projectId: number; versionId?: number };

export function CodePanel({ projectId, versionId }: Props) {
  const queryClient = useQueryClient();
  const activeFile = useWorkspaceStore((s) => s.activeFile);
  const selection = useWorkspaceStore((s) => s.selection);
  const openMethod = useWorkspaceStore((s) => s.openMethod);
  const selectNode = useWorkspaceStore((s) => s.selectNode);
  const lastRun = useRunStore((s) => {
    const id = s.order[s.order.length - 1];
    return id === undefined ? undefined : s.entries[id];
  });
  const file = useQuery(fileQuery(projectId, activeFile, versionId));
  const draft = useEditorStore((s) => (activeFile ? s.drafts[activeFile] : undefined));
  const liveIssues = useEditorStore((s) => (activeFile ? s.issues[activeFile] : undefined));
  const locked = useEditorStore((s) => s.busy > 0);

  // Метод открытой вкладки: его схема обновляется по мере набора кода.
  const methodId = useWorkspaceStore((s) => {
    const tab = s.tabs.find((candidate) => candidate.id === s.activeTabId);
    return tab?.kind === "method" ? tab.methodId : null;
  });

  // Диапазон выбранного узла: из выбора, а если его нет (выбор из кода/URL) — из загруженных схем.
  const highlight = useMemo<SourceRange | null>(() => {
    if (!selection || !activeFile) return null;
    const range =
      selection.range ?? findCachedBlock(queryClient, projectId, selection.nodeId, versionId)?.range ?? null;
    return range && range.file === activeFile ? range : null;
  }, [selection, activeFile, queryClient, projectId, versionId]);

  const markers = useMemo<EditorMarker[]>(() => {
    const issues = liveIssues ?? file.data?.syntax_errors ?? [];
    const result: EditorMarker[] = issues.map((issue) => ({
      severity: "error",
      message: issue.message,
      range: issue.range,
    }));
    for (const d of lastRun?.state.diagnostics ?? []) {
      if (d.file !== activeFile || d.line <= 0) continue;
      result.push({
        severity: d.severity === "error" ? "error" : d.severity === "warning" ? "warning" : "info",
        message: d.message,
        range: {
          file: d.file,
          start_line: d.line,
          start_col: d.column,
          end_line: d.end_line ?? d.line,
          end_col: d.end_column ?? d.column + 1,
        },
      });
    }
    return result;
  }, [file.data, liveIssues, lastRun?.state.diagnostics, activeFile]);

  // Курсор → узел графа (docs/09 § 4): сначала по уже загруженным схемам, затем у backend.
  const onCursor = useDebouncedCallback(async (line: number, column: number) => {
    if (!activeFile) return;
    const hit = locateInCache(queryClient, projectId, activeFile, line, column, versionId);
    if (hit) {
      openMethod(hit.methodId);
      selectNode(hit.nodeId, "code", findCachedBlock(queryClient, projectId, hit.nodeId, versionId)?.range ?? null);
      return;
    }
    const { data } = await api.GET("/api/projects/{project_id}/locate", {
      params: { path: { project_id: projectId }, query: { file: activeFile, line, column } },
    });
    if (data?.node_id && data.method_id) {
      openMethod(data.method_id);
      selectNode(data.node_id, "code", null);
    }
  }, 150);

  // Ввод: живой разбор через 400 мс и сохранение через 800 мс после паузы.
  const parseController = useRef<AbortController | null>(null);
  const runParse = useDebouncedCallback(async (path: string, text: string) => {
    parseController.current?.abort();
    const controller = new AbortController();
    parseController.current = controller;
    try {
      const result = await parseLive(projectId, path, text, methodId, controller.signal);
      useEditorStore.getState().applyParse(path, methodId, text, result.syntax_errors, result.flow);
    } catch {
      // запрос отменён новым вводом или backend недоступен: схема остаётся прежней
    }
  }, PARSE_DELAY_MS);
  const runSave = useDebouncedCallback(() => {
    void flushDrafts(queryClient, projectId).catch(() => undefined);
  }, SAVE_DELAY_MS);

  const onChange = useCallback(
    (text: string) => {
      if (!activeFile) return;
      useEditorStore.getState().setDraft(activeFile, text);
      runParse(activeFile, text);
      runSave();
    },
    [activeFile, runParse, runSave],
  );
  const onSave = useCallback(() => {
    void flushDrafts(queryClient, projectId).catch(() => undefined);
  }, [queryClient, projectId]);

  // Рабочая область закрывается: набранное, но не сохранённое уходит на сервер.
  useEffect(
    () => () => {
      parseController.current?.abort();
      flushDraftsOnExit(queryClient, projectId);
    },
    [queryClient, projectId],
  );

  return (
    <section className="flex h-full min-h-0 flex-col" aria-label={t.title}>
      <div className="flex items-center gap-2 border-b px-3 py-1.5">
        <h2 className="text-sm font-semibold">{t.title}</h2>
        {activeFile && <code className="truncate text-xs text-muted-foreground">{activeFile}</code>}
        {locked ? (
          <Badge variant="outline" className="ml-auto text-[10px]" title={t.readOnlyHint}>
            {t.readOnly}
          </Badge>
        ) : (
          <span className="ml-auto hidden text-[10px] text-muted-foreground lg:inline">{t.autosave}</span>
        )}
      </div>
      <div className="relative min-h-0 flex-1" data-testid="code-panel">
        {!activeFile && <p className="p-4 text-sm text-muted-foreground">{t.noFile}</p>}
        {activeFile && file.isLoading && <Skeleton className="m-3 h-32" />}
        {file.error && (
          <p className="p-4 text-sm text-destructive">
            {t.loadFailed}: {file.error.message}
          </p>
        )}
        {file.data && (
          <CodeEditor
            path={file.data.path}
            value={draft ?? file.data.content}
            readOnly={locked}
            highlight={highlight}
            revealSeq={selection?.source === "code" ? null : (selection?.seq ?? 0)}
            markers={markers}
            onCursor={onCursor}
            onChange={onChange}
            onSave={onSave}
          />
        )}
      </div>
    </section>
  );
}
