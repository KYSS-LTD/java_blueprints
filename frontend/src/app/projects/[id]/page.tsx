import { notFound } from "next/navigation";

import { Workspace } from "@/components/workspace/workspace";

export default async function ProjectPage({ params }: PageProps<"/projects/[id]">) {
  const { id } = await params;
  const projectId = Number(id);
  if (!Number.isInteger(projectId) || projectId <= 0) notFound();
  return <Workspace projectId={projectId} />;
}
