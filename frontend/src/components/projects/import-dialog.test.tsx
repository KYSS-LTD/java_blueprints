import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ProjectCreated } from "@/lib/api/projects";
import { ru } from "@/lib/i18n/ru";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const created: ProjectCreated = {
  id: 7,
  name: "demo",
  head_version_id: 1,
  original_version_id: 1,
  main_class: "Main",
  main_class_candidates: ["Main"],
  syntax_errors: [],
  files: [],
};
const createProjectFromSample = vi.fn(async () => created);
const createProjectFromText = vi.fn(async () => created);
const createProjectFromUpload = vi.fn(async () => created);
vi.mock("@/lib/api/projects", () => ({
  createProjectFromSample,
  createProjectFromText,
  createProjectFromUpload,
}));

vi.mock("@/lib/api/queries", () => ({
  samplesQuery: {
    queryKey: ["samples"],
    queryFn: async () => ({
      samples: [{ id: "demo-orders", name: "demo-orders", description: "Демо-монолит", lines: 995 }],
    }),
  },
}));

const { ImportDialog, splitUploads } = await import("./import-dialog");

const file = (name: string, size = 10) => new File(["x".repeat(size)], name);

function renderDialog() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ImportDialog open onOpenChange={() => {}} />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.clearAllMocks());

describe("splitUploads", () => {
  it("делит файлы на .java, один архив и отклонённые", () => {
    const result = splitUploads([file("A.java"), file("lib.ZIP"), file("second.zip"), file("notes.txt")]);
    expect(result.java.map((f) => f.name)).toEqual(["A.java"]);
    expect(result.archive?.name).toBe("lib.ZIP");
    expect(result.rejected.map((f) => f.name)).toEqual(["second.zip", "notes.txt"]);
  });
});

describe("ImportDialog", () => {
  it("открывает встроенный пример и переходит в проект", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("tab", { name: ru.importDialog.tabs.sample }));
    await user.click(await screen.findByRole("button", { name: ru.importDialog.openSample }));
    await waitFor(() => expect(createProjectFromSample).toHaveBeenCalledWith("demo-orders"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/projects/7"));
  });

  it("создаёт проект из вставленного текста: имя файла по умолчанию Main.java", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("tab", { name: ru.importDialog.tabs.text }));
    const create = screen.getAllByRole("button", { name: ru.importDialog.create }).at(-1)!;
    expect(create).toBeDisabled();
    await user.click(screen.getByLabelText(ru.importDialog.code));
    await user.paste("class Main {}");
    expect(create).toBeEnabled();
    await user.click(create);
    await waitFor(() => expect(createProjectFromText).toHaveBeenCalledTimes(1));
    expect(createProjectFromText).toHaveBeenCalledWith("Main", [{ path: "Main.java", content: "class Main {}" }]);
    await waitFor(() => expect(push).toHaveBeenCalledWith("/projects/7"));
  });

  it("загружает выбранные файлы и показывает их список", async () => {
    const user = userEvent.setup();
    renderDialog();
    const input = screen.getByTestId("file-input");
    await user.upload(input, [file("Main.java"), file("Util.java")]);
    expect(screen.getByText("Util.java")).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: ru.importDialog.create })[0]);
    await waitFor(() => expect(createProjectFromUpload).toHaveBeenCalledTimes(1));
    const [name, java, archive] = createProjectFromUpload.mock.calls[0] as unknown as [string, File[], File | null];
    expect(name).toBe("Main");
    expect(java.map((f) => f.name)).toEqual(["Main.java", "Util.java"]);
    expect(archive).toBeNull();
  });

  it("не даёт создать проект без файлов", () => {
    renderDialog();
    expect(screen.getAllByRole("button", { name: ru.importDialog.create })[0]).toBeDisabled();
  });
});
