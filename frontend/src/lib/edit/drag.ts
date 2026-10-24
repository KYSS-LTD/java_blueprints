import type { NodeKind } from "@/lib/graph/flow-model";

/**
 * Что тащит пользователь: блок из палитры или блок схемы. Во время `dragover` браузер не отдаёт
 * данные `dataTransfer`, поэтому перетаскиваемое хранится здесь.
 */
export type DragPayload = { type: "palette"; kind: NodeKind } | { type: "node"; nodeId: string };

export const DRAG_MIME = "application/x-jb-block";

let current: DragPayload | null = null;

export function beginDrag(event: { dataTransfer: DataTransfer | null }, payload: DragPayload): void {
  current = payload;
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = payload.type === "node" ? "move" : "copy";
    // Firefox не начинает перетаскивание без данных
    event.dataTransfer.setData(DRAG_MIME, JSON.stringify(payload));
    event.dataTransfer.setData("text/plain", payload.type === "palette" ? payload.kind : payload.nodeId);
  }
}

export function currentDrag(): DragPayload | null {
  return current;
}

export function endDrag(): void {
  current = null;
}
