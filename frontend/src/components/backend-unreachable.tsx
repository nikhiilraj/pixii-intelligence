import { Card } from "@/components/ui/card";
import { API_BASE } from "@/lib/api";
import { cn } from "@/lib/cn";

/* One copy of a block that existed five times: the four page.tsx server wrappers
   (`posts/`, `studio/`, `templates/`, `scoreboard/`) plus the home page. Four of the five
   said only "Backend unreachable"; home's version named the address it tried. That one is
   the useful wording, so it is the one that survives — the address is the whole diagnostic
   when NEXT_PUBLIC_API_BASE is set to something unexpected.

   Not in components/ui/: it is an app-specific state, not a vendored primitive.

   The message is deliberately NOT `text-danger`. #D6455D measures 4.12:1 on --bg and fails
   AA as text (see badge.tsx — the PRD is wrong that the status colours are all AA). The
   danger colour tints the card and its border; the words stay --text at 14:1. */
export function BackendUnreachable({ className }: { className?: string }) {
  return (
    <Card
      role="status"
      className={cn("border-danger/40 bg-danger/10 text-body", className)}
    >
      Backend unreachable at <code className="font-mono">{API_BASE}</code>. Start it with{" "}
      <code className="rounded-input bg-surface-2 px-1 font-mono">make api</code>.
    </Card>
  );
}
