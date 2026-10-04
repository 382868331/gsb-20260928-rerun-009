import type {
  MapState,
  PlanReplay,
  Point,
  Project,
  SavedPlan,
  ValidationResult,
  Version,
} from "./types";

async function jsonFetch(url: string, init?: RequestInit): Promise<any> {
  const response = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(data.detail ?? "请求失败");
  }
  return response.json();
}

export async function createProject(options: {
  name: string;
  resolution: number;
  file?: File;
}): Promise<Project> {
  const form = new FormData();
  form.set("name", options.name);
  form.set("resolution", String(options.resolution));
  if (options.file) form.set("file", options.file);
  const response = await fetch("/api/projects", { method: "POST", body: form });
  if (!response.ok) throw new Error((await response.json()).detail);
  return response.json();
}

export const getProject = (id: number) => jsonFetch(`/api/projects/${id}`);
export const saveProject = (id: number, state: MapState) =>
  jsonFetch(`/api/projects/${id}`, {
    method: "PUT",
    body: JSON.stringify({ state, note: "" }),
  });
export const validateProject = (
  id: number,
  state: MapState,
  keepGeometry = false,
  geometricPath?: Array<Point & { orientation: number }> | null,
): Promise<ValidationResult> =>
  jsonFetch(`/api/projects/${id}/validate`, {
    method: "POST",
    body: JSON.stringify({
      state,
      keep_geometry: keepGeometry,
      geometric_path: keepGeometry ? geometricPath ?? null : null,
    }),
  });

export const listPlans = (id: number): Promise<SavedPlan[]> =>
  jsonFetch(`/api/projects/${id}/plans`);
export const getPlan = (planId: number): Promise<SavedPlan> =>
  jsonFetch(`/api/plans/${planId}`);
export const replayPlan = (planId: number): Promise<PlanReplay> =>
  jsonFetch(`/api/plans/${planId}/replay`, { method: "POST" });
export const listVersions = (id: number): Promise<Version[]> =>
  jsonFetch(`/api/projects/${id}/versions`);
export const saveVersion = (id: number, name: string, state: MapState) =>
  jsonFetch(`/api/projects/${id}/versions`, {
    method: "POST",
    body: JSON.stringify({ name, state }),
  });
export async function restoreVersion(
  versionId: number,
): Promise<{ state: MapState; state_defaults?: string[] }> {
  return jsonFetch(`/api/versions/${versionId}/restore`, { method: "POST" });
}
