import { BackendUnreachable } from "@/components/backend-unreachable";
import { Card } from "@/components/ui/card";
import type { ApiFailure } from "@/lib/api";
import { cn } from "@/lib/cn";

/* What a failed read renders instead of an empty page.
 *
 * Before US-003 every read failure was `null`, so all five read pages showed
 * BackendUnreachable — correct wording for a dead server and a lie for a live one that
 * answered 500. Now the two are distinguishable, so they say different things:
 * unreachable keeps the `make api` instruction, and an HTTP failure reports the status and
 * the API's own message.
 *
 * Deliberately minimal. US-016 owns route-level error.tsx boundaries, retry and the empty
 * states; this is the honest placeholder those get built on, and the copy here is what
 * should move into them. Same reasoning as backend-unreachable.tsx for the colour: the
 * danger tint is on the card, the words stay --text, because #D6455D fails AA as text.
 */
export function ApiFailureNotice({
  failure,
  className,
}: {
  failure: ApiFailure;
  className?: string;
}) {
  if (failure.kind === "network") return <BackendUnreachable className={className} />;

  return (
    <Card role="alert" className={cn("border-danger/40 bg-danger/10 text-body", className)}>
      <p className="font-medium">Request failed — HTTP {failure.status}</p>
      <p className="mt-1 text-muted">{failure.message}</p>
    </Card>
  );
}
