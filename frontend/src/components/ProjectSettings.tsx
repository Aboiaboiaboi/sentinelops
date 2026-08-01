import { useState, type FormEvent } from 'react';
import { Lock, Pencil } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useUpdateProject } from '@/hooks/useProjects';
import type { Project } from '@/types/project';

/**
 * Editing a project's name, and its URL while that is still allowed.
 *
 * The URL field is disabled with a reason rather than accepting input that
 * would be rejected on save — the server answers 409, but discovering that
 * after typing is a worse experience than being told up front.
 */
export function ProjectSettings({ project }: { project: Project }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(project.name);
  const [repositoryUrl, setRepositoryUrl] = useState(project.repository_url);
  const update = useUpdateProject(project.id);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    update.mutate(
      // Only what actually changed, so a rename never carries the URL along
      // and trips the lock on a project that has been scanned.
      {
        ...(name !== project.name ? { name } : {}),
        ...(repositoryUrl !== project.repository_url ? { repository_url: repositoryUrl } : {}),
      },
      { onSuccess: () => setOpen(false) },
    );
  }

  if (!open) {
    return (
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <Pencil /> Edit
      </Button>
    );
  }

  return (
    <Card className="w-full">
      <CardHeader>
        <CardTitle className="text-base">Project settings</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="project-name">Name</Label>
            <Input
              id="project-name"
              required
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="project-url">Repository URL</Label>
            <Input
              id="project-url"
              type="url"
              required
              value={repositoryUrl}
              disabled={!project.repository_url_editable}
              aria-describedby={
                project.repository_url_editable ? undefined : 'project-url-locked'
              }
              onChange={(event) => setRepositoryUrl(event.target.value)}
            />
            {!project.repository_url_editable && (
              <p
                id="project-url-locked"
                className="flex items-start gap-1.5 text-xs text-muted-foreground"
              >
                <Lock className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
                This project has been scanned, so its repository is fixed — changing it would
                leave the scan history describing a different one. Create a new project instead.
              </p>
            )}
          </div>

          {update.isError && (
            <p role="alert" className="text-sm text-destructive">
              {update.error.message}
            </p>
          )}

          <div className="flex gap-2">
            <Button type="submit" disabled={update.isPending}>
              {update.isPending ? 'Saving…' : 'Save'}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setName(project.name);
                setRepositoryUrl(project.repository_url);
                setOpen(false);
              }}
            >
              Cancel
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
