import type { QueryClient } from "@tanstack/react-query";

import { buildFlowModel, type Block, type FlowGraph, type FlowModel } from "./flow-model";
import { locateBlock } from "./locate";

// Модель строится из одного и того же объекта FlowGraph (кеш TanStack Query) много раз: холст, код, дерево.
const models = new WeakMap<FlowGraph, FlowModel>();

export function modelOf(flow: FlowGraph): FlowModel {
  let model = models.get(flow);
  if (!model) {
    model = buildFlowModel(flow);
    models.set(flow, model);
  }
  return model;
}

/** Загруженные блок-схемы проекта из кеша запросов (ключ `["flow", projectId, versionId, methodId]`). */
export function cachedFlows(queryClient: QueryClient, projectId: number): FlowGraph[] {
  return queryClient
    .getQueriesData<FlowGraph>({ queryKey: ["flow", projectId] })
    .flatMap(([, flow]) => (flow ? [flow] : []));
}

/** Блок по id среди загруженных схем (нужен, чтобы подсветить в коде узел, выбранный без диапазона). */
export function findCachedBlock(queryClient: QueryClient, projectId: number, nodeId: string): Block | null {
  for (const flow of cachedFlows(queryClient, projectId)) {
    const block = modelOf(flow).blocks.get(nodeId);
    if (block) return block;
  }
  return null;
}

export type LocatedBlock = { methodId: string; nodeId: string };

/**
 * Узел под курсором по уже загруженным схемам. Побеждает самый глубокий блок; блок «Метод»
 * уступает вложенным. Если подходящей схемы нет, вернёт `null`, и тогда спрашивают backend.
 */
export function locateInCache(
  queryClient: QueryClient,
  projectId: number,
  file: string,
  line: number,
  column: number,
): LocatedBlock | null {
  let best: { located: LocatedBlock; depth: number } | null = null;
  for (const flow of cachedFlows(queryClient, projectId)) {
    if (flow.file !== file) continue;
    const model = modelOf(flow);
    const id = locateBlock(model, line, column);
    if (id === null) continue;
    const depth = model.blocks.get(id)?.depth ?? 0;
    if (best === null || depth > best.depth) best = { located: { methodId: flow.method_id, nodeId: id }, depth };
  }
  return best?.located ?? null;
}
