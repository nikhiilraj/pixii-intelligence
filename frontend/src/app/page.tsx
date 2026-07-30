import { BackendUnreachable } from "@/components/backend-unreachable";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { API_BASE } from "@/lib/api";

type Health = {
  status: string;
  database: boolean;
  credentials: Record<string, boolean>;
};

/* ponytail: still a local fetch rather than getJson, because getJson collapses every
   failure to null and this page needs to distinguish "down" from "empty" — which is
   exactly what US-003 rebuilds. Left for that slice to migrate; the duplicate local
   API_BASE it also has to delete is already gone, since BackendUnreachable owns it now. */
async function fetchHealth(): Promise<Health | null> {
  try {
    const res = await fetch(`${API_BASE}/health`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as Health;
  } catch {
    return null;
  }
}

function Row({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div className="flex items-center justify-between border-b border-border py-3 last:border-0">
      <span className="text-body">{label}</span>
      <Badge variant={ok ? "success" : "danger"}>{ok ? "ok" : "down"}</Badge>
    </div>
  );
}

export default async function Home() {
  const health = await fetchHealth();

  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center px-6 py-16">
      <h1 className="text-title font-semibold tracking-tight">Pixii Intelligence</h1>
      <p className="mt-1 text-body text-muted">
        Content generation, improvement and analysis.
      </p>

      <section className="mt-10">
        <h2 className="text-caption font-medium uppercase tracking-widest text-muted">
          System status
        </h2>
        <Card className="mt-3 px-4 py-1">
          <Row label="Backend API" ok={health !== null} />
          <Row label="Database" ok={health?.database ?? false} />
          {health &&
            Object.entries(health.credentials).map(([name, present]) => (
              <Row key={name} label={`Credentials — ${name}`} ok={present} />
            ))}
        </Card>
        {!health && <BackendUnreachable className="mt-4" />}
      </section>
    </main>
  );
}
