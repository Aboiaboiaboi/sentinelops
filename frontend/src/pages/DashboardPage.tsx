import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { useCreateProject, useDeleteProject, useProjects } from '@/hooks/useProjects';

export default function DashboardPage() {
  const { data: projects, isPending, isError, error } = useProjects();
  const createProject = useCreateProject();
  const deleteProject = useDeleteProject();

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState('');
  const [repositoryUrl, setRepositoryUrl] = useState('');

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
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Projects</h1>
          <p className="text-sm text-muted-foreground">
            Repositories tracked for production readiness.
          </p>
        </div>
        <Button onClick={() => setShowForm((open) => !open)}>
          <Plus /> Add project
        </Button>
      </div>

      {showForm && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">New project</CardTitle>
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
                <Label htmlFor="repo">Repository URL</Label>
                <Input
                  id="repo"
                  type="url"
                  required
                  placeholder="https://github.com/owner/repo"
                  value={repositoryUrl}
                  onChange={(e) => setRepositoryUrl(e.target.value)}
                />
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
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            No projects yet. Add a repository to run your first scan.
          </CardContent>
        </Card>
      )}

      {projects && projects.length > 0 && (
        <ul className="space-y-3">
          {projects.map((project) => (
            <li key={project.id}>
              <Card>
                <CardContent className="flex items-center justify-between gap-4 py-4">
                  <div className="min-w-0">
                    <Link
                      to={`/projects/${project.id}`}
                      className="font-medium hover:underline"
                    >
                      {project.name}
                    </Link>
                    <p className="truncate text-sm text-muted-foreground">
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
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
