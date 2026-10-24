import { hashKey, type QueryClient, type QueryKey } from "@tanstack/react-query";

import {
  type GraphOp,
  type OpsResult,
  postOps,
  redoVersion,
  restoreVersion,
  saveFile,
  undoVersion,
  type VersionChange,
} from "@/lib/api/mutations";
import { ApiError, fileQuery, flowQuery, projectQuery } from "@/lib/api/queries";
import type { FlowGraph } from "@/lib/graph/flow-model";
import { useEditorStore } from "@/stores/editor-store";

/**
 * Все изменения проекта (операции, сохранение кода, отмена) идут по очереди: каждое читает
 * актуальный HEAD в момент запуска, поэтому `base_version_id` не устаревает от собственных запросов.
 */
const queues = new Map<number, Promise<unknown>>();

export function runExclusive<T>(projectId: number, task: () => Promise<T>): Promise<T> {
  const previous = queues.get(projectId) ?? Promise.resolve();
  const next = previous.catch(() => undefined).then(task);
  queues.set(projectId, next);
  return next;
}

export function headOf(queryClient: QueryClient, projectId: number): number {
  const project = queryClient.getQueryData(projectQuery(projectId).queryKey);
  if (!project) throw new ApiError("Проект ещё не загружен");
  return project.head_version_id;
}

/** Данные, зависящие от содержимого версии: после смены HEAD или правки они перечитываются. */
const VERSIONED = ["tree", "file", "flow", "structure"] as const;

/**
 * HEAD проекта сменился (или изменилось его содержимое: правки кода схлопываются в ту же версию).
 * Новая версия подставляется в кеш сразу, чтобы следующая операция не получила `409`; данные,
 * которые мы только что положили в кеш сами (`keep`), не перечитываются.
 */
export function adoptHead(
  queryClient: QueryClient,
  projectId: number,
  headVersionId: number,
  keep: QueryKey[] = [],
): void {
  queryClient.setQueryData(projectQuery(projectId).queryKey, (old) =>
    old ? { ...old, head_version_id: headVersionId } : old,
  );
  const skipped = new Set(keep.map((key) => hashKey(key)));
  for (const prefix of VERSIONED) {
    void queryClient.invalidateQueries({
      queryKey: [prefix, projectId],
      predicate: (query) => !skipped.has(hashKey(query.queryKey)),
    });
  }
  void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
  void queryClient.invalidateQueries({ queryKey: ["versions", projectId] });
  void queryClient.invalidateQueries({ queryKey: ["analysis", projectId] });
}

/** `409`: кто-то уже сменил HEAD. Обновляем проект, чтобы следующая попытка шла от актуальной версии. */
function noteConflict(queryClient: QueryClient, projectId: number, error: unknown): void {
  if (error instanceof ApiError && error.code === "VERSION_CONFLICT") {
    void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["versions", projectId] });
  }
}

// --- сохранение кода ---------------------------------------------------------------------------------

async function saveDraft(queryClient: QueryClient, projectId: number, file: string): Promise<void> {
  await runExclusive(projectId, async () => {
    const editor = useEditorStore.getState();
    const text = editor.drafts[file];
    if (text === undefined) return;
    editor.setSaveState("saving");
    try {
      const previousHead = headOf(queryClient, projectId);
      const change = await saveFile(projectId, file, previousHead, text);
      const head = change.head_version_id;
      const keep: QueryKey[] = [];

      // сохранённый текст нам известен, поэтому файл не перечитываем
      const fileKey = fileQuery(projectId, file, head).queryKey;
      queryClient.setQueryData(fileKey, {
        path: file,
        version_id: head,
        content: text,
        syntax_errors: change.syntax_errors,
      });
      keep.push(fileKey);

      // схему, разобранную из этого же текста, кладём как схему новой версии: холст не мигает
      const live = useEditorStore.getState();
      if (change.syntax_errors.length === 0) {
        for (const [methodId, flow] of Object.entries(live.liveFlows)) {
          if (flow.file !== file || live.parsedText[methodId] !== text) continue;
          const flowKey = flowQuery(projectId, methodId, head).queryKey;
          queryClient.setQueryData(flowKey, { ...flow, version_id: head });
          keep.push(flowKey);
        }
      }
      // Код с синтаксической ошибкой: backend строит схему по обломкам разбора. Холст остаётся на последней
      // корректной схеме (docs/09 § 4), поэтому её копия становится схемой новой версии.
      if (change.syntax_errors.length > 0) {
        for (const [, flow] of queryClient.getQueriesData<FlowGraph>({ queryKey: ["flow", projectId] })) {
          if (!flow || flow.file !== file || flow.version_id !== previousHead) continue;
          const flowKey = flowQuery(projectId, flow.method_id, head).queryKey;
          queryClient.setQueryData(flowKey, { ...flow, version_id: head });
          keep.push(flowKey);
        }
      }
      adoptHead(queryClient, projectId, head, keep);
      useEditorStore.getState().finishSave(file, text, change.syntax_errors);
    } catch (error) {
      noteConflict(queryClient, projectId, error);
      useEditorStore.getState().setSaveState("error", error instanceof Error ? error.message : String(error));
      throw error;
    }
  });
}

