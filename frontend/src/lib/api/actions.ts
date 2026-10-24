import type { QueryClient } from "@tanstack/react-query";

import { api } from "./client";
import { unwrap } from "./queries";
import type { components } from "./schema";

type Schemas = components["schemas"];

export type Scenario = Schemas["Scenario"];
export type ScenarioCreate = Schemas["ScenarioCreate"];
export type ScenarioUpdate = Schemas["ScenarioUpdate"];
export type FixPreview = Schemas["FixPreview"];
export type ApplyResult = Schemas["ApplyResult"];
export type OptimizePlan = Schemas["OptimizePlan"];
export type OptimizeStep = Schemas["OptimizeStep"];
export type ExtractMethodPreview = Schemas["ExtractMethodPreview"];
export type ExtractMethodRequest = Schemas["ExtractMethodRequest"];
export type DecomposePlan = Schemas["DecomposePlan"];
export type DecomposeClass = Schemas["DecomposeClass"];
export type DecomposePreview = Schemas["DecomposePreview"];
export type DecomposeOptions = Schemas["DecomposeOptions"];
export type FileDiff = Schemas["FileDiff"];
export type Precondition = Schemas["Precondition"];
export type SourceRange = Schemas["SourceRange"];

// --- сценарии ----------------------------------------------------------------------------------------------

export async function createScenario(projectId: number, body: ScenarioCreate): Promise<Scenario> {
  return unwrap(
    await api.POST("/api/projects/{project_id}/scenarios", {
      params: { path: { project_id: projectId } },
      body,
    }),
  );
}

export async function updateScenario(
  projectId: number,
  scenarioId: number,
  body: ScenarioUpdate,
): Promise<Scenario> {
  return unwrap(
    await api.PUT("/api/projects/{project_id}/scenarios/{scenario_id}", {
      params: { path: { project_id: projectId, scenario_id: scenarioId } },
      body,
    }),
  );
}

export async function deleteScenario(projectId: number, scenarioId: number): Promise<void> {
  const result = await api.DELETE("/api/projects/{project_id}/scenarios/{scenario_id}", {
    params: { path: { project_id: projectId, scenario_id: scenarioId } },
  });
  if (result.error) unwrap(result as never);
}

export async function startGeneration(projectId: number): Promise<number> {
  const ref = unwrap(
    await api.POST("/api/projects/{project_id}/scenarios/generate", {
      params: { path: { project_id: projectId } },
    }),
  );
  return ref.run_id;
}

// --- исправления и оптимизация ------------------------------------------------------------------------------

export async function previewFix(projectId: number, findingId: string): Promise<FixPreview> {
  return unwrap(
    await api.POST("/api/projects/{project_id}/fixes/{finding_id}/preview", {
      params: { path: { project_id: projectId, finding_id: findingId } },
      body: {},
    }),
  );
}

export async function applyFix(
  projectId: number,
  findingId: string,
  baseVersionId: number,
): Promise<ApplyResult> {
  return unwrap(
    await api.POST("/api/projects/{project_id}/fixes/{finding_id}/apply", {
      params: { path: { project_id: projectId, finding_id: findingId } },
      body: { base_version_id: baseVersionId },
    }),
  );
}

export async function verifyFinding(projectId: number, findingId: string): Promise<number> {
  const ref = unwrap(
    await api.POST("/api/projects/{project_id}/findings/{finding_id}/verify", {
      params: { path: { project_id: projectId, finding_id: findingId } },
    }),
  );
  return ref.run_id;
}

export async function planOptimize(projectId: number): Promise<OptimizePlan> {
  return unwrap(
    await api.POST("/api/projects/{project_id}/optimize/plan", {
      params: { path: { project_id: projectId } },
      body: {},
    }),
  );
}

export async function applyOptimize(
  projectId: number,
  baseVersionId: number,
  stepIds: string[],
): Promise<number> {
  const ref = unwrap(
    await api.POST("/api/projects/{project_id}/optimize/apply", {
      params: { path: { project_id: projectId } },
      body: { base_version_id: baseVersionId, step_ids: stepIds },
    }),
  );
  return ref.run_id;
}

export async function acceptCandidate(projectId: number, versionId: number) {
  return unwrap(
    await api.POST("/api/projects/{project_id}/versions/{version_id}/accept", {
      params: { path: { project_id: projectId, version_id: versionId } },
    }),
  );
}

export async function rejectCandidate(projectId: number, versionId: number) {
  return unwrap(
    await api.POST("/api/projects/{project_id}/versions/{version_id}/reject", {
      params: { path: { project_id: projectId, version_id: versionId } },
    }),
  );
}

// --- выделение метода и декомпозиция ---------------------------------------------------------------------------

export async function previewExtract(
  projectId: number,
  request: ExtractMethodRequest,
): Promise<ExtractMethodPreview> {
  return unwrap(
    await api.POST("/api/projects/{project_id}/extract-method/preview", {
      params: { path: { project_id: projectId } },
      body: request,
    }),
  );
}

export async function applyExtract(
  projectId: number,
  baseVersionId: number,
  request: Pick<ExtractMethodRequest, "node_ids" | "range" | "name">,
): Promise<ApplyResult> {
  return unwrap(
    await api.POST("/api/projects/{project_id}/extract-method/apply", {
      params: { path: { project_id: projectId } },
      body: { ...request, base_version_id: baseVersionId },
    }),
  );
}

export async function planDecompose(
  projectId: number,
  options?: DecomposeOptions,
): Promise<DecomposePlan> {
  return unwrap(
    await api.POST("/api/projects/{project_id}/decompose/plan", {
      params: { path: { project_id: projectId } },
      body: { options: options ?? null },
    }),
  );
}

export async function previewDecompose(
  projectId: number,
  baseVersionId: number,
  plan: DecomposePlan,
): Promise<DecomposePreview> {
  return unwrap(
    await api.POST("/api/projects/{project_id}/decompose/preview", {
      params: { path: { project_id: projectId } },
      body: { base_version_id: baseVersionId, plan },
    }),
  );
}

export async function applyDecompose(
  projectId: number,
  baseVersionId: number,
  plan: DecomposePlan,
): Promise<ApplyResult> {
  return unwrap(
    await api.POST("/api/projects/{project_id}/decompose/apply", {
      params: { path: { project_id: projectId } },
      body: { base_version_id: baseVersionId, plan },
    }),
  );
}

// --- обновление данных после изменений --------------------------------------------------------------------------

const PROJECT_KEYS = [
  "project",
  "versions",
  "tree",
  "file",
  "structure",
  "flow",
  "analysis",
  "metrics",
  "fixes",
  "scenarios",
  "runs",
  "diff",
] as const;

/** Данные проекта после изменения HEAD, сценариев или результата проверки устарели. */
export async function refreshProject(client: QueryClient, projectId: number): Promise<void> {
  await Promise.all(
    PROJECT_KEYS.map((key) =>
      client.invalidateQueries({
        predicate: (query) => query.queryKey[0] === key && query.queryKey[1] === projectId,
      }),
    ),
  );
}
