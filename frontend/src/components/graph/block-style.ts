import {
  Ban,
  Box,
  Code,
  CornerDownLeft,
  CornerDownRight,
  Equal,
  ExternalLink,
  GitBranch,
  ListTree,
  type LucideIcon,
  Repeat,
  ShieldAlert,
  ShieldCheck,
  ShieldX,
  SkipForward,
  SquareFunction,
  Terminal,
  Variable,
  Zap,
} from "lucide-react";

import type { BlockKind } from "@/lib/graph/flow-model";

export type BlockStyle = {
  icon: LucideIcon;
  /** Имя CSS-переменной цвета блока (docs/09 § 6). */
  color: string;
};

const style = (icon: LucideIcon, color: string): BlockStyle => ({ icon, color });

// Цвета берутся из токенов тёмной темы: --node-method, --node-variable и т. д. (globals.css).
const STYLES: Record<BlockKind, BlockStyle> = {
  method: style(Box, "--node-method"),
  var_decl: style(Variable, "--node-variable"),
  assign: style(Equal, "--node-variable"),
  operation: style(Terminal, "--node-operation"),
  condition: style(GitBranch, "--node-condition"),
  switch: style(ListTree, "--node-condition"),
  case: style(CornerDownRight, "--node-condition"),
  loop_for: style(Repeat, "--node-loop"),
  loop_foreach: style(Repeat, "--node-loop"),
  loop_while: style(Repeat, "--node-loop"),
  try: style(ShieldAlert, "--node-operation"),
  catch: style(ShieldX, "--severity-high"),
  finally: style(ShieldCheck, "--node-operation"),
  throw: style(Zap, "--severity-high"),
  return: style(CornerDownLeft, "--node-return"),
  break: style(Ban, "--node-condition"),
  continue: style(SkipForward, "--node-condition"),
  yield: style(CornerDownLeft, "--node-return"),
  lambda: style(SquareFunction, "--node-loop"),
  local_type: style(Box, "--node-method"),
  labeled: style(Repeat, "--node-loop"),
  assert: style(ShieldCheck, "--node-condition"),
  sync: style(ShieldAlert, "--node-operation"),
  code: style(Code, "--muted-foreground"),
  external: style(ExternalLink, "--node-call"),
};

export function blockStyle(kind: BlockKind): BlockStyle {
  return STYLES[kind] ?? STYLES.code;
}

/** Цвет в виде значения CSS: `var(--node-method)` или с прозрачностью через color-mix. */
export function colorVar(token: string, alpha = 1): string {
  return alpha >= 1
    ? `var(${token})`
    : `color-mix(in oklab, var(${token}) ${Math.round(alpha * 100)}%, transparent)`;
}
