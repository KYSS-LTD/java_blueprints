import { expect, type APIRequestContext, type Page } from "@playwright/test";

export type Project = { id: number; head_version_id: number };

/** Новый проект из встроенного примера: у каждого теста свои данные. */
export async function createProject(request: APIRequestContext, sample = "demo-orders"): Promise<Project> {
  const response = await request.post("/api/projects", { data: { sample_id: sample } });
  expect(response.status()).toBe(201);
  return (await response.json()) as Project;
}

/** Запуск на backend и ожидание конца (опрос `GET /runs/{id}`). */
export async function runAndWait(
  request: APIRequestContext,
  projectId: number,
  body: Record<string, unknown>,
  timeoutMs = 240_000,
) {
  const started = await request.post(`/api/projects/${projectId}/runs`, { data: body });
  expect(started.status()).toBe(202);
  const { run_id: runId } = (await started.json()) as { run_id: number };
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const info = (await (await request.get(`/api/runs/${runId}`)).json()) as {
      status: string;
      summary: Record<string, unknown>;
    };
    if (!["queued", "running"].includes(info.status)) return info;
    if (Date.now() > deadline) throw new Error(`запуск ${runId} не завершился`);
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
}

export async function headOf(request: APIRequestContext, projectId: number): Promise<number> {
  const info = (await (await request.get(`/api/projects/${projectId}`)).json()) as { head_version_id: number };
  return info.head_version_id;
}

/** Рабочая область проекта, опционально сразу на методе (`Main.java#Main.run(Scanner)`). */
export async function openWorkspace(page: Page, projectId: number, methodId?: string): Promise<void> {
  const query = methodId ? `?file=Main.java&method=${encodeURIComponent(methodId)}` : "";
  await page.goto(`/projects/${projectId}${query}`);
  await expect(page.getByTestId("workspace")).toBeVisible();
  if (methodId) await expect(page.getByTestId("flow-canvas")).toBeVisible();
}

export function blocks(page: Page) {
  return page.locator('[data-testid^="block-"]');
}

/** Исходник файла в HEAD через API. */
export async function sourceOf(request: APIRequestContext, projectId: number, path = "Main.java"): Promise<string> {
  const response = await request.get(`/api/projects/${projectId}/files/${path}`);
  return ((await response.json()) as { content: string }).content;
}
