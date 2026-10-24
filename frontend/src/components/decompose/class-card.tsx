"use client";

import { FileCode2, GripVertical, Lock, MoveRight } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import type { DecomposeClass, DecomposePlan } from "@/lib/api/actions";
import {
  NEW_CLASS,
  canReceive,
  isMovable,
  isRenamable,
  isValidClassName,
  memberLabel,
} from "@/lib/decompose/plan-edit";
import { ru } from "@/lib/i18n/ru";
import { cn } from "@/lib/utils";

const t = ru.decompose;

// Классы Tailwind пишутся целиком, иначе сборка их не увидит.
const ROLE_STYLE: Record<DecomposeClass["role"], string> = {
  core: "border-node-method/50 text-node-method",
  parser: "border-node-variable/50 text-node-variable",
  analyzer: "border-node-condition/50 text-node-condition",
  optimizer: "border-node-loop/50 text-node-loop",
  printer: "border-node-operation/50 text-node-operation",
  model: "border-border text-muted-foreground",
  util: "border-border text-muted-foreground",
};

export type CardHandlers = {
  /** Метод, который сейчас тянут (HTML5 drag-and-drop не отдаёт данные до отпускания). */
  dragged: string | null;
  onDragStart: (memberId: string) => void;
  onDragEnd: () => void;
  onMove: (memberId: string, target: string) => void;
  onRename: (from: string, to: string) => void;
  /** Имя не занято другим классом плана. */
  isNameFree: (name: string, except: string) => boolean;
};

function MemberRow({
  plan,
  cls,
  memberId,
  handlers,
}: {
  plan: DecomposePlan;
  cls: DecomposeClass;
  memberId: string;
  handlers: CardHandlers;
}) {
  const movable = isMovable(plan, memberId);
  const reason = plan.pinned[memberId];
  const targets = plan.classes.filter((other) => canReceive(other) && other.name !== cls.name);
  return (
    <li
      className={cn(
        "group flex items-center gap-1 rounded px-1 py-0.5 font-mono text-xs",
        movable ? "cursor-grab hover:bg-accent active:cursor-grabbing" : "text-muted-foreground",
        handlers.dragged === memberId && "opacity-50",
      )}
      draggable={movable}
      onDragStart={(event) => {
        if (!movable) return;
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", memberId);
        handlers.onDragStart(memberId);
      }}
      onDragEnd={handlers.onDragEnd}
      data-testid="decompose-member"
      data-member={memberId}
      data-movable={movable}
      title={reason}
    >
      {movable ? (
        <GripVertical className="size-3 shrink-0 text-muted-foreground" aria-hidden />
      ) : (
        <Lock className="size-3 shrink-0" aria-label={t.pinnedHint} />
      )}
      <span className="min-w-0 flex-1 truncate">{memberLabel(memberId)}</span>
      {movable && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon-xs"
              className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100"
              aria-label={`${t.moveMenu}: ${memberLabel(memberId)}`}
              data-testid="member-move"
            >
              <MoveRight />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-44">
            {targets.map((target) => (
              <DropdownMenuItem
                key={target.name}
                onSelect={() => handlers.onMove(memberId, target.name)}
                data-testid="member-move-target"
              >
                {t.moveTo.replace("…", "")} {target.name}
              </DropdownMenuItem>
            ))}
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => handlers.onMove(memberId, NEW_CLASS)}>
              {t.newClass}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </li>
  );
}

function NameField({ cls, handlers }: { cls: DecomposeClass; handlers: CardHandlers }) {
  const [typed, setTyped] = useState<string | null>(null);
  const value = typed ?? cls.name;
  const trimmed = value.trim();
  const problem =
    typed === null || trimmed === cls.name
      ? null
      : !isValidClassName(trimmed)
        ? t.renameInvalid
        : !handlers.isNameFree(trimmed, cls.name)
          ? t.renameTaken
          : null;

  const finish = () => {
    if (typed !== null && trimmed !== cls.name && problem === null)
      handlers.onRename(cls.name, trimmed);
    setTyped(null);
  };

  return (
    <div className="min-w-0 flex-1">
      <Input
        value={value}
        onChange={(event) => setTyped(event.target.value)}
        onBlur={finish}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") setTyped(null);
        }}
        aria-label={t.rename}
        aria-invalid={problem !== null}
        spellCheck={false}
        autoComplete="off"
        className="h-7 px-2 font-mono text-sm font-semibold"
        data-testid="decompose-name"
      />
      {problem && (
        <p className="mt-0.5 text-[11px] text-destructive" role="alert">
          {problem}
        </p>
      )}
    </div>
  );
}

