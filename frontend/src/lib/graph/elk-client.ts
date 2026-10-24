import type { ELK as ElkInstance, ElkNode } from "elkjs/lib/elk-api";
import ELK from "elkjs/lib/elk-api";

let elk: ElkInstance | null = null;

function workerClient(): ElkInstance | null {
  if (typeof Worker === "undefined") return null;
  elk ??= new ELK({
    workerFactory: () => new Worker(new URL("../../workers/elk.worker.ts", import.meta.url)),
  });
  return elk;
}

/**
 * Раскладывает граф в Web Worker; там, где потоков нет (тесты в Node), считает в основном потоке.
 * Результат — тот же граф с координатами `x`, `y`, `width`, `height` у каждого узла.
 */
export async function layoutWithElk(graph: ElkNode): Promise<ElkNode> {
  const client = workerClient();
  if (client) return client.layout(graph);
  const { default: Bundled } = await import("elkjs/lib/elk.bundled.js");
  return new Bundled().layout(graph);
}
