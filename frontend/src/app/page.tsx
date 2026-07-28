const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

type Health = {
  status: string;
  database: boolean;
  credentials: Record<string, boolean>;
};

async function fetchHealth(): Promise<Health | null> {
  try {
    const res = await fetch(`${API_BASE}/health`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as Health;
  } catch {
    return null;
  }
}

function Dot({ ok }: { ok: boolean }) {
  return (
    <span
      aria-label={ok ? "connected" : "unavailable"}
      className={`inline-block h-2.5 w-2.5 rounded-full ${ok ? "bg-emerald-500" : "bg-red-500"}`}
    />
  );
}

function Row({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div className="flex items-center justify-between border-b border-black/10 py-3 last:border-0 dark:border-white/10">
      <span className="text-sm">{label}</span>
      <span className="flex items-center gap-2 text-sm opacity-70">
        <Dot ok={ok} />
        {ok ? "ok" : "down"}
      </span>
    </div>
  );
}

export default async function Home() {
  const health = await fetchHealth();

  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">Pixii Intelligence</h1>
      <p className="mt-1 text-sm opacity-60">
        Content generation, improvement and analysis.
      </p>

      <section className="mt-10">
        <h2 className="text-xs font-medium uppercase tracking-widest opacity-50">
          System status
        </h2>
        <div className="mt-3">
          <Row label="Backend API" ok={health !== null} />
          <Row label="Database" ok={health?.database ?? false} />
          {health &&
            Object.entries(health.credentials).map(([name, present]) => (
              <Row key={name} label={`Credentials — ${name}`} ok={present} />
            ))}
        </div>
        {!health && (
          <p className="mt-4 text-sm text-red-600 dark:text-red-400">
            Backend unreachable at {API_BASE}. Start it with{" "}
            <code className="rounded bg-black/5 px-1 dark:bg-white/10">make api</code>.
          </p>
        )}
      </section>
    </main>
  );
}
