"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { Loader2, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { type Resolver, useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { blockStyle, colorVar } from "@/components/graph/block-style";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { checkFragment, type GraphOp, validateOps } from "@/lib/api/mutations";
import { ApiError, paletteQuery } from "@/lib/api/queries";
import {
  cleanFields,
  defaultValues,
  fieldsForKind,
  type FormValues,
  fragmentKind,
  type PaletteBlock,
  type PaletteField,
  valuesOfBlock,
} from "@/lib/edit/block-fields";
import { useEditing } from "@/lib/edit/use-editing";
import type { BlockKind, FlowModel } from "@/lib/graph/flow-model";
import { ru } from "@/lib/i18n/ru";
import { cn } from "@/lib/utils";
import { type InspectorTarget, useEditorStore } from "@/stores/editor-store";

const t = ru.inspector;

const CHECK_DELAY_MS = 300;
const PREVIEW_DELAY_MS = 450;

function buildSchema(fields: PaletteField[]) {
  const shape: Record<string, z.ZodType> = {};
  for (const field of fields) {
    if (field.type === "boolean") shape[field.name] = z.boolean();
    else if (field.required) shape[field.name] = z.string().trim().min(1, t.required);
    else shape[field.name] = z.string();
  }
  return z.object(shape);
}

/** Что показывает инспектор: поля формы и как из их значений получается операция. */
type Spec = {
  kind: BlockKind;
  /** Подпись над заголовком: «Новый блок», «Блок»… */
  heading: string;
  title: string;
  /** `null` — у блока нет формы, его правят текстом в редакторе кода. */
  fields: PaletteField[] | null;
  initial: FormValues;
  /** Узел, в области видимости которого проверяются имена. */
  contextId: string;
  submitLabel: string;
  description: string;
  /** Блок «Код» редактируется в многострочном поле. */
  multiline: boolean;
  /** Операция по значениям формы; `null` — менять нечего. */
  makeOp: (values: FormValues) => GraphOp | null;
  /** Имя в поле не считается занятым, пока оно совпадает с исходным (правка блока). */
  keepOwnNames: boolean;
};

function useSpec(target: InspectorTarget, model: FlowModel, blocks: PaletteBlock[] | undefined): Spec | null {
  return useMemo(() => {
    if (target.mode === "action") {
      return {
        kind: target.kind,
        heading: t.actionTitle,
        title: target.title,
        fields: target.fields,
        initial: defaultValues(target.fields),
        contextId: target.contextId,
        submitLabel: t.apply,
        description: target.description,
        multiline: false,
        keepOwnNames: false,
        makeOp: (values) => target.makeOp(cleanFields(target.fields, values)),
      };
    }
    if (target.mode === "insert") {
      const fields = fieldsForKind(target.kind, blocks, "insert");
      const title = blocks?.find((b) => b.kind === target.kind)?.title ?? target.kind;
      return {
        kind: target.kind,
        heading: t.insertTitle,
        title,
        fields,
        initial: fields ? defaultValues(fields) : {},
        contextId: target.point.parentId,
        submitLabel: t.insert,
        description: `Вставить блок «${title}»`,
        multiline: target.kind === "code",
        keepOwnNames: false,
        makeOp: (values) =>
          fields
            ? {
                op: "insert_node",
                parent_id: target.point.parentId,
                slot: target.point.slot,
                index: target.point.index,
                kind: target.kind,
                fields: cleanFields(fields, values),
              }
            : null,
      };
    }
    const block = model.blocks.get(target.nodeId);
    if (!block) return null;
    const fields = fieldsForKind(block.kind, blocks, "update");
    const initial = fields ? { ...defaultValues(fields), ...valuesOfBlock(block) } : {};
    const title = blocks?.find((b) => b.kind === block.kind)?.title ?? block.title;
    return {
      kind: block.kind,
      heading: t.editTitle,
      title,
      fields,
      initial,
      contextId: block.id,
      submitLabel: t.apply,
      description: `Изменить блок «${title}»`,
      multiline: block.kind === "code",
      keepOwnNames: true,
      makeOp: (values) => {
        if (!fields) return null;
        const changed = fields.filter((f) => String(values[f.name] ?? "") !== String(initial[f.name] ?? ""));
        return changed.length === 0
          ? null
          : { op: "update_node", node_id: block.id, fields: cleanFields(changed, values) };
      },
    };
  }, [target, model, blocks]);
}

/** Окрашенный unified diff: что изменится в коде. */
function DiffPreview({ text }: { text: string }) {
  const lines = text.split("\n").filter((line) => !line.startsWith("---") && !line.startsWith("+++"));
  return (
    <pre
      className="max-h-40 overflow-auto rounded-md border bg-background/60 p-2 font-mono text-[11px] leading-4"
      data-testid="inspector-preview"
    >
      {lines.map((line, index) => (
        <div
          key={index}
          className={cn(
            "whitespace-pre-wrap",
            line.startsWith("+") && "bg-node-return/15 text-node-return",
            line.startsWith("-") && "bg-severity-high/15 text-severity-high",
            line.startsWith("@@") && "text-muted-foreground",
          )}
        >
          {line || " "}
        </div>
      ))}
    </pre>
  );
}

