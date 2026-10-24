"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileCode2, FileArchive, Loader2, Upload, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  createProjectFromSample,
  createProjectFromText,
  createProjectFromUpload,
  type ProjectCreated,
} from "@/lib/api/projects";
import { samplesQuery } from "@/lib/api/queries";
import { ru } from "@/lib/i18n/ru";
import { cn } from "@/lib/utils";

const t = ru.importDialog;

type Props = { open: boolean; onOpenChange: (open: boolean) => void };

/** Делит выбранные файлы на .java и архив; всё остальное отбрасывает с сообщением. */
export function splitUploads(files: File[]): { java: File[]; archive: File | null; rejected: File[] } {
  const java: File[] = [];
  const rejected: File[] = [];
  let archive: File | null = null;
  for (const file of files) {
    const name = file.name.toLowerCase();
    if (name.endsWith(".java")) java.push(file);
    else if (name.endsWith(".zip") && archive === null) archive = file;
    else rejected.push(file);
  }
  return { java, archive, rejected };
}

function projectNameFrom(files: File[]): string {
  const first = files[0]?.name ?? "project";
  return first.replace(/\.(java|zip)$/i, "") || "project";
}

export function ImportDialog({ open, onOpenChange }: Props) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState("files");
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [name, setName] = useState("");
  const [fileName, setFileName] = useState("Main.java");
  const [code, setCode] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const samples = useQuery({ ...samplesQuery, enabled: open });

  const finish = (project: ProjectCreated) => {
    void queryClient.invalidateQueries({ queryKey: ["projects"] });
    if (project.syntax_errors.length > 0) toast.warning(t.syntaxWarning);
    else toast.success(t.created);
    onOpenChange(false);
    router.push(`/projects/${project.id}`);
  };
  const fail = (error: Error) => toast.error(t.failed, { description: error.message });

  const upload = useMutation({
    mutationFn: () => {
      const { java, archive } = splitUploads(files);
      return createProjectFromUpload(name.trim() || projectNameFrom(files), java, archive);
    },
    onSuccess: finish,
    onError: fail,
  });
  const paste = useMutation({
    mutationFn: () =>
      createProjectFromText(name.trim() || fileName.replace(/\.java$/i, "") || "project", [
        { path: fileName.trim() || "Main.java", content: code },
      ]),
    onSuccess: finish,
    onError: fail,
  });
  const sample = useMutation({
    mutationFn: (id: string) => createProjectFromSample(id),
    onSuccess: finish,
    onError: fail,
  });

  const busy = upload.isPending || paste.isPending || sample.isPending;

  const addFiles = (incoming: File[]) => {
    const { rejected } = splitUploads(incoming);
    if (rejected.length > 0) toast.warning(t.unsupported, { description: rejected.map((f) => f.name).join(", ") });
    setFiles((current) => {
      const merged = [...current];
      for (const file of incoming) {
        if (!merged.some((f) => f.name === file.name && f.size === file.size)) merged.push(file);
      }
      // архив один: новый заменяет прежний
      const archives = merged.filter((f) => f.name.toLowerCase().endsWith(".zip"));
      return archives.length > 1
        ? merged.filter((f) => !archives.includes(f) || f === archives[archives.length - 1])
        : merged;
    });
  };

  const usable = splitUploads(files);
  const canUpload = usable.java.length > 0 || usable.archive !== null;

  return (
    <Dialog open={open} onOpenChange={(next) => !busy && onOpenChange(next)}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{t.title}</DialogTitle>
          <DialogDescription>{t.description}</DialogDescription>
        </DialogHeader>

        <Tabs value={tab} onValueChange={setTab}>
          <TabsList className="w-full">
            <TabsTrigger value="files">{t.tabs.files}</TabsTrigger>
            <TabsTrigger value="text">{t.tabs.text}</TabsTrigger>
            <TabsTrigger value="sample">{t.tabs.sample}</TabsTrigger>
          </TabsList>

          <TabsContent value="files" className="space-y-4">
            <div
              role="button"
              tabIndex={0}
              aria-label={t.dropzone}
              onClick={() => input.current?.click()}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") input.current?.click();
              }}
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                addFiles(Array.from(event.dataTransfer.files));
              }}
              className={cn(
                "flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed p-8 text-center transition-colors",
                dragging ? "border-primary bg-primary/10" : "border-border hover:border-primary/60",
              )}
            >
              <Upload className="size-8 text-muted-foreground" aria-hidden />
              <p className="text-sm font-medium">{t.dropzone}</p>
              <p className="text-xs text-muted-foreground">{t.dropzoneHint}</p>
              <input
                ref={input}
                type="file"
                multiple
                accept=".java,.zip"
                className="sr-only"
                data-testid="file-input"
                onChange={(event) => {
                  addFiles(Array.from(event.target.files ?? []));
                  event.target.value = "";
                }}
              />
            </div>

            {files.length > 0 && (
              <ul className="space-y-1" aria-label={t.selected}>
                {files.map((file) => (
                  <li
                    key={`${file.name}:${file.size}`}
                    className="flex items-center gap-2 rounded-md border bg-muted/40 px-3 py-1.5 text-sm"
                  >
                    {file.name.toLowerCase().endsWith(".zip") ? (
                      <FileArchive className="size-4 text-muted-foreground" aria-hidden />
                    ) : (
                      <FileCode2 className="size-4 text-muted-foreground" aria-hidden />
                    )}
                    <span className="flex-1 truncate">{file.name}</span>
                    <span className="text-xs text-muted-foreground">{Math.ceil(file.size / 1024)} КБ</span>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label={`${t.remove}: ${file.name}`}
                      onClick={() => setFiles((current) => current.filter((f) => f !== file))}
                    >
                      <X />
                    </Button>
                  </li>
                ))}
              </ul>
            )}

            <div className="space-y-2">
              <Label htmlFor="project-name">{t.name}</Label>
              <Input
                id="project-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder={files.length > 0 ? projectNameFrom(files) : t.namePlaceholder}
              />
            </div>
            <Button className="w-full" disabled={!canUpload || busy} onClick={() => upload.mutate()}>
              {upload.isPending ? <Loader2 className="animate-spin" /> : null}
              {upload.isPending ? t.creating : t.create}
            </Button>
          </TabsContent>

          <TabsContent value="text" className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="paste-name">{t.name}</Label>
                <Input
                  id="paste-name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder={t.namePlaceholder}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="paste-file">{t.fileName}</Label>
                <Input id="paste-file" value={fileName} onChange={(event) => setFileName(event.target.value)} />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="paste-code">{t.code}</Label>
              <Textarea
                id="paste-code"
                value={code}
                onChange={(event) => setCode(event.target.value)}
                placeholder={t.codePlaceholder}
                spellCheck={false}
                className="h-56 font-mono text-xs"
              />
            </div>
            <Button className="w-full" disabled={code.trim() === "" || busy} onClick={() => paste.mutate()}>
              {paste.isPending ? <Loader2 className="animate-spin" /> : null}
              {paste.isPending ? t.creating : t.create}
            </Button>
          </TabsContent>

          <TabsContent value="sample" className="space-y-2">
            {samples.isLoading && <Skeleton className="h-24 w-full" />}
            {samples.error && <p className="text-sm text-destructive">{samples.error.message}</p>}
            {samples.data?.samples.length === 0 && (
              <p className="text-sm text-muted-foreground">{t.noSamples}</p>
            )}
            <ul className="max-h-80 space-y-2 overflow-y-auto pr-1">
              {samples.data?.samples.map((item) => (
                <li key={item.id} className="flex items-start gap-3 rounded-lg border p-3">
                  <div className="min-w-0 flex-1 space-y-1">
                    <p className="text-sm font-medium">
                      {item.name}{" "}
                      <span className="font-normal text-muted-foreground">
                        · {item.lines} {t.sampleLines}
                      </span>
                    </p>
                    <p className="line-clamp-3 text-xs text-muted-foreground">{item.description}</p>
                  </div>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={busy}
                    onClick={() => sample.mutate(item.id)}
                  >
                    {sample.isPending && sample.variables === item.id ? (
                      <Loader2 className="animate-spin" />
                    ) : null}
                    {t.openSample}
                  </Button>
                </li>
              ))}
            </ul>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
