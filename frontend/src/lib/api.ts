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
  is_external: boolean;
  impressions: number;
  reach: number;
  likes: number;
  comments: number;
  shares: number;
  saves: number;
  engagement_rate: number;
  engaged_actions: number;
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