type BodyProps = {
  projectId: number;
  versionId: number;
  spec: Spec;
  onClose: () => void;
};

function InspectorBody({ projectId, versionId, spec, onClose }: BodyProps) {
  const uid = useId();
  const { runOps, working } = useEditing(projectId);
  const fields = spec.fields;

  const form = useForm<FormValues>({
    resolver: zodResolver(buildSchema(fields ?? [])) as unknown as Resolver<FormValues>,
    defaultValues: spec.initial,
    mode: "onChange",
  });
  const values = useWatch({ control: form.control }) as FormValues;
  const snapshot = (fields ?? []).map((f) => String(values[f.name] ?? "")).join("\u0000");

  // Живая проверка выражений, типов и имён у backend (POST /validate/expression).
  const [remote, setRemote] = useState<Record<string, string>>({});
  const [checking, setChecking] = useState(false);
  const checked = useRef<Record<string, string>>({});
  useEffect(() => {
    if (!fields) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      const todo = fields.flatMap((field) => {
        const fragment = fragmentKind(field.type);
        const text = String(values[field.name] ?? "").trim();
        return fragment && text !== "" && checked.current[field.name] !== text ? [{ field, fragment, text }] : [];
      });
      setRemote((previous) => {
        const next = { ...previous };
        for (const field of fields) if (String(values[field.name] ?? "").trim() === "") delete next[field.name];
        return next;
      });
      if (todo.length === 0) return;
      setChecking(true);
      await Promise.all(
        todo.map(async ({ field, fragment, text }) => {
          const own = spec.keepOwnNames && text === String(spec.initial[field.name] ?? "");
          try {
            const result = await checkFragment(
              projectId,
              fragment,
              text,
              fragment === "identifier" && !own ? spec.contextId : null,
              controller.signal,
            );
            checked.current[field.name] = text;
            setRemote((previous) => ({
              ...previous,
              [field.name]: result.ok ? "" : (result.message ?? "Неверное значение"),
            }));
          } catch {
            // запрос отменён новым вводом: итог придёт со следующей проверкой
          }
        }),
      );
      if (!controller.signal.aborted) setChecking(false);
    }, CHECK_DELAY_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- проверка зависит от текста полей (snapshot)
  }, [snapshot, fields, projectId]);

  const remoteInvalid = Object.values(remote).some((message) => message !== "");
  const valid = form.formState.isValid && !remoteInvalid;
  const nothingToChange = fields !== null && spec.makeOp(values) === null;

  // Предпросмотр: сухой прогон операции показывает diff кода. Результат относится к тексту полей `key`.
  const [preview, setPreview] = useState<{ key: string; text?: string; error?: string } | null>(null);
  useEffect(() => {
    if (!valid || !fields) return;
    const current = spec.makeOp(form.getValues());
    if (!current) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const result = await validateOps(projectId, versionId, [current], controller.signal);
        if (controller.signal.aborted) return;
        setPreview(
          result.ok
            ? { key: snapshot, text: result.diff[0]?.unified ?? "" }
            : { key: snapshot, error: result.errors[0]?.message ?? t.previewFailed },
        );
      } catch {
        if (!controller.signal.aborted) setPreview({ key: snapshot, error: t.previewFailed });
      }
    }, PREVIEW_DELAY_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- пересчёт по тексту полей и допустимости формы
  }, [snapshot, valid, projectId, versionId]);
  const shownPreview = valid && preview?.key === snapshot ? preview : null;

  const submit = form.handleSubmit(async (current) => {
    const next = spec.makeOp(current);
    if (!next) {
      onClose();
      return;
    }
    const result = await runOps([next], spec.description, {
      onError: (error) => {
        const field = error instanceof ApiError ? error.field : null;
        if (field && fields?.some((f) => f.name === field)) {
          form.setError(field, { type: "server", message: error.message });
        } else {
          toast.error(ru.edit.failed, { description: error.message });
        }
      },
    });
    if (result) onClose();
  });

  const style = blockStyle(spec.kind);
  const Icon = style.icon;
  const submitting = form.formState.isSubmitting || working;

  return (
    <aside
      className="absolute top-2 right-2 bottom-2 z-10 flex w-80 max-w-[85%] flex-col overflow-hidden rounded-lg border bg-card shadow-xl"
      aria-label={spec.heading}
      data-testid="node-inspector"
      onKeyDown={(event) => {
        if (event.key === "Escape") onClose();
      }}
    >
      <header className="flex items-center gap-2 border-b px-3 py-2" style={{ background: colorVar(style.color, 0.12) }}>
        <Icon className="size-4 shrink-0" style={{ color: colorVar(style.color) }} aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="text-[10px] text-muted-foreground">{spec.heading}</p>
          <h3 className="truncate text-sm font-semibold">{spec.title}</h3>
        </div>
        <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label={t.cancel}>
          <X />
        </Button>
      </header>

      <ScrollArea className="min-h-0 flex-1">
        {fields === null ? (
          <p className="p-3 text-xs text-muted-foreground">{t.noFields}</p>
        ) : (
          <form onSubmit={submit} className="space-y-3 p-3" noValidate id={`${uid}-form`}>
            {fields.map((field) => {
              const id = `${uid}-${field.name}`;
              const error = form.formState.errors[field.name]?.message ?? (remote[field.name] || undefined);
              if (field.type === "boolean") {
                return (
                  <div key={field.name} className="flex items-center justify-between gap-2">
                    <Label htmlFor={id} className="text-xs">
                      {field.label}
                    </Label>
                    <Switch
                      id={id}
                      size="sm"
                      checked={Boolean(values[field.name])}
                      onCheckedChange={(checked) =>
                        form.setValue(field.name, checked, { shouldDirty: true, shouldValidate: true })
                      }
                    />
                  </div>
                );
              }
              return (
                <div key={field.name} className="space-y-1">
                  <Label htmlFor={id} className="text-xs">
                    {field.label}
                    {field.required && <span className="text-destructive"> *</span>}
                  </Label>
                  {field.type === "select" ? (
                    <select
                      id={id}
                      className="h-8 w-full rounded-md border border-input bg-transparent px-2 text-xs"
                      {...form.register(field.name)}
                    >
                      {field.options.map((option) => (
                        <option key={option} value={option} className="bg-card">
                          {option}
                        </option>
                      ))}
                    </select>
                  ) : spec.multiline ? (
                    <Textarea
                      id={id}
                      rows={5}
                      className="font-mono text-xs"
                      aria-invalid={Boolean(error)}
                      {...form.register(field.name)}
                    />
                  ) : (
                    <Input
                      id={id}
                      className="h-8 font-mono text-xs"
                      autoComplete="off"
                      spellCheck={false}
                      aria-invalid={Boolean(error)}
                      aria-describedby={error ? `${id}-error` : undefined}
                      {...form.register(field.name)}
                    />
                  )}
                  {error && (
                    <p id={`${id}-error`} role="alert" className="text-[11px] text-destructive">
                      {error}
                    </p>
                  )}
                </div>
              );
            })}

            <div className="space-y-1">
              <p className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
                {t.preview}
                {checking && <Loader2 className="size-3 animate-spin" aria-label={t.checking} />}
              </p>
              {shownPreview?.text !== undefined &&
                (shownPreview.text ? (
                  <DiffPreview text={shownPreview.text} />
                ) : (
                  <p className="text-[11px] text-muted-foreground">{t.previewEmpty}</p>
                ))}
              {shownPreview?.error && <p className="text-[11px] text-destructive">{shownPreview.error}</p>}
              {!shownPreview && <p className="text-[11px] text-muted-foreground">—</p>}
            </div>
          </form>
        )}
      </ScrollArea>

      {fields !== null && (
        <footer className="flex justify-end gap-2 border-t px-3 py-2">
          <Button variant="outline" size="sm" onClick={onClose} type="button">
            {t.cancel}
          </Button>
          <Button
            size="sm"
            type="submit"
            form={`${uid}-form`}
            disabled={submitting || checking || remoteInvalid || nothingToChange || !form.formState.isValid}
            data-testid="inspector-apply"
          >
            {submitting && <Loader2 className="animate-spin" />}
            {spec.submitLabel}
          </Button>
        </footer>
      )}
    </aside>
  );
}

function targetKey(target: InspectorTarget): string {
  if (target.mode === "update") return `update:${target.nodeId}`;
  if (target.mode === "action") return `action:${target.key}`;
  return `insert:${target.kind}:${target.point.parentId}:${target.point.slot}:${target.point.index}`;
}

type Props = { projectId: number; versionId: number; methodId: string; model: FlowModel };

/** Инспектор блока справа от холста: форма по виду блока, живая проверка и предпросмотр кода (docs/09 § 3.4). */
export function NodeInspector({ projectId, versionId, methodId, model }: Props) {
  const target = useEditorStore((s) => s.inspector);
  const openInspector = useEditorStore((s) => s.openInspector);
  // форма строится по описанию блоков из палитры: ждём его, иначе поля и значения по умолчанию были бы пустыми
  const palette = useQuery(paletteQuery(projectId));
  const spec = useSpec(target ?? { mode: "update", methodId, nodeId: "" }, model, palette.data?.blocks);
  if (!target || target.methodId !== methodId || palette.isLoading || !spec) return null;
  return (
    <InspectorBody
      key={targetKey(target)}
      projectId={projectId}
      versionId={versionId}
      spec={spec}
      onClose={() => openInspector(null)}
    />
  );
}
