import type { Point, Project, VerificationResult } from "./types"

const API = "/api"

export async function listProjects(): Promise<Project[]> {
  const response = await fetch(`${API}/projects`)
  if (!response.ok) throw new Error(await response.text())
  return response.json()
}

export async function uploadProject(file: File, resolution: number, name: string): Promise<Project> {
  const form = new FormData()
  form.append("map_file", file)
  form.append("resolution", String(resolution))
  form.append("name", name)
  const response = await fetch(`${API}/projects`, { method: "POST", body: form })
  if (!response.ok) throw new Error(await response.text())
  return response.json()
}

export async function saveProject(project: Project, revisionLabel?: string): Promise<Project> {
  const response = await fetch(`${API}/projects/${project.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      origin: project.origin,
      forbidden_zones: project.forbidden_zones,
      revision_label: revisionLabel ?? null,
    }),
  })
  if (!response.ok) throw new Error(await response.text())
  return response.json()
}

export async function rollbackProject(project: Project, revisionId: number): Promise<Project> {
  const response = await fetch(`${API}/projects/${project.id}/rollback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ revision_id: revisionId }),
  })
  if (!response.ok) throw new Error(await response.text())
  return response.json()
}

export async function verifyProject(
  project: Project,
  robot: { width: number; length: number },
  start: Point,
  goal: Point,
): Promise<{ project: Project; result: VerificationResult }> {
  const response = await fetch(`${API}/projects/${project.id}/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ robot, start, goal, orientations: 24, save: true }),
  })
  if (!response.ok) throw new Error(await response.text())
  return response.json()
}
