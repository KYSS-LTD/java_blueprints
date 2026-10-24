import { keepPreviousData, queryOptions } from "@tanstack/react-query";

import { api } from "./client";
import type { components } from "./schema";

type ApiResult<T> = { data?: T; error?: components["schemas"]["ErrorResponse"] };

// Ошибки backend приходят в едином формате {"error": {"code", "message", "details"}} (docs/08-api.md § 1).
export class ApiError extends Error {
  constructor(
    message: string,
    readonly code?: string,
    /** Подробности ошибки: у операций над графом — `op_index`, `field`, `line`, `column`. */
    readonly details?: Record<string, unknown> | null,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** Поле формы, к которому относится ошибка операции (`INVALID_FRAGMENT`). */
  get field(): string | null {
    const field = this.details?.field;
    return typeof field === "string" ? field : null;
  }
}

export function unwrap<T>({ data, error }: ApiResult<T>): T {
  if (error || data === undefined) {
    throw new ApiError(error?.error?.message ?? "Backend недоступен", error?.error?.code, error?.error?.details);
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
    // после правки HEAD меняется: пока грузится новая версия, показываем прежнюю, а не пустое место
    placeholderData: keepPreviousData,
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
    // тот же файл прежней версии остаётся на экране, пока грузится новая (редактор не мигает и не теряет курсор)
    placeholderData: (previous) => (previous?.path === path ? previous : undefined),
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
    placeholderData: keepPreviousData,
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
    // схема того же метода прежней версии остаётся на холсте, пока грузится новая
    placeholderData: (previous) => (previous?.method_id === methodId ? previous : undefined),
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

/** История запусков проекта: из неё берутся итоги оптимизации (измеренное ускорение). */
export const runsQuery = (projectId: number) =>
  queryOptions({
    queryKey: ["runs", projectId],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/projects/{project_id}/runs", {
          params: { path: { project_id: projectId } },
        }),
      ),
    retry: false,
  });

/** Метрики версии; `compareTo` — версия «до» для сравнения «Оригинал → Текущая». */
export const metricsQuery = (projectId: number, compareTo?: number | null) =>
  queryOptions({
    queryKey: ["metrics", projectId, compareTo ?? "none"],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/projects/{project_id}/metrics", {
          params: {
            path: { project_id: projectId },
            query: { compare_to: compareTo ?? undefined },
          },
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

export const paletteQuery = (projectId: number) =>
  queryOptions({
    queryKey: ["palette", projectId],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/projects/{project_id}/palette", {
          params: { path: { project_id: projectId } },
        }),
      ),
    retry: false,
    staleTime: Infinity,
  });

export const diffQuery = (projectId: number, from: number, to: number) =>
  queryOptions({
    queryKey: ["diff", projectId, from, to],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/projects/{project_id}/diff", {
          params: { path: { project_id: projectId }, query: { from, to } },
        }),
      ),
    retry: false,
    staleTime: Infinity,
  });
