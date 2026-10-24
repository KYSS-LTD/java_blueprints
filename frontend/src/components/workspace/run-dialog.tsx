"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bug, Loader2, Play } from "lucide-react";
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
import { scenariosQuery } from "@/lib/api/queries";
import { flushDrafts } from "@/lib/edit/session";
import { ru } from "@/lib/i18n/ru";
import { useDebugStore } from "@/stores/debug-store";
import { useRunStore } from "@/stores/run-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

const t = ru.runDialog;

/** Аргументы командной строки из поля ввода: пробелы разделяют, кавычки группируют. */
export function parseArgs(text: string): string[] {
  const args: string[] = [];
  const pattern = /"([^"]*)"|'([^']*)'|(\S+)/g;
  for (const match of text.matchAll(pattern)) args.push(match[1] ?? match[2] ?? match[3]);
  return args;
}

/** Значение списка сценариев «все сразу» (только для трассировки). */
const ALL_SCENARIOS = "all";

type Props = {
  projectId: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `trace` — «Дебаг»: тот же ввод, но запуск считает выполнения блоков. */
  mode?: "run" | "trace";
};

export function RunDialog({ projectId, open, onOpenChange, mode = "run" }: Props) {
  const tr = mode === "trace" ? ru.debugDialog : null;
  const [stdin, setStdin] = useState("");
  const [argsText, setArgsText] = useState("");
  const [scenarioId, setScenarioId] = useState("");
  const scenarios = useQuery({ ...scenariosQuery(projectId), enabled: open });
  const setBottomTab = useWorkspaceStore((s) => s.setBottomTab);
  const queryClient = useQueryClient();

  const run = useMutation({
    mutationFn: async () => {
      // запускается сохранённый код: набранное в редакторе уходит на сервер до запуска
      await flushDrafts(queryClient, projectId);
      const scenario = scenarios.data?.scenarios.find((s) => String(s.id) === scenarioId);
      if (mode === "trace") {
        const everything = scenarioId === ALL_SCENARIOS;
        useDebugStore.getState().clear();
        return useRunStore.getState().start(
          projectId,
          everything ? { kind: "trace" } : { kind: "trace", stdin, args: parseArgs(argsText) },
          `${ru.header.debug} · ${everything ? tr?.scenarioAll : (scenario?.name ?? t.scenarioNone)}`,
        );
      }
      return useRunStore.getState().start(
        projectId,
        { kind: "run", stdin, args: parseArgs(argsText) },
        scenario ? `${ru.console.started} · ${scenario.name}` : undefined,
      );
    },
    onSuccess: () => {
      setBottomTab("console");
      onOpenChange(false);
    },
    onError: (error: Error) => toast.error((tr ?? t).failed, { description: error.message }),
  });

  const pickScenario = (value: string) => {
    setScenarioId(value);
    if (value === ALL_SCENARIOS) return;
    const scenario = scenarios.data?.scenarios.find((s) => String(s.id) === value);
    if (!scenario) return;
    setStdin(scenario.stdin);
    setArgsText(scenario.args.map((a) => (/\s/.test(a) ? `"${a}"` : a)).join(" "));
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !run.isPending && onOpenChange(next)}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{tr?.title ?? t.title}</DialogTitle>
          <DialogDescription>{tr?.description ?? t.description}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {scenarios.data && scenarios.data.scenarios.length > 0 && (
            <div className="space-y-2">
              <Label htmlFor="run-scenario">{t.scenario}</Label>
              <select
                id="run-scenario"
                value={scenarioId}
                onChange={(event) => pickScenario(event.target.value)}
                className="h-9 w-full rounded-md border bg-input/30 px-3 text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
              >
                <option value="">{t.scenarioNone}</option>
                {mode === "trace" && <option value={ALL_SCENARIOS}>{tr?.scenarioAll}</option>}
                {scenarios.data.scenarios.map((scenario) => (
                  <option key={scenario.id} value={scenario.id}>
                    {scenario.name}
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">{tr?.scenarioHint ?? t.scenarioHint}</p>
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="run-stdin">{t.stdin}</Label>
            <textarea
              id="run-stdin"
              value={stdin}
              onChange={(event) => setStdin(event.target.value)}
              placeholder={t.stdinPlaceholder}
              disabled={scenarioId === ALL_SCENARIOS}
              spellCheck={false}
              className="h-40 w-full resize-y rounded-md border bg-input/30 p-2 font-mono text-xs outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="run-args">{t.args}</Label>
            <Input
              id="run-args"
              value={argsText}
              onChange={(event) => setArgsText(event.target.value)}
              placeholder={t.argsPlaceholder}
              disabled={scenarioId === ALL_SCENARIOS}
              className="font-mono text-xs"
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={run.isPending}>
            {ru.common.cancel}
          </Button>
          <Button onClick={() => run.mutate()} disabled={run.isPending}>
            {run.isPending ? <Loader2 className="animate-spin" /> : mode === "trace" ? <Bug /> : <Play />}
            {run.isPending ? (tr ?? t).starting : (tr ?? t).start}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
