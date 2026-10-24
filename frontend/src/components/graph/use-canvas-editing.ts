"use client";

import { useCallback, useMemo, useState } from "react";

import type { GraphOp } from "@/lib/api/mutations";
import { BRANCH_FIELDS, WRAP_FIELDS } from "@/lib/edit/block-fields";
import { isInside, isMovable, isSamePlace, type InsertPoint } from "@/lib/edit/slots";
import { useEditing } from "@/lib/edit/use-editing";
import { useInsertBlock } from "@/lib/edit/use-insert";
import type { Block, FlowModel, NodeKind } from "@/lib/graph/flow-model";
import { ru } from "@/lib/i18n/ru";
import { useEditorStore } from "@/stores/editor-store";

export type Wrapper = "if" | "try" | "for" | "while" | "block";
export type Branch = "else" | "else_if" | "catch" | "finally" | "case" | "default";

/** Блоки, которые можно развернуть, оставив тело на месте. */
const UNWRAPPABLE: ReadonlySet<string> = new Set(["condition", "loop_for", "loop_foreach", "loop_while", "try"]);
/** Блок нельзя дублировать: получилось бы повторное объявление или лишняя ветка. */
const NOT_DUPLICABLE: ReadonlySet<string> = new Set(["var_decl", "method", "catch", "finally", "case", "local_type", "external"]);
const BRANCHES: Partial<Record<string, Branch[]>> = {
  condition: ["else", "else_if"],
  try: ["catch", "finally"],
  switch: ["case", "default"],
};

export type BlockCapabilities = {
  edit: boolean;
  duplicate: boolean;
  remove: boolean;
  wrap: boolean;
  unwrap: boolean;
  branches: Branch[];
};

/** Что можно делать с блоком из меню и клавиатуры. */
export function capabilitiesOf(model: FlowModel, block: Block): BlockCapabilities {
  const statement = isMovable(model, block.id);
  const clause = block.kind === "catch" || block.kind === "finally";
  return {
    edit: block.kind !== "method" && block.kind !== "external",
    duplicate: statement && !NOT_DUPLICABLE.has(block.kind),
    remove: (statement || clause) && block.kind !== "method",
    wrap: statement,
    unwrap: statement && UNWRAPPABLE.has(block.kind),
    branches: BRANCHES[block.kind] ?? [],
  };
}

/** Действия над схемой: каждое превращается в операцию `POST /ops` (docs/09 § 3.3). */
export function useCanvasEditing(projectId: number, model: FlowModel) {
  const { runOps } = useEditing(projectId);
  const { insertBlock } = useInsertBlock(projectId);
  const openInspector = useEditorStore((s) => s.openInspector);
  // блок с содержимым удаляется после вопроса: «с телом» или «оставить тело»
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const methodId = model.methodId;

  const edit = useCallback(
    (nodeId: string) => openInspector({ mode: "update", methodId, nodeId }),
    [openInspector, methodId],
  );

  const run = useCallback(
    (op: GraphOp, description: string) => runOps([op], description),
    [runOps],
  );

  /** Описание версии: «Удалить блок «Операция»». */
  const describe = useCallback(
    (verb: string, nodeId: string) => {
      const title = model.blocks.get(nodeId)?.title;
      return title ? `${verb} «${title}»` : verb;
    },
    [model],
  );

  const remove = useCallback(
    (nodeId: string, mode: "with_body" | "unwrap" = "with_body") =>
      run({ op: "delete_node", node_id: nodeId, mode }, describe(ru.edit.removed, nodeId)),
    [run, describe],
  );

  const requestDelete = useCallback(
    (nodeId: string) => {
      const block = model.blocks.get(nodeId);
      if (!block) return;
      if (block.children.length > 0) setPendingDelete(nodeId);
      else void remove(nodeId);
    },
    [model, remove],
  );

  const duplicate = useCallback(
    (nodeId: string) => run({ op: "duplicate_node", node_id: nodeId }, describe(ru.edit.duplicated, nodeId)),
    [run, describe],
  );

  const unwrap = useCallback(
    (nodeId: string) => run({ op: "unwrap_node", node_id: nodeId }, describe(ru.edit.menu.unwrap, nodeId)),
    [run, describe],
  );

  const wrap = useCallback(
    (nodeId: string, wrapper: Wrapper) => {
      const label = ru.edit.menu.wrappers[wrapper];
      const op = (fields: Record<string, string | boolean> = {}): GraphOp => ({
        op: "wrap_node",
        node_ids: [nodeId],
        wrapper,
        fields,
      });
      if (wrapper === "block" || wrapper === "try") {
        void run(op(), describe(`${ru.edit.menu.wrap} ${label}:`, nodeId));
        return;
      }
      const block = model.blocks.get(nodeId);
      openInspector({
        mode: "action",
        methodId,
        key: `wrap:${wrapper}:${nodeId}`,
        kind: wrapper === "if" ? "condition" : wrapper === "while" ? "loop_while" : "loop_for",
        title: `${ru.edit.menu.wrap} ${label}`,
        fields: WRAP_FIELDS[wrapper],
        contextId: block?.id ?? nodeId,
        description: `${ru.edit.menu.wrap} ${label}`,
        makeOp: op,
      });
    },
    [model, methodId, openInspector, run, describe],
  );

  const addBranch = useCallback(
    (nodeId: string, branch: Branch) => {
      const op = (fields: Record<string, string | boolean> = {}): GraphOp => ({
        op: "add_branch",
        node_id: nodeId,
        branch,
        fields,
      });
      const label = ru.edit.menu.branches[branch];
      if (branch === "else" || branch === "finally" || branch === "default") {
        void run(op(), describe(`${ru.edit.menu.branch} ${label} к`, nodeId));
        return;
      }
      const block = model.blocks.get(nodeId);
      openInspector({
        mode: "action",
        methodId,
        key: `branch:${branch}:${nodeId}`,
        kind: block?.kind === "try" ? "catch" : "condition",
        title: `${ru.edit.menu.branch}: ${label.replace("…", "")}`,
        fields: BRANCH_FIELDS[branch],
        contextId: nodeId,
        description: `${ru.edit.menu.branch}: ${label}`,
        makeOp: op,
      });
    },
    [model, methodId, openInspector, run, describe],
  );

  /** Блок схемы брошен на новое место. */
  const move = useCallback(
    (nodeId: string, point: InsertPoint) => {
      if (isInside(model, point.parentId, nodeId) || isSamePlace(model, nodeId, point)) return;
      void run(
        { op: "move_node", node_id: nodeId, new_parent_id: point.parentId, slot: point.slot, index: point.index },
        describe(ru.edit.moved, nodeId),
      );
    },
    [model, run, describe],
  );

  /** Блок из палитры брошен на схему. */
  const insert = useCallback(
    (kind: NodeKind, point: InsertPoint) => insertBlock(kind, { methodId, point }),
    [insertBlock, methodId],
  );

  return useMemo(
    () => ({
      edit,
      remove,
      requestDelete,
      duplicate,
      unwrap,
      wrap,
      addBranch,
      move,
      insert,
      pendingDelete,
      cancelDelete: () => setPendingDelete(null),
    }),
    [edit, remove, requestDelete, duplicate, unwrap, wrap, addBranch, move, insert, pendingDelete],
  );
}
