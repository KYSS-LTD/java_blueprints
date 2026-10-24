import { api } from "./client";
import { unwrap } from "./queries";
import type { components } from "./schema";

type Schemas = components["schemas"];

export type GraphOp = Schemas["OpsRequest"]["ops"][number];
export type OpsResult = Schemas["OpsResult"];
export type OpsValidation = Schemas["OpsValidation"];
export type VersionChange = Schemas["VersionChange"];
export type ParseResult = Schemas["ParseResult"];
export type VersionInfo = Schemas["VersionInfo"];
export type FragmentKind = Schemas["ExpressionCheck"]["kind"];

/** Пакет операций над графом: атомарно, с проверкой `base_version_id` (docs/08 § 5). */
export async function postOps(
  projectId: number,
  baseVersionId: number,
  ops: GraphOp[],
  description?: string,
): Promise<OpsResult> {
  return unwrap(
    await api.POST("/api/projects/{project_id}/ops", {
      params: { path: { project_id: projectId } },
      body: { base_version_id: baseVersionId, ops, description },
    }),
  );
}

/** Сухая проверка пакета: ничего не сохраняется, в ответе — diff. */
export async function validateOps(
  projectId: number,
  baseVersionId: number,
  ops: GraphOp[],
  signal?: AbortSignal,
): Promise<OpsValidation> {
  return unwrap(
    await api.POST("/api/projects/{project_id}/ops/validate", {
      params: { path: { project_id: projectId } },
      body: { base_version_id: baseVersionId, ops },
      signal,
    }),
  );
}

export async function undoVersion(projectId: number): Promise<VersionChange> {
  return unwrap(await api.POST("/api/projects/{project_id}/undo", { params: { path: { project_id: projectId } } }));
}

export async function redoVersion(projectId: number): Promise<VersionChange> {
  return unwrap(await api.POST("/api/projects/{project_id}/redo", { params: { path: { project_id: projectId } } }));
}

export async function restoreVersion(projectId: number, versionId: number): Promise<VersionChange> {
  return unwrap(
    await api.POST("/api/projects/{project_id}/versions/{version_id}/restore", {
      params: { path: { project_id: projectId, version_id: versionId } },
    }),
  );
}

/** Сохранение файла: подряд идущие правки кода схлопываются в HEAD, поэтому номер версии может не меняться. */
export async function saveFile(
  projectId: number,
  path: string,
  baseVersionId: number,
  content: string,
): Promise<VersionChange> {
  return unwrap(
    await api.PUT("/api/projects/{project_id}/files/{path}", {
      params: { path: { project_id: projectId, path } },
      body: { base_version_id: baseVersionId, content },
    }),
  );
}

/** Живой разбор текста без сохранения: ошибки синтаксиса и схема метода. */
export async function parseLive(
  projectId: number,
  file: string,
  content: string,
  methodKey: string | null,
  signal?: AbortSignal,
): Promise<ParseResult> {
  return unwrap(
    await api.POST("/api/projects/{project_id}/parse", {
      params: { path: { project_id: projectId } },
      body: { file, content, method_key: methodKey },
      signal,
    }),
  );
}

/** Проверка фрагмента формы: выражение, тип или имя. */
export async function checkFragment(
  projectId: number,
  kind: FragmentKind,
  text: string,
  contextNodeId: string | null,
  signal?: AbortSignal,
): Promise<{ ok: boolean; message: string | null }> {
  const result = unwrap(
    await api.POST("/api/projects/{project_id}/validate/expression", {
      params: { path: { project_id: projectId } },
      body: { kind, text, context_node_id: contextNodeId },
      signal,
    }),
  );
  return { ok: result.ok, message: result.error?.message ?? null };
}
