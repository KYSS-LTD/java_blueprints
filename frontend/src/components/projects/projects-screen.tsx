"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, FolderOpen, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { HealthCard } from "@/components/health-card";
import { ImportDialog } from "@/components/projects/import-dialog";
import { ThemeToggle } from "@/components/theme-toggle";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { MOCK_MODE } from "@/lib/api/client";
import { deleteProject } from "@/lib/api/projects";
import { projectsQuery } from "@/lib/api/queries";
import { ru } from "@/lib/i18n/ru";

const t = ru.home;

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" });
}

export function ProjectsScreen() {
  const [importing, setImporting] = useState(false);
  const queryClient = useQueryClient();
  const projects = useQuery(projectsQuery);
  const remove = useMutation({
    mutationFn: deleteProject,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["projects"] }),
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <main className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 p-8">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-3xl font-semibold tracking-tight">{ru.app.title}</h1>
          <p className="text-muted-foreground">{ru.app.subtitle}</p>
        </div>
        <div className="flex items-center gap-2">
          {MOCK_MODE && <Badge variant="secondary">{ru.preview.mockBadge}</Badge>}
          <Button asChild variant="outline" size="sm">
            <Link href="/preview">{t.preview}</Link>
          </Button>
          <Button onClick={() => setImporting(true)}>
            <Plus /> {t.newProject}
          </Button>
          <ThemeToggle />
        </div>
      </header>

      <section aria-labelledby="projects-title" className="space-y-3">
        <h2 id="projects-title" className="text-lg font-medium">
          {t.projects}
        </h2>

        {projects.isLoading && (
          <div className="grid gap-3 sm:grid-cols-2">
            <Skeleton className="h-28" />
            <Skeleton className="h-28" />
          </div>
        )}

        {projects.error && (
          <Alert variant="destructive">
            <CircleAlert />
            <AlertTitle>{ru.common.errorTitle}</AlertTitle>
            <AlertDescription>
              <p>{projects.error.message}</p>
              <p>{ru.common.backendHint}</p>
            </AlertDescription>
          </Alert>
        )}

        {projects.data && projects.data.projects.length === 0 && (
          <Card className="items-center border-dashed py-10 text-center">
            <CardContent className="flex flex-col items-center gap-3">
              <FolderOpen className="size-10 text-muted-foreground" aria-hidden />
              <p className="font-medium">{t.empty}</p>
              <p className="max-w-sm text-sm text-muted-foreground">{t.emptyHint}</p>
              <Button onClick={() => setImporting(true)}>
                <Plus /> {t.newProject}
              </Button>
            </CardContent>
          </Card>
        )}

        <ul className="grid gap-3 sm:grid-cols-2">
          {projects.data?.projects.map((project) => (
            <li key={project.id}>
              <Card className="relative h-full gap-3 transition-colors hover:border-primary/60">
                <CardHeader className="flex items-start justify-between gap-2">
                  <CardTitle className="min-w-0 truncate text-base">
                    <Link
                      href={`/projects/${project.id}`}
                      className="after:absolute after:inset-0 hover:underline"
                    >
                      {project.name}
                    </Link>
                  </CardTitle>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    className="relative z-10"
                    aria-label={`Удалить проект ${project.name}`}
                    disabled={remove.isPending}
                    onClick={() => {
                      if (window.confirm(`Удалить проект «${project.name}» со всеми версиями?`)) {
                        remove.mutate(project.id);
                      }
                    }}
                  >
                    <Trash2 />
                  </Button>
                </CardHeader>
                <CardContent className="space-y-1 text-sm text-muted-foreground">
                  <p>
                    {project.files_count} {t.files}
                    {project.main_class && (
                      <>
                        {" · "}
                        {t.mainClass}: <code>{project.main_class}</code>
                      </>
                    )}
                  </p>
                  <p>
                    {t.updated}: {formatDate(project.updated_at)}
                  </p>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      </section>

      <div className="mt-auto flex justify-center pt-6">
        <HealthCard />
      </div>

      <ImportDialog open={importing} onOpenChange={setImporting} />
    </main>
  );
}