/** Сохраняет все несохранённые файлы; нужно перед операцией над графом, отменой и запуском. */
export async function flushDrafts(queryClient: QueryClient, projectId: number): Promise<void> {
  for (const file of Object.keys(useEditorStore.getState().drafts)) {
    await saveDraft(queryClient, projectId, file);
  }
}

// --- операции над графом и версии --------------------------------------------------------------------

/** Пакет операций: сначала сохраняется набранный код, затем операция применяется к актуальному HEAD. */
export async function applyOps(
  queryClient: QueryClient,
  projectId: number,
  ops: GraphOp[],
  description?: string,
): Promise<OpsResult> {
  const editor = useEditorStore.getState();
  editor.beginBusy(); // пока идёт операция, код в редакторе не правится: набранное иначе потерялось бы
  try {
    await flushDrafts(queryClient, projectId);
    return await runExclusive(projectId, async () => {
      try {
        const result = await postOps(projectId, headOf(queryClient, projectId), ops, description);
        const keep: QueryKey[] = [];
        if (result.flow) {
          const flowKey = flowQuery(projectId, result.flow.method_id, result.version.id).queryKey;
          queryClient.setQueryData(flowKey, result.flow);
          keep.push(flowKey);
        }
        useEditorStore.getState().dropDrafts();
        adoptHead(queryClient, projectId, result.version.id, keep);
        return result;
      } catch (error) {
        noteConflict(queryClient, projectId, error);
        throw error;
      }
    });
  } finally {
    editor.endBusy();
  }
}

export type HeadMove = "undo" | "redo" | { restore: number };

/** Отмена, повтор и восстановление версии: HEAD переходит на другую версию. */
export async function moveHead(
  queryClient: QueryClient,
  projectId: number,
  move: HeadMove,
): Promise<VersionChange> {
  const editor = useEditorStore.getState();
  editor.beginBusy();
  try {
    await flushDrafts(queryClient, projectId);
    return await runExclusive(projectId, async () => {
      try {
        const change =
          move === "undo"
            ? await undoVersion(projectId)
            : move === "redo"
              ? await redoVersion(projectId)
              : await restoreVersion(projectId, move.restore);
        useEditorStore.getState().dropDrafts();
        adoptHead(queryClient, projectId, change.head_version_id);
        return change;
      } catch (error) {
        noteConflict(queryClient, projectId, error);
        throw error;
      }
    });
  } finally {
    editor.endBusy();
  }
}

/**
 * Страница закрывается или рабочая область размонтируется: уходящий текст отправляется напрямую,
 * минуя очередь и хранилище (оно к этому моменту уже может быть сброшено).
 */
export function flushDraftsOnExit(queryClient: QueryClient, projectId: number): void {
  const drafts = Object.entries(useEditorStore.getState().drafts);
  if (drafts.length === 0) return;
  let base: number;
  try {
    base = headOf(queryClient, projectId);
  } catch {
    return;
  }
  void (async () => {
    for (const [file, text] of drafts) {
      try {
        base = (await saveFile(projectId, file, base, text)).head_version_id;
      } catch {
        return;
      }
    }
  })();
}
