import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { PageHeader } from '@/components/PageHeader';
import { RowList, Row } from '@/components/Rows';
import { GitHubConnection } from '@/components/GitHubConnection';
import { RepositoryPicker } from '@/components/RepositoryPicker';
import { useCreateProject, useDeleteProject, useProjects } from '@/hooks/useProjects';
import type { GitHubRepository } from '@/types/github';

export default function DashboardPage() {
  const { data: projects, isPending, isError, error } = useProjects();
  const createProject = useCreateProject();
  const deleteProject = useDeleteProject();

  const [showForm, setShowForm] = useState(false);
  const [showPicker, setShowPicker] = useState(false);
  const [name, setName] = useState('');
  const [repositoryUrl, setRepositoryUrl] = useState('');

  function handlePick(repository: GitHubRepository) {
    setRepositoryUrl(repository.url);
    // The repo name is a better default than an empty box, and still editable.
    if (!name) setName(repository.full_name.split('/')[1]);
    setShowPicker(false);
  }

  function handleCreate(event: FormEvent) {
    event.preventDefault();
    createProject.mutate(
      { name, repository_url: repositoryUrl },
      {
        onSuccess: () => {
          setName('');
          setRepositoryUrl('');
          setShowForm(false);
        },
      },
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        caption="Repositories tracked for production readiness."
        actions={
          <Button onClick={() => setShowForm((open) => !open)}>
            <Plus /> Add project
          </Button>
        }
      >
        Projects
      </PageHeader>

      <GitHubConnection />

      {showForm && (
        <Card className="border-border/60 bg-card/60">
          <CardHeader>
            <CardTitle className="font-display text-base tracking-tight">New project</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleCreate} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="name">Name</Label>
                <Input
                  id="name"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <Label htmlFor="repo">Repository URL</Label>
                  <Button
                    type="button"
                    variant="link"
                    className="h-auto p-0 text-xs"
                    onClick={() => setShowPicker((open) => !open)}
                  >
                    {showPicker ? 'Enter a URL instead' : 'Choose from GitHub'}
                  </Button>
                </div>
                <Input
                  id="repo"
                  type="url"
                  required
                  placeholder="https://github.com/owner/repo"
                  value={repositoryUrl}
                  onChange={(e) => setRepositoryUrl(e.target.value)}
                />
                <RepositoryPicker open={showPicker} onPick={handlePick} />
              </div>

              {createProject.isError && (
                <p role="alert" className="text-sm text-destructive">
                  {createProject.error.message}
                </p>
              )}

              <div className="flex gap-2">
                <Button type="submit" disabled={createProject.isPending}>
                  {createProject.isPending ? 'Adding…' : 'Add project'}
                </Button>
                <Button type="button" variant="ghost" onClick={() => setShowForm(false)}>
                  Cancel
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      {isPending && (
        <div className="space-y-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      )}

      {isError && (
        <Alert variant="destructive">
          <AlertDescription>{error.message}</AlertDescription>
        </Alert>
      )}

      {projects && projects.length === 0 && (
        <div className="rounded-lg border border-dashed border-border/60 py-10 text-center text-sm text-muted-foreground">
          No projects yet. Add a repository to run your first scan.
        </div>
      )}

      {projects && projects.length > 0 && (
        <RowList as="ul">
          {projects.map((project) => (
            <Row key={project.id} as="li" className="py-4">
              <div className="min-w-0">
                <Link
                  to={`/projects/${project.id}`}
                  className="font-display font-medium tracking-tight hover:underline"
                >
                  {project.name}
                </Link>
                <p className="truncate font-mono text-xs text-muted-foreground">
                  {project.repository_url}
                </p>
                {project.framework && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    Detected: {project.framework}
                  </p>
                )}
              </div>

              <Button
                variant="ghost"
                size="icon"
                aria-label={`Delete ${project.name}`}
                disabled={deleteProject.isPending}
                onClick={() => deleteProject.mutate(project.id)}
              >
                <Trash2 className="text-muted-foreground" />
              </Button>
            </Row>
          ))}
        </RowList>
      )}
    </div>
  );
}
