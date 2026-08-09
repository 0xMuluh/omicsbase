export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      "X-Tenant-ID": "default_tenant",
      "X-User-ID": "default_user",
      ...options?.headers,
    },
    ...options,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`API error ${res.status}: ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export async function fetchArtifactBlob(path: string): Promise<Blob> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      "X-Tenant-ID": "default_tenant",
      "X-User-ID": "default_user",
    },
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`API error ${res.status}: ${detail}`);
  }
  return res.blob();
}
