"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { toast } from "sonner";

import { paletteQuery, projectQuery } from "@/lib/api/queries";
import { fieldsForKind, needsForm } from "@/lib/edit/block-fields";
import { hasSyntaxErrors, shownModel } from "@/lib/edit/flows";
import { clickPoint, type InsertPoint } from "@/lib/edit/slots";
import { useEditing } from "@/lib/edit/use-editing";
import type { NodeKind } from "@/lib/graph/flow-model";
import { ru } from "@/lib/i18n/ru";
import { useEditorStore } from "@/stores/editor-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export type InsertTarget = { methodId: string; point: InsertPoint };

/** Метод открытой вкладки схемы или `null`, если открыта структура или ничего. */
export function activeMethodId(): string | null {
  const { tabs, activeTabId } = useWorkspaceStore.getState();
  const tab = tabs.find((candidate) => candidate.id === activeTabId);
  return tab?.kind === "method" ? tab.methodId : null;
}

/**
 * Вставка блока из палитры: по клику (после выбранного блока) или по перетаскиванию (в `target`).
 * Блоку с полями открывается инспектор, `break`/`continue` вставляются сразу.
 */
export function useInsertBlock(projectId: number) {
  const queryClient = useQueryClient();
  const project = useQuery(projectQuery(projectId));
  const palette = useQuery(paletteQuery(projectId));
  const { runOps } = useEditing(projectId);
  const versionId = project.data?.head_version_id;

  const insertBlock = useCallback(
    async (kind: NodeKind, target?: InsertTarget): Promise<void> => {
      const methodId = target?.methodId ?? activeMethodId();
      if (!methodId) {
        toast.info(ru.palette.noMethod);
        return;
      }
      const model = shownModel(queryClient, projectId, methodId, versionId);
      if (!model) return;
      if (hasSyntaxErrors(queryClient, projectId, model.file, versionId)) {
        toast.warning(ru.palette.blocked);
        return;
      }
      const point = target?.point ?? clickPoint(model, useWorkspaceStore.getState().selection?.nodeId ?? null);
      const fields = fieldsForKind(kind, palette.data?.blocks, "insert");
      if (needsForm(kind, fields)) {
        useEditorStore.getState().openInspector({ mode: "insert", methodId, kind, point });
        return;
      }
      await runOps(
        [{ op: "insert_node", parent_id: point.parentId, slot: point.slot, index: point.index, kind, fields: {} }],
        `Вставить блок «${palette.data?.blocks.find((b) => b.kind === kind)?.title ?? kind}»`,
      );
    },
    [queryClient, projectId, versionId, palette.data, runOps],
  );

  return { insertBlock, blocks: palette.data?.blocks, loading: palette.isLoading };
}
