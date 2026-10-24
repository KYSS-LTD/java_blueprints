// Режим моков (NEXT_PUBLIC_MOCK=1): подменяет fetch клиента openapi-fetch и отвечает JSON-фикстурами
// из ./fixtures (их генерирует scripts/gen_mock_fixtures.py по схемам контракта).
// Подключается динамическим импортом из lib/api/client.ts, поэтому в обычную сборку не попадает.
import buildJson from "./fixtures/build.json";
import fileMain from "./fixtures/file-main.json";
import files from "./fixtures/files.json";
import findings from "./fixtures/findings.json";
import fixes from "./fixtures/fixes.json";
import flows from "./fixtures/flows.json";
import health from "./fixtures/health.json";
import metrics from "./fixtures/metrics.json";
import palette from "./fixtures/palette.json";
import project from "./fixtures/project.json";
import projects from "./fixtures/projects.json";
import run7 from "./fixtures/run-7.json";
import run8 from "./fixtures/run-8.json";
import runs from "./fixtures/runs.json";
import samples from "./fixtures/samples.json";
import scenarios from "./fixtures/scenarios.json";
import structure from "./fixtures/structure.json";
import tree from "./fixtures/tree.json";
import versions from "./fixtures/versions.json";

// Блок-схемы всех методов демо-монолита по id члена (их строит настоящий движок).
export const MOCK_FLOWS: Record<string, unknown> = flows;

type Params = Record<string, string>;
type Reply = { status?: number; body: unknown };
type Handler = (params: Params, url: URL) => Reply | unknown;

function error(status: number, code: string, message: string): Reply {
  return { status, body: { error: { code, message } } };
}

type MockFinding = (typeof findings.findings)[number];

function findingById(id: string): MockFinding | undefined {
  return findings.findings.find((f) => f.id === id);
}

// Предпросмотр исправления для находки из фикстуры; без доступного исправления — 422, как у backend.
function previewOf(id: string): Reply | unknown {
  const finding = findingById(id);
  if (!finding) return error(404, "NOT_FOUND", `Находка ${id} не найдена`);
  if (!finding.fix?.available) {
    return error(422, "PRECONDITION_FAILED", "Исправление недоступно: предусловия не выполнены");
  }
  const before = "for (Order a : orders) {\n    for (Order b : merged) { /* поиск пары */ }\n}\n";
  const after = "Map<Key, List<Order>> index = new LinkedHashMap<>();\nfor (Order a : orders) { /* по ключу */ }\n";
  return {
    fix_id: finding.fix.fix_id,
    finding_id: finding.id,
    title: finding.title,
    summary: finding.recommendation,
    preconditions: finding.fix.preconditions,
    effect: {
      complexity_before: finding.complexity?.current ?? null,
      complexity_after: finding.complexity?.after ?? null,
      notes: null,
    },
    diff: [
      {
        path: finding.location.file,
        status: "modified",
        unified: `-${before}+${after}`,
        before,
        after,
      },
    ],
    behavior_preserving: finding.fix.behavior_preserving,
  };
}

// Решение по кандидату: версия из ответа получает новый статус.
function decisionOf(versionId: number, status: "active" | "rejected") {
  return {
    version: {
      id: versionId,
      number: versionId,
      parent_id: project.head_version_id,
      origin: "autofix",
      description: "Исправление",
      created_at: "2026-10-24T08:10:00Z",
      gate_status: "failed",
      status,
      content_hash: "mock",
    },
    head_version_id: status === "active" ? versionId : project.head_version_id,
  };
}

function optimizePlan() {
  const steps = findings.findings
    .filter((f) => f.fix?.available && f.fix.behavior_preserving && f.fix.fix_id.startsWith("OPT"))
    .map((f) => ({
      step_id: f.id,
      fix_id: f.fix?.fix_id ?? "",
      finding_id: f.id,
      title: f.title,
      effect: { complexity_before: f.complexity?.current ?? null, complexity_after: f.complexity?.after ?? null, notes: null },
      behavior_preserving: true,
    }));
  return {
    version_id: findings.version_id,
    steps,
    effect: { complexity_before: findings.complexity.current, complexity_after: findings.complexity.optimized, notes: null },
  };
}

