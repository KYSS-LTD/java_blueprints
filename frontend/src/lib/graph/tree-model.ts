import type { components } from "@/lib/api/schema";

export type TreeNode = components["schemas"]["TreeNode"];

export type TreeRow = {
  node: TreeNode;
  depth: number;
  expanded: boolean;
  /** Строка попала под поиск (подсвечивается), а не просто оказалась предком совпадения. */
  matched: boolean;
};

function matches(node: TreeNode, needle: string): boolean {
  return node.name.toLowerCase().includes(needle);
}

/** Узел или любой его потомок подходит под поиск. */
function subtreeMatches(node: TreeNode, needle: string): boolean {
  return matches(node, needle) || node.children.some((child) => subtreeMatches(child, needle));
}

/**
 * Плоский список видимых строк дерева. При поиске раскрываются все предки совпадений,
 * без поиска — только узлы из `expanded`.
 */
export function visibleRows(
  roots: TreeNode[],
  expanded: ReadonlySet<string>,
  search: string,
): TreeRow[] {
  const needle = search.trim().toLowerCase();
  const rows: TreeRow[] = [];
  const walk = (node: TreeNode, depth: number) => {
    if (needle && !subtreeMatches(node, needle)) return;
    const open = needle ? node.children.some((c) => subtreeMatches(c, needle)) : expanded.has(node.id);
    rows.push({ node, depth, expanded: open, matched: needle !== "" && matches(node, needle) });
    if (open) for (const child of node.children) walk(child, depth + 1);
  };
  for (const root of roots) walk(root, 0);
  return rows;
}

/** Узлы по умолчанию раскрыты до файлов и типов-контейнеров: видно, что есть в проекте. */
export function defaultExpanded(roots: TreeNode[]): Set<string> {
  const expanded = new Set<string>();
  const walk = (node: TreeNode) => {
    if (node.kind === "package" || node.kind === "file") {
      expanded.add(node.id);
      node.children.forEach(walk);
    }
  };
  roots.forEach(walk);
  return expanded;
}

/** Идентификаторы предков узла с заданным id (чтобы раскрыть путь к активному методу). */
export function pathTo(roots: TreeNode[], id: string): string[] {
  const path: string[] = [];
  const walk = (node: TreeNode): boolean => {
    if (node.id === id) return true;
    for (const child of node.children) {
      if (walk(child)) {
        path.push(node.id);
        return true;
      }
    }
    return false;
  };
  roots.some(walk);
  return path;
}

export function findNode(roots: TreeNode[], predicate: (node: TreeNode) => boolean): TreeNode | null {
  for (const root of roots) {
    if (predicate(root)) return root;
    const inner = findNode(root.children, predicate);
    if (inner) return inner;
  }
  return null;
}
