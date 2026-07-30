import { ApiFailureNotice } from "@/components/api-failure";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { getJson } from "@/lib/api";

type Health = {
  status: string;
  database: boolean;
  credentials: Record<string, boolean>;
};

function Row({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div className="flex items-center justify-between border-b border-border py-3 last:border-0">
      <span className="text-body">{label}</span>
      <Badge variant={ok ? "success" : "danger"}>{ok ? "ok" : "down"}</Badge>
    </div>
  );
}

export default async function Home() {
  const health = await getJson<Health>("/health");

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
          {/* "Backend API: ok" now means the API answered, not merely that something was
              listening — a 500 from /health leaves this row red and names the status below,
              where the old local fetch and the old getJson both reported it as unreachable. */}
          <Row label="Backend API" ok={health.ok} />
          <Row label="Database" ok={health.ok && health.data.database} />
          {health.ok &&
            Object.entries(health.data.credentials).map(([name, present]) => (
              <Row key={name} label={`Credentials — ${name}`} ok={present} />
            ))}
        </Card>
        {!health.ok && <ApiFailureNotice failure={health} className="mt-4" />}
      </section>
    </main>
  );
}
