import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiError, paletteQuery, projectQuery, versionsQuery } from "@/lib/api/queries";
import type { components } from "@/lib/api/schema";
import { buildFlowModel } from "@/lib/graph/flow-model";
import flowsJson from "@/mocks/fixtures/flows.json";
import paletteJson from "@/mocks/fixtures/palette.json";
import { useEditorStore } from "@/stores/editor-store";

type FlowGraph = components["schemas"]["FlowGraph"];

const api = vi.hoisted(() => ({
  checkFragment: vi.fn(),
  validateOps: vi.fn(),
  postOps: vi.fn(),
  undoVersion: vi.fn(),
  redoVersion: vi.fn(),
  restoreVersion: vi.fn(),
  saveFile: vi.fn(),
  parseLive: vi.fn(),
}));
vi.mock("@/lib/api/mutations", () => api);

import { NodeInspector } from "./node-inspector";

const ORDERS = "Main.java#Main.processOrders(List<Order>)";
const model = buildFlowModel((flowsJson as unknown as Record<string, FlowGraph>)[ORDERS]);

function renderInspector() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  queryClient.setQueryData(projectQuery(1).queryKey, { id: 1, head_version_id: 3 } as never);
  queryClient.setQueryData(versionsQuery(1).queryKey, { head_version_id: 3, versions: [] } as never);
  queryClient.setQueryData(paletteQuery(1).queryKey, paletteJson as never);
  render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <NodeInspector projectId={1} versionId={3} methodId={ORDERS} model={model} />
      </TooltipProvider>
    </QueryClientProvider>,
  );
  return queryClient;
}

const opsResult = {
  version: { id: 4, number: 4, origin: "graph_op", description: "", parent_id: 3 },
  changed_files: ["Main.java"],
  focus_node_id: `${ORDERS}/op[0]`,
  id_map: {},
  flow: null,
  syntax_errors: [],
};

