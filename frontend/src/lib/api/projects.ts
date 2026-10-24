import { api } from "./client";
import { unwrap } from "./queries";
import type { components } from "./schema";

export type ProjectCreated = components["schemas"]["ProjectCreated"];
export type ProjectFileIn = components["schemas"]["ProjectFileIn"];

export function createProjectFromSample(sampleId: string, name?: string): Promise<ProjectCreated> {
  return api
    .POST("/api/projects", { body: { sample_id: sampleId, name: name ?? null } })
    .then(unwrap);
}

export function createProjectFromText(name: string, files: ProjectFileIn[]): Promise<ProjectCreated> {
  return api.POST("/api/projects", { body: { name, files } }).then(unwrap);
}

/** Загрузка файлов и архива (multipart): backend сам распаковывает .zip с проверкой путей и размера. */
export function createProjectFromUpload(
  name: string,
  files: File[],
  archive?: File | null,
): Promise<ProjectCreated> {
  const form = new FormData();
  form.set("name", name);
  for (const file of files) form.append("files", file, file.name);
  if (archive) form.set("archive", archive, archive.name);
  return api
    .POST("/api/projects", {
      // тип тела описывает JSON; multipart собираем сами, Content-Type с границей выставит браузер
      body: undefined as never,
      bodySerializer: () => form,
    })
    .then(unwrap);
}

export function deleteProject(projectId: number): Promise<void> {
  return api
    .DELETE("/api/projects/{project_id}", { params: { path: { project_id: projectId } } })
    .then(({ error }) => {
      if (error) throw new Error(error.error?.message ?? "Не удалось удалить проект");
    });
}

export function exportUrl(projectId: number, kind: "source" | "bundle" | "report"): string {
  return `/api/projects/${projectId}/export/${kind}`;
}
