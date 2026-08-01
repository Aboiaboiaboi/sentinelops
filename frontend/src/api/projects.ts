import { request } from './client';
import type { CreateProjectInput, Project, UpdateProjectInput } from '@/types/project';

/**
 * Note this deliberately does not include each project's latest scan or score —
 * the backend spec keeps them separate, so the frontend fetches scans on demand.
 */
export function listProjects(): Promise<Project[]> {
  return request<Project[]>('/projects');
}

export function getProject(id: string): Promise<Project> {
  return request<Project>(`/projects/${id}`);
}

export function createProject(input: CreateProjectInput): Promise<Project> {
  return request<Project>('/projects', { method: 'POST', body: input });
}

export function updateProject(id: string, input: UpdateProjectInput): Promise<Project> {
  return request<Project>(`/projects/${id}`, { method: 'PATCH', body: input });
}

export function deleteProject(id: string): Promise<void> {
  return request<void>(`/projects/${id}`, { method: 'DELETE' });
}
