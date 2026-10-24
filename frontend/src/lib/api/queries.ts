import { queryOptions } from "@tanstack/react-query";

import { api } from "./client";
import type { components } from "./schema";

type ApiResult<T> = { data?: T; error?: components["schemas"]["ErrorResponse"] };

// Ошибки backend приходят в едином формате {"error": {"code", "message"}} (docs/08-api.md § 1).
export class ApiError extends Error {
  constructor(
    message: string,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function unwrap<T>({ data, error }: ApiResult<T>): T {
  if (error || data === undefined) {
    throw new ApiError(error?.error?.message ?? "Backend недоступен", error?.error?.code);
  }
  return data;
}

export const healthQuery = queryOptions({
  queryKey: ["health"],
  queryFn: async () => unwrap(await api.GET("/api/health")),
  retry: false,
  refetchInterval: 15_000,
});

export const samplesQuery = queryOptions({
  queryKey: ["samples"],
  queryFn: async () => unwrap(await api.GET("/api/samples")),
  retry: false,
  staleTime: 60_000,
});

export const projectsQuery = queryOptions({
  queryKey: ["projects"],
  queryFn: async () => unwrap(await api.GET("/api/projects")),
  retry: false,
});

export const projectQuery = (projectId: number) =>
  queryOptions({
    queryKey: ["project", projectId],
    queryFn: async () =>
      unwrap(await api.GET("/api/projects/{project_id}", { params: { path: { project_id: projectId } } })),
    retry: false,
  });

export const versionsQuery = (projectId: number) =>
  queryOptions({
    queryKey: ["versions", projectId],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/projects/{project_id}/versions", {
          params: { path: { project_id: projectId } },
        }),
      ),
    retry: false,
  });

// Ключи данных версии содержат versionId: после правки HEAD меняется, и кеш старой версии не мешает.
export const treeQuery = (projectId: number, versionId?: number) =>
  queryOptions({
    queryKey: ["tree", projectId, versionId ?? "head"],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/projects/{project_id}/tree", {
          params: { path: { project_id: projectId }, query: { version_id: versionId } },
        }),
      ),
    retry: false,
  });

export const fileQuery = (projectId: number, path: string | null, versionId?: number) =>
  queryOptions({
    queryKey: ["file", projectId, versionId ?? "head", path],
    enabled: path !== null,
    queryFn: async () =>
      unwrap(
        await api.GET("/api/projects/{project_id}/files/{path}", {
          params: {
            path: { project_id: projectId, path: path ?? "" },
            query: { version_id: versionId },
          },
        }),
      ),
    retry: false,
  });

export const structureQuery = (
  projectId: number,
  mode: "types" | "methods" | "deps" = "methods",
  versionId?: number,
) =>
  queryOptions({
    queryKey: ["structure", projectId, versionId ?? "head", mode],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/projects/{project_id}/structure", {
          params: { path: { project_id: projectId }, query: { mode, version_id: versionId } },
        }),
      ),
    retry: false,
  });

export const flowQuery = (projectId: number, methodId: string | null, versionId?: number) =>
  queryOptions({
    queryKey: ["flow", projectId, versionId ?? "head", methodId],
    enabled: methodId !== null,
    queryFn: async () =>
      unwrap(
        await api.GET("/api/projects/{project_id}/flow", {
          params: {
            path: { project_id: projectId },
            query: { method_id: methodId ?? "", version_id: versionId },
          },
        }),
      ),
    retry: false,
  });

export const analysisQuery = (projectId: number) =>
  queryOptions({
    queryKey: ["analysis", projectId],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/projects/{project_id}/analysis", {
          params: { path: { project_id: projectId } },
        }),
      ),
    retry: false,
  });

export const scenariosQuery = (projectId: number) =>
  queryOptions({
    queryKey: ["scenarios", projectId],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/projects/{project_id}/scenarios", {
          params: { path: { project_id: projectId } },
        }),
      ),
    retry: false,
  });