/** Карточка класса «после»: имя, роль, строки, методы; принимает перетаскиваемые методы. */
export function ClassCard({
  plan,
  cls,
  handlers,
}: {
  plan: DecomposePlan;
  cls: DecomposeClass;
  handlers: CardHandlers;
}) {
  const [over, setOver] = useState(false);
  const owner = handlers.dragged
    ? plan.classes.find((c) => c.members.includes(handlers.dragged!))
    : undefined;
  const accepts =
    handlers.dragged !== null && canReceive(cls) && owner !== undefined && owner.name !== cls.name;
  const isCore = cls.role === "core";
  const pinned = cls.members.filter((id) => !isMovable(plan, id));
  const movable = cls.members.filter((id) => isMovable(plan, id));

  return (
    <section
      className={cn(
        "flex flex-col gap-2 rounded-lg border bg-card p-3 transition-colors",
        accepts && "border-dashed border-primary/60",
        accepts && over && "bg-primary/10 ring-1 ring-primary",
      )}
      aria-label={cls.name}
      onDragOver={(event) => {
        if (!accepts) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        setOver(true);
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOver(false);
      }}
      onDrop={(event) => {
        setOver(false);
        if (!accepts || !handlers.dragged) return;
        event.preventDefault();
        handlers.onMove(handlers.dragged, cls.name);
      }}
      data-testid="decompose-class"
      data-class={cls.name}
      data-role={cls.role}
    >
      <div className="flex items-start gap-2">
        {isRenamable(cls) ? (
          <NameField cls={cls} handlers={handlers} />
        ) : (
          <h3 className="min-w-0 flex-1 truncate py-1 font-mono text-sm font-semibold">
            {cls.name}
          </h3>
        )}
        <Badge variant="outline" className={cn("shrink-0 text-[10px]", ROLE_STYLE[cls.role])}>
          {t.roles[cls.role]}
        </Badge>
      </div>
      <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <FileCode2 className="size-3 shrink-0" aria-hidden />
        <code>{cls.file}</code>
        <span aria-hidden>·</span>
        <span data-testid="decompose-lines">
          {cls.lines} {t.lines.toLowerCase()}
        </span>
        <span aria-hidden>·</span>
        <span data-testid="decompose-count">
          {cls.members.length} {isCore ? t.members.toLowerCase() : t.methods}
        </span>
      </p>
      {cls.reason && <p className="text-[11px] text-muted-foreground">{cls.reason}</p>}

      {movable.length > 0 && (
        <ul className="max-h-56 space-y-0.5 overflow-y-auto" aria-label={cls.name}>
          {movable.map((id) => (
            <MemberRow key={id} plan={plan} cls={cls} memberId={id} handlers={handlers} />
          ))}
        </ul>
      )}
      {pinned.length > 0 && (
        <details className="text-xs" data-testid="decompose-pinned">
          <summary className="cursor-pointer text-muted-foreground select-none">
            {t.pinned}: {pinned.length}
          </summary>
          <ul className="mt-1 max-h-40 space-y-0.5 overflow-y-auto">
            {pinned.map((id) => (
              <MemberRow key={id} plan={plan} cls={cls} memberId={id} handlers={handlers} />
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

/** Место для метода, из которого вырастает новый класс. */
export function NewClassDrop({ handlers }: { handlers: CardHandlers }) {
  const [over, setOver] = useState(false);
  const active = handlers.dragged !== null;
  return (
    <div
      className={cn(
        "flex min-h-16 items-center justify-center rounded-lg border border-dashed p-3 text-center text-xs text-muted-foreground transition-colors",
        active && "border-primary/60 text-foreground",
        active && over && "bg-primary/10 ring-1 ring-primary",
      )}
      onDragOver={(event) => {
        if (!active) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        setOver(false);
        if (!handlers.dragged) return;
        event.preventDefault();
        handlers.onMove(handlers.dragged, NEW_CLASS);
      }}
      data-testid="decompose-new-class"
    >
      {active ? t.newClassHint : t.dragHint}
    </div>
  );
}
