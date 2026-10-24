"use client";

import { Copy, GitBranchPlus, PackageOpen, Pencil, Scissors, Trash2, WrapText } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { Block, FlowModel } from "@/lib/graph/flow-model";
import { ru } from "@/lib/i18n/ru";

import { type Branch, type BlockCapabilities, capabilitiesOf, type Wrapper } from "./use-canvas-editing";

const t = ru.edit;

const WRAPPERS: Wrapper[] = ["if", "try", "for", "while", "block"];

export type MenuState = { x: number; y: number; blockId: string };

export type MenuActions = {
  edit: (id: string) => void;
  duplicate: (id: string) => void;
  requestDelete: (id: string) => void;
  unwrap: (id: string) => void;
  wrap: (id: string, wrapper: Wrapper) => void;
  addBranch: (id: string, branch: Branch) => void;
  extract: (id: string) => void;
};

type MenuProps = {
  model: FlowModel;
  state: MenuState | null;
  actions: MenuActions;
  onClose: () => void;
};

/** Контекстное меню блока: открывается в точке клика, пункты зависят от вида блока (docs/09 § 3.3). */
export function NodeMenu({ model, state, actions, onClose }: MenuProps) {
  const block: Block | undefined = state ? model.blocks.get(state.blockId) : undefined;
  const caps: BlockCapabilities | null = block ? capabilitiesOf(model, block) : null;
  const open = state !== null && block !== undefined && caps !== null;

  return (
    <DropdownMenu open={open} onOpenChange={(next) => !next && onClose()}>
      <DropdownMenuTrigger asChild>
        {/* невидимая точка привязки меню к месту клика */}
        <span
          aria-hidden
          className="pointer-events-none fixed size-px"
          style={{ left: state?.x ?? 0, top: state?.y ?? 0 }}
        />
      </DropdownMenuTrigger>
      {open && block && caps && state && (
        <DropdownMenuContent align="start" className="min-w-52" data-testid="node-menu">
          {caps.edit && (
            <DropdownMenuItem onSelect={() => actions.edit(block.id)}>
              <Pencil /> {t.menu.edit}
            </DropdownMenuItem>
          )}
          {caps.duplicate && (
            <DropdownMenuItem onSelect={() => actions.duplicate(block.id)}>
              <Copy /> {t.menu.duplicate}
              <span className="ml-auto text-[10px] text-muted-foreground">Ctrl+D</span>
            </DropdownMenuItem>
          )}
          {caps.wrap && (
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>
                <WrapText /> {t.menu.wrap}
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent>
                {WRAPPERS.map((wrapper) => (
                  <DropdownMenuItem key={wrapper} onSelect={() => actions.wrap(block.id, wrapper)}>
                    {t.menu.wrappers[wrapper]}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
          )}
          {caps.extract && (
            <DropdownMenuItem onSelect={() => actions.extract(block.id)} data-testid="menu-extract">
              <Scissors /> {t.menu.extract}
            </DropdownMenuItem>
          )}
          {caps.unwrap && (
            <DropdownMenuItem onSelect={() => actions.unwrap(block.id)}>
              <PackageOpen /> {t.menu.unwrap}
            </DropdownMenuItem>
          )}
          {caps.branches.length > 0 && (
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>
                <GitBranchPlus /> {t.menu.branch}
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent>
                {caps.branches.map((branch) => (
                  <DropdownMenuItem key={branch} onSelect={() => actions.addBranch(block.id, branch)}>
                    {t.menu.branches[branch]}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
          )}
          {caps.remove && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onSelect={() => actions.requestDelete(block.id)}>
                <Trash2 /> {t.menu.delete}
                <span className="ml-auto text-[10px] text-muted-foreground">Del</span>
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      )}
    </DropdownMenu>
  );
}

type DeleteProps = {
  model: FlowModel;
  blockId: string | null;
  onCancel: () => void;
  onDelete: (id: string, mode: "with_body" | "unwrap") => void;
};

/** Блок с содержимым удаляется после вопроса: целиком или оставив тело на месте. */
export function DeleteBlockDialog({ model, blockId, onCancel, onDelete }: DeleteProps) {
  const block = blockId ? model.blocks.get(blockId) : undefined;
  const canKeepBody = block ? capabilitiesOf(model, block).unwrap : false;
  const choose = (mode: "with_body" | "unwrap") => {
    if (blockId) onDelete(blockId, mode);
    onCancel();
  };

  return (
    <Dialog open={block !== undefined} onOpenChange={(open) => !open && onCancel()}>
      <DialogContent className="sm:max-w-md" data-testid="delete-dialog">
        <DialogHeader>
          <DialogTitle>{t.deleteTitle}</DialogTitle>
          <DialogDescription>
            {block && <code className="font-mono text-xs">{block.title}: {block.code}</code>}
            <span className="mt-1 block">{t.deleteText}</span>
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2 sm:gap-2">
          <Button variant="outline" onClick={onCancel}>
            {ru.common.cancel}
          </Button>
          {canKeepBody && (
            <Button variant="secondary" onClick={() => choose("unwrap")}>
              {t.keepBody}
            </Button>
          )}
          <Button variant="destructive" onClick={() => choose("with_body")} data-testid="delete-with-body">
            {t.deleteConfirm}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
