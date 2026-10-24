import type { QueryClient } from "@tanstack/react-query";

import { fileQuery, flowQuery } from "@/lib/api/queries";
import { modelOf } from "@/lib/graph/flow-cache";
import type { FlowGraph, FlowModel } from "@/lib/graph/flow-model";
import { useEditorStore } from "@/stores/editor-store";

/** Схема метода, которую сейчас видит пользователь: набранная в коде, а если её нет — загруженная. */
export function shownFlow(
  queryClient: QueryClient,
  projectId: number,
  methodId: string,
  versionId?: number,
): FlowGraph | undefined {
  return (
    useEditorStore.getState().liveFlows[methodId] ??
    queryClient.getQueryData(flowQuery(projectId, methodId, versionId).queryKey)
  );
}

export function shownModel(
  queryClient: QueryClient,
  projectId: number,
  methodId: string,
  versionId?: number,
): FlowModel | undefined {
  const flow = shownFlow(queryClient, projectId, methodId, versionId);
  return flow ? modelOf(flow) : undefined;
}

/** В файле есть синтаксические ошибки: граф по нему недостоверен, его правка заблокирована. */
export function hasSyntaxErrors(queryClient: QueryClient, projectId: number, file: string, versionId?: number): boolean {
  const live = useEditorStore.getState().issues[file];
  if (live) return live.length > 0;
  const saved = queryClient.getQueryData(fileQuery(projectId, file, versionId).queryKey);
  return (saved?.syntax_errors.length ?? 0) > 0;
}
