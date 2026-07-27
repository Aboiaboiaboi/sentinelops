import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createProject, deleteProject, getProject, listProjects } from '@/api/projects';
import { USE_FIXTURES, store } from '@/lib/fixtures';
import type { CreateProjectInput } from '@/types/project';

export const projectKeys = {
  all: ['projects'] as const,
  detail: (id: string) => ['projects', id] as const,
};

// Fixture mode is switched here rather than in the pages, so no page has to know
// it exists. See lib/fixtures.ts.
export function useProjects() {
  return useQuery({
    queryKey: projectKeys.all,
    queryFn: USE_FIXTURES ? store.listProjects : listProjects,
  });
}

export function useProject(id: string | undefined) {
  return useQuery({
    queryKey: projectKeys.detail(id ?? ''),
    queryFn: () => (USE_FIXTURES ? store.getProject(id as string) : getProject(id as string)),
    enabled: Boolean(id),
  });
}

export function useCreateProject() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateProjectInput) =>
      USE_FIXTURES ? store.createProject(input) : createProject(input),
    onSuccess: () => client.invalidateQueries({ queryKey: projectKeys.all }),
  });
}

export function useDeleteProject() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      USE_FIXTURES ? store.deleteProject(id) : deleteProject(id),
    onSuccess: () => client.invalidateQueries({ queryKey: projectKeys.all }),
  });
}
