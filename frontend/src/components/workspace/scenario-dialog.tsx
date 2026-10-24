"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createScenario, refreshProject, type Scenario, updateScenario } from "@/lib/api/actions";
import { ru } from "@/lib/i18n/ru";
import { parseArgs } from "@/components/workspace/run-dialog";

const t = ru.scenarioDialog;

function formatArgs(args: string[]): string {
  return args.map((a) => (/\s/.test(a) ? `"${a}"` : a)).join(" ");
}

type Props = {
  projectId: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Редактируемый сценарий; без него создаётся новый. */
  scenario?: Scenario | null;
};

/** Создание и правка сценария: название, ввод и аргументы. */
export function ScenarioDialog({ projectId, open, onOpenChange, scenario }: Props) {
  const [pending, setPending] = useState(false);
  return (
    <Dialog open={open} onOpenChange={(next) => !pending && onOpenChange(next)}>
      <DialogContent className="sm:max-w-lg">
        {/* содержимое монтируется при каждом открытии, поэтому поля всегда начинаются с актуальных значений */}
        <ScenarioForm
          projectId={projectId}
          scenario={scenario ?? null}
          onPending={setPending}
          onClose={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  );
}

function ScenarioForm({
  projectId,
  scenario,
  onPending,
  onClose,
}: {
  projectId: number;
  scenario: Scenario | null;
  onPending: (pending: boolean) => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(scenario?.name ?? "");
  const [note, setNote] = useState(scenario?.description ?? "");
  const [stdin, setStdin] = useState(scenario?.stdin ?? "");
  const [argsText, setArgsText] = useState(scenario ? formatArgs(scenario.args) : "");
  const [touched, setTouched] = useState(false);

  const save = useMutation({
    mutationFn: async () => {
      const body = { name: name.trim(), description: note, stdin, args: parseArgs(argsText) };
      return scenario
        ? updateScenario(projectId, scenario.id, body)
        : createScenario(projectId, body);
    },
    onMutate: () => onPending(true),
    onSettled: () => onPending(false),
    onSuccess: async () => {
      toast.success(ru.tests.saved);
      await refreshProject(queryClient, projectId);
      onClose();
    },
    onError: (error: Error) => toast.error(t.failed, { description: error.message }),
  });

  const invalid = name.trim() === "";

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        setTouched(true);
        if (!invalid) save.mutate();
      }}
    >
      <DialogHeader>
        <DialogTitle>{scenario ? t.editTitle : t.createTitle}</DialogTitle>
        <DialogDescription>{t.description}</DialogDescription>
      </DialogHeader>

      <div className="space-y-2">
        <Label htmlFor="scenario-name">{t.name}</Label>
        <Input
          id="scenario-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          aria-invalid={touched && invalid}
          autoFocus
        />
        {touched && invalid && <p className="text-xs text-destructive">{t.nameRequired}</p>}
      </div>

      <div className="space-y-2">
        <Label htmlFor="scenario-note">{t.note}</Label>
        <Input id="scenario-note" value={note} onChange={(event) => setNote(event.target.value)} />
      </div>

      <div className="space-y-2">
        <Label htmlFor="scenario-stdin">{t.stdin}</Label>
        <textarea
          id="scenario-stdin"
          value={stdin}
          onChange={(event) => setStdin(event.target.value)}
          spellCheck={false}
          className="h-32 w-full resize-y rounded-md border bg-input/30 p-2 font-mono text-xs outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="scenario-args">{t.args}</Label>
        <Input
          id="scenario-args"
          value={argsText}
          onChange={(event) => setArgsText(event.target.value)}
          placeholder={t.argsPlaceholder}
          className="font-mono text-xs"
        />
      </div>

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose} disabled={save.isPending}>
          {ru.common.cancel}
        </Button>
        <Button type="submit" disabled={save.isPending}>
          {save.isPending && <Loader2 className="animate-spin" />}
          {save.isPending ? t.saving : t.save}
        </Button>
      </DialogFooter>
    </form>
  );
}