describe("NodeInspector", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useEditorStore.getState().reset();
    api.checkFragment.mockResolvedValue({ ok: true, message: null });
    api.validateOps.mockResolvedValue({ ok: true, errors: [], diff: [{ path: "Main.java", unified: "@@ -1 +1,2 @@\n+foo();" }] });
    api.postOps.mockResolvedValue(opsResult);
  });

  it("ничего не показывает, пока инспектор не открыт", () => {
    renderInspector();
    expect(screen.queryByTestId("node-inspector")).not.toBeInTheDocument();
  });

  it("вставка: проверяет поле у backend, показывает diff и отправляет insert_node", async () => {
    const user = userEvent.setup();
    useEditorStore.getState().openInspector({
      mode: "insert",
      methodId: ORDERS,
      kind: "operation",
      point: { parentId: ORDERS, slot: "body", index: 2 },
    });
    renderInspector();

    const field = await screen.findByLabelText(/Выражение/);
    const apply = screen.getByTestId("inspector-apply");
    expect(apply).toBeDisabled();
    await user.type(field, "foo()");

    await waitFor(() => expect(api.checkFragment).toHaveBeenCalledWith(1, "expression", "foo()", null, expect.anything()));
    expect(await screen.findByTestId("inspector-preview")).toHaveTextContent("+foo();");
    await waitFor(() => expect(apply).toBeEnabled());

    await user.click(apply);
    await waitFor(() => expect(api.postOps).toHaveBeenCalled());
    expect(api.postOps).toHaveBeenCalledWith(
      1,
      3,
      [{ op: "insert_node", parent_id: ORDERS, slot: "body", index: 2, kind: "operation", fields: { expr: "foo()" } }],
      "Вставить блок «Операция»",
    );
    // успех закрывает инспектор
    await waitFor(() => expect(screen.queryByTestId("node-inspector")).not.toBeInTheDocument());
  });

  it("неверное выражение блокирует кнопку и показывает причину у поля", async () => {
    const user = userEvent.setup();
    api.checkFragment.mockResolvedValue({ ok: false, message: "выражение не разбирается" });
    useEditorStore.getState().openInspector({
      mode: "insert",
      methodId: ORDERS,
      kind: "operation",
      point: { parentId: ORDERS, slot: "body", index: 0 },
    });
    renderInspector();

    await user.type(await screen.findByLabelText(/Выражение/), "2 +");
    expect(await screen.findByText("выражение не разбирается")).toBeInTheDocument();
    expect(screen.getByTestId("inspector-apply")).toBeDisabled();
    expect(api.postOps).not.toHaveBeenCalled();
  });

  it("ошибка backend по полю показывается у этого поля, инспектор остаётся открытым", async () => {
    const user = userEvent.setup();
    api.postOps.mockRejectedValue(new ApiError("Неверное выражение в условии", "INVALID_FRAGMENT", { field: "expr" }));
    useEditorStore.getState().openInspector({
      mode: "insert",
      methodId: ORDERS,
      kind: "operation",
      point: { parentId: ORDERS, slot: "body", index: 0 },
    });
    renderInspector();

    await user.type(await screen.findByLabelText(/Выражение/), "foo()");
    const apply = screen.getByTestId("inspector-apply");
    await waitFor(() => expect(apply).toBeEnabled());
    await user.click(apply);

    expect(await screen.findByText("Неверное выражение в условии")).toBeInTheDocument();
    expect(screen.getByTestId("node-inspector")).toBeInTheDocument();
  });

  it("правка блока: поля заполнены данными блока, на backend уходят только изменённые", async () => {
    const user = userEvent.setup();
    const condition = [...model.blocks.values()].find((b) => b.kind === "condition")!;
    useEditorStore.getState().openInspector({ mode: "update", methodId: ORDERS, nodeId: condition.id });
    renderInspector();

    const field = await screen.findByLabelText(/Выражение/);
    expect(field).toHaveValue("order.getQuantity() > 0");
    expect(screen.getByTestId("inspector-apply")).toBeDisabled(); // менять нечего

    await user.clear(field);
    await user.type(field, "order != null");
    const apply = screen.getByTestId("inspector-apply");
    await waitFor(() => expect(apply).toBeEnabled());
    await user.click(apply);

    await waitFor(() => expect(api.postOps).toHaveBeenCalled());
    expect(api.postOps.mock.calls[0][2]).toEqual([
      { op: "update_node", node_id: condition.id, fields: { expr: "order != null" } },
    ]);
  });

  it("действие «обернуть в if» отправляет wrap_node с условием из формы", async () => {
    const user = userEvent.setup();
    const target = `${ORDERS}/var[0]`;
    useEditorStore.getState().openInspector({
      mode: "action",
      methodId: ORDERS,
      key: "wrap:if",
      kind: "condition",
      title: "Обернуть в if",
      fields: [{ name: "expr", label: "Условие", type: "expression", required: true, default: null, options: [] }],
      contextId: target,
      description: "Обернуть в if",
      makeOp: (fields) => ({ op: "wrap_node", node_ids: [target], wrapper: "if", fields }),
    });
    renderInspector();

    await user.type(await screen.findByLabelText(/Условие/), "batch != null");
    const apply = screen.getByTestId("inspector-apply");
    await waitFor(() => expect(apply).toBeEnabled());
    await user.click(apply);

    await waitFor(() => expect(api.postOps).toHaveBeenCalled());
    expect(api.postOps.mock.calls[0][2]).toEqual([
      { op: "wrap_node", node_ids: [target], wrapper: "if", fields: { expr: "batch != null" } },
    ]);
  });

  it("блок без формы (try) объясняет, что он правится текстом", async () => {
    const tryBlock = [...model.blocks.values()].find((b) => b.kind === "try")!;
    useEditorStore.getState().openInspector({ mode: "update", methodId: ORDERS, nodeId: tryBlock.id });
    renderInspector();
    expect(await screen.findByText(/правиться только текстом|править только текстом/)).toBeInTheDocument();
    expect(screen.queryByTestId("inspector-apply")).not.toBeInTheDocument();
  });
});
