export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type Post = {
  id: number;
  zernio_id: string;
  platform: string;
  content: string;
  published_at: string | null;
  platform_post_url: string | null;
  account_username: string | null;
  media_type: string | null;
  thumbnail_url: string | null;
  local_media_path: string | null;
  is_external: boolean;
  excluded_from_extraction: boolean;
  impressions: number;
  reach: number;
  likes: number;
  comments: number;
  shares: number;
  saves: number;
  engagement_rate: number;
  engaged_actions: number;
};

export type TemplateKind = "hook" | "structure" | "visual";
// The body of work a template was read from — `Cohort` in backend/app/extraction.py.
// Sent as the plain string value; FastAPI coerces it into the StrEnum.
export type Cohort = "voice" | "inspiration";
export type TemplateStatus = "proposed" | "approved" | "retired";

export type Template = {
  id: number;
  family_id: string;
  version: number;
  kind: TemplateKind;
  name: string;
  status: TemplateStatus;
  body: Record<string, unknown>;
  slots: Record<string, unknown>[];
  provenance: string[];
  notes: string;
};

export type LineageEntry = { family: string; version: number; name: string } | null;

export type Draft = {
  id: number;
  idea: string;
  mode: string;
  hook_text: string;
  body_text: string;
  full_text: string;
  visual_values: Record<string, string>;
  visual_error: string | null;
  visual_png: string | null;
  zernio_post_id: string | null;
  lineage: { hook: LineageEntry; structure: LineageEntry; visual: LineageEntry };
};

export async function getJson<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}
