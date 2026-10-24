import type { FlowModel, SourceRange } from "./flow-model";

/** Позиция (строка и колонка с 1, UTF-16) внутри диапазона; границы входят. */
export function rangeContains(range: SourceRange, line: number, column: number): boolean {
  if (line < range.start_line || line > range.end_line) return false;
  if (line === range.start_line && column < range.start_col) return false;
  if (line === range.end_line && column > range.end_col) return false;
  return true;
}

/**
 * Самый глубокий блок схемы под позицией курсора (docs/09 § 4). Колонки в UTF-16, как в Monaco,
 * поэтому кириллица в строках подсветку не сдвигает. Если курсор вне схемы — `null`.
 */
export function locateBlock(model: FlowModel, line: number, column: number): string | null {
  let best: { id: string; depth: number; span: number } | null = null;
  for (const block of model.blocks.values()) {
    if (!block.range || !rangeContains(block.range, line, column)) continue;
    const span =
      (block.range.end_line - block.range.start_line) * 100_000 +
      (block.range.end_col - block.range.start_col);
    if (best === null || block.depth > best.depth || (block.depth === best.depth && span < best.span)) {
      best = { id: block.id, depth: block.depth, span };
    }
  }
  return best?.id ?? null;
}
