"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";
import { toast } from "sonner";

import type { GraphOp, OpsResult } from "@/lib/api/mutations";
import { projectQuery, versionsQuery } from "@/lib/api/queries";
import { type HeadMove, applyOps, moveHead } from "@/lib/edit/session";
import { modelOf } from "@/lib/graph/flow-cache";
import { ru } from "@/lib/i18n/ru";
import { useEditorStore } from "@/stores/editor-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

const t = ru.edit;

type RunOptions = {
  /** Ошибку разбирает вызывающий (форма показывает её у поля); тост не показывается. */
  onError?: (error: Error) => void;
};

/** После операции выбор переезжает по `id_map`, а фокус идёт на узел, который изменила операция. */
function followResult(result: OpsResult): void {
  const workspace = useWorkspaceStore.getState();
  const model = result.flow ? modelOf(result.flow) : null;
  const rangeOf = (id: string) => model?.blocks.get(id)?.range ?? null;
  if (result.focus_node_id) {
    workspace.selectNode(result.focus_node_id, "edit", rangeOf(result.focus_node_id));
    return;
  }
  const selected = workspace.selection?.nodeId;
  const moved = selected ? result.id_map[selected] : undefined;
  if (moved) workspace.selectNode(moved, "edit", rangeOf(moved));
}

/** Правка проекта с интерфейса: операции над графом, отмена и повтор, история версий. */
export function useEditing(projectId: number) {
  const queryClient = useQueryClient();
  const project = useQuery(projectQuery(projectId));
  const versions = useQuery(versionsQuery(projectId));
  const working = useEditorStore((s) => s.busy > 0);

  const head = versions.data?.versions.find((v) => v.id === project.data?.head_version_id);
  const canUndo = head !== undefined && head.parent_id !== null;
  const canRedo =
    head !== undefined && (versions.data?.versions.some((v) => v.parent_id === head.id && v.status === "active") ?? false);

  const runOps = useCallback(
    async (ops: GraphOp[], description?: string, options: RunOptions = {}): Promise<OpsResult | null> => {
      try {
        const result = await applyOps(queryClient, projectId, ops, description);
        followResult(result);
        return result;
      } catch (error) {
        const failure = error instanceof Error ? error : new Error(String(error));
        if (options.onError) options.onError(failure);
        else toast.error(t.failed, { description: failure.message });
        return null;
      }
    },
    [queryClient, projectId],
  );

  const move = useCallback(
    async (target: HeadMove): Promise<boolean> => {
      try {
        await moveHead(queryClient, projectId, target);
        return true;
      } catch (error) {
        toast.error(t.failed, { description: error instanceof Error ? error.message : String(error) });
        return false;
      }
    },
    [queryClient, projectId],
  );

  const undo = useCallback(async () => {
    if (await move("undo")) toast(t.undone);
  }, [move]);
  const redo = useCallback(async () => {
    if (await move("redo")) toast(t.redone);
  }, [move]);
  const restore = useCallback(
    async (versionId: number) => {
      if (await move({ restore: versionId })) toast.success(t.restored);
    },
    [move],
  );

  return useMemo(
    () => ({ head, canUndo, canRedo, working, runOps, undo, redo, restore }),
    [head, canUndo, canRedo, working, runOps, undo, redo, restore],
  );
}