const routes: Array<[method: string, pattern: string, handler: Handler]> = [
  ["GET", "/api/health", () => health],
  ["GET", "/api/samples", () => samples],
  ["GET", "/api/projects", () => projects],
  [
    "POST",
    "/api/projects",
    () => ({
      status: 201,
      body: {
        id: project.id,
        name: project.name,
        head_version_id: project.head_version_id,
        original_version_id: project.original_version_id,
        main_class: project.main_class,
        main_class_candidates: project.main_class_candidates,
        syntax_errors: [],
        files: files.files,
      },
    }),
  ],
  ["GET", "/api/projects/:id", () => project],
  ["GET", "/api/projects/:id/versions", () => versions],
  ["GET", "/api/projects/:id/files", () => files],
  [
    "GET",
    "/api/projects/:id/files/:path",
    (p) => (p.path === "Main.java" ? fileMain : error(404, "NOT_FOUND", `Файл ${p.path} не найден`)),
  ],
  ["GET", "/api/projects/:id/tree", () => tree],
  ["GET", "/api/projects/:id/structure", () => structure],
  [
    "GET",
    "/api/projects/:id/flow",
    (_, url) => {
      const methodId = url.searchParams.get("method_id") ?? "";
      return (
        MOCK_FLOWS[methodId] ??
        error(404, "NOT_FOUND", `В моках нет блок-схемы для ${methodId || "метода"}`)
      );
    },
  ],
  ["GET", "/api/projects/:id/analysis", () => findings],
  [
    "GET",
    "/api/projects/:id/findings",
    () => ({ version_id: findings.version_id, findings: findings.findings }),
  ],
  ["GET", "/api/projects/:id/metrics", () => metrics],
  ["GET", "/api/projects/:id/fixes", () => fixes],
  ["GET", "/api/projects/:id/palette", () => palette],
  ["GET", "/api/projects/:id/scenarios", () => scenarios],
  ["GET", "/api/projects/:id/runs", () => runs],
  [
    "GET",
    "/api/runs/:rid",
    (p) => (p.rid === "7" ? run7 : p.rid === "8" ? run8 : error(404, "NOT_FOUND", "Запуск не найден")),
  ],
  ["POST", "/api/projects/:id/fixes/:path/preview", (p) => previewOf(p.path)],
  [
    "POST",
    "/api/projects/:id/fixes/:path/apply",
    () => ({ status: 202, body: { candidate_version_id: 9, run_id: 8 } }),
  ],
  ["POST", "/api/projects/:id/findings/:path/verify", () => ({ status: 202, body: { run_id: 8 } })],
  ["POST", "/api/projects/:id/optimize/plan", () => optimizePlan()],
  ["POST", "/api/projects/:id/optimize/apply", () => ({ status: 202, body: { run_id: 8 } })],
  ["POST", "/api/projects/:id/versions/:vid/accept", (p) => decisionOf(Number(p.vid), "active")],
  ["POST", "/api/projects/:id/versions/:vid/reject", (p) => decisionOf(Number(p.vid), "rejected")],
  ["POST", "/api/projects/:id/build", () => buildJson],
  ["POST", "/api/projects/:id/runs", () => ({ status: 202, body: { run_id: 8, status: "queued" } })],
];

function compile(pattern: string): RegExp {
  const source = pattern.replace(/:(\w+)/g, (_, name: string) =>
    name === "path" ? `(?<${name}>.+)` : `(?<${name}>[^/]+)`,
  );
  return new RegExp(`^${source}$`);
}

const compiled = routes.map(([method, pattern, handler]) => ({
  method,
  regex: compile(pattern),
  handler,
}));

function isReply(value: unknown): value is Reply {
  return typeof value === "object" && value !== null && "body" in value && "status" in value;
}

export function resolveMock(method: string, url: URL): { status: number; body: unknown } {
  for (const route of compiled) {
    if (route.method !== method) continue;
    const match = route.regex.exec(url.pathname);
    if (!match) continue;
    const params: Params = {};
    for (const [key, value] of Object.entries(match.groups ?? {})) {
      params[key] = decodeURIComponent(value);
    }
    const result = route.handler(params, url);
    if (isReply(result)) {
      return { status: result.status ?? 200, body: result.body };
    }
    // ответ-ошибка из error() имеет status; обычное значение — это тело 200
    return { status: 200, body: result };
  }
  const reply = error(501, "NOT_IMPLEMENTED", `В режиме моков нет ответа для ${method} ${url.pathname}`);
  return { status: reply.status ?? 501, body: reply.body };
}

export async function mockFetch(request: Request): Promise<Response> {
  const url = new URL(request.url, "http://localhost");
  const delay = process.env.NODE_ENV === "test" ? 0 : 120;
  if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
  const { status, body } = resolveMock(request.method.toUpperCase(), url);
  return Response.json(body, { status });
}
