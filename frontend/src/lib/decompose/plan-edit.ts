import type { DecomposeClass, DecomposePlan } from "@/lib/api/actions";

/** Цель переноса «в новый класс»: класс создаётся при переносе. */
export const NEW_CLASS = "\u0000new-class";

/** Класс, который показывается и правится как набор методов (не ядро и не вынесенный тип). */
export function isCluster(cls: DecomposeClass): boolean {
  return cls.role !== "core" && cls.role !== "model";
}

/** Имя можно менять у новых классов; ядро называется как монолит, вынесенный тип — как объявлен. */
export function isRenamable(cls: DecomposeClass): boolean {
  return isCluster(cls);
}

export function ownerOf(plan: DecomposePlan, memberId: string): DecomposeClass | undefined {
  return plan.classes.find((cls) => cls.members.includes(memberId));
}

/** Член можно перетащить: он не закреплён в ядре и не является выносимым типом. */
export function isMovable(plan: DecomposePlan, memberId: string): boolean {
  if (memberId in plan.pinned) return false;
  const owner = ownerOf(plan, memberId);
  return owner !== undefined && owner.role !== "model";
}

/** Класс может принять метод. */
export function canReceive(cls: DecomposeClass): boolean {
  return cls.role !== "model";
}

const IDENTIFIER = /^[A-Za-z_$][A-Za-z0-9_$]*$/;

export function isValidClassName(name: string): boolean {
  return IDENTIFIER.test(name);
}

/** Имя не совпадает с именем другого класса плана. */
export function isNameFree(plan: DecomposePlan, name: string, except?: string): boolean {
  return !plan.classes.some((cls) => cls.name === name && cls.name !== except);
}

/** `Helper`, `Helper2`, … — первое имя, которого нет в плане. */
export function uniqueClassName(plan: DecomposePlan, base = "Helper"): string {
  let name = base;
  for (let index = 2; !isNameFree(plan, name); index += 1) name = `${base}${index}`;
  return name;
}

export type MoveResult = {
  plan: DecomposePlan;
  /** Классы, оставшиеся без методов и убранные из плана. */
  removed: string[];
  /** Имя класса, созданного переносом в «новый класс». */
  created: string | null;
};

/**
 * Перенос метода в другой класс плана или в новый класс. Опустевшие классы убираются: так классы
 * сливаются. `null` — перенос невозможен (закреплённый член, тот же класс, неизвестная цель).
 */
export function moveMember(
  plan: DecomposePlan,
  memberId: string,
  target: string,
): MoveResult | null {
  const source = ownerOf(plan, memberId);
  if (!source || !isMovable(plan, memberId)) return null;
  let created: string | null = null;
  let classes = plan.classes.map((cls) => ({ ...cls, members: [...cls.members] }));

  if (target === NEW_CLASS) {
    if (source.members.length === 1 && isCluster(source)) return null; // новый класс вместо единственного метода
    created = uniqueClassName(plan);
    classes.push({
      name: created,
      role: "util",
      file: `${created}.java`,
      members: [],
      lines: 0,
      reason: "создан вручную",
    });
  } else {
    const receiver = classes.find((cls) => cls.name === target);
    if (!receiver || !canReceive(receiver) || receiver.name === source.name) return null;
  }
  const to = classes.find((cls) => cls.name === (created ?? target));
  const from = classes.find((cls) => cls.name === source.name);
  if (!to || !from) return null;
  from.members = from.members.filter((id) => id !== memberId);
  to.members.push(memberId);

  const removed = classes
    .filter((cls) => isCluster(cls) && cls.members.length === 0)
    .map((cls) => cls.name);
  classes = classes.filter((cls) => !removed.includes(cls.name));
  return {
    plan: { ...plan, classes, edges: withoutClasses(plan, removed) },
    removed,
    created,
  };
}

function withoutClasses(plan: DecomposePlan, dropped: string[]): DecomposePlan["edges"] {
  return plan.edges.filter((edge) => !dropped.includes(edge.from) && !dropped.includes(edge.to));
}

/** Новое имя класса: меняется имя, файл и концы связей. */
export function renameClass(plan: DecomposePlan, from: string, to: string): DecomposePlan {
  return {
    ...plan,
    classes: plan.classes.map((cls) =>
      cls.name === from ? { ...cls, name: to, file: `${to}.java` } : cls,
    ),
    edges: plan.edges.map((edge) => ({
      ...edge,
      from: edge.from === from ? to : edge.from,
      to: edge.to === from ? to : edge.to,
    })),
  };
}

/** Состав классов и имена двух планов совпадают (производные данные не сравниваются). */
export function sameComposition(a: DecomposePlan, b: DecomposePlan): boolean {
  if (a.classes.length !== b.classes.length) return false;
  return a.classes.every((cls, index) => {
    const other = b.classes[index];
    return (
      cls.name === other.name &&
      cls.members.length === other.members.length &&
      cls.members.every((id, i) => id === other.members[i])
    );
  });
}

/** Читаемое имя члена: `Main.java#Main.parseInput(String)` → `parseInput(String)`. */
export function memberLabel(memberId: string): string {
  const member = memberId.split("#")[1] ?? memberId;
  const paren = member.indexOf("(");
  const dot = member.lastIndexOf(".", paren === -1 ? undefined : paren);
  return dot === -1 ? member : member.slice(dot + 1);
}

/** Изменение размера ядра в процентах: «−57 %» (отрицательное — ядро уменьшилось). */
export function shrinkPercent(before: number, after: number): number {
  if (before <= 0) return 0;
  return Math.round(((after - before) / before) * 100);
}

export type PlanSummary = {
  clusters: number;
  detached: number;
  methods: number;
  pinned: number;
};

export function summarize(plan: DecomposePlan): PlanSummary {
  const clusters = plan.classes.filter(isCluster);
  const detached = plan.classes.filter((cls) => cls.role === "model");
  const core = plan.classes.find((cls) => cls.role === "core");
  return {
    clusters: clusters.length,
    detached: detached.length,
    methods: clusters.reduce((total, cls) => total + cls.members.length, 0),
    pinned: core ? core.members.filter((id) => id in plan.pinned).length : 0,
  };
}
