import { BackendUnreachable } from "@/components/backend-unreachable";
import { ReloadButton } from "@/components/reload-button";
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
 * **This is the error state, not a placeholder for one, and US-016 left it here rather than
 * moving it into `error.tsx`.** A route's `error.tsx` only ever sees a *throw*, and since
 * US-003 a failed read is a value — so no `error.tsx` in this app will ever be handed an
 * `ApiFailure` to report. Making a page throw in order to route failures there would also
 * throw away the message: Next replaces a server component's `error.message` with an opaque
 * digest in a production build, so the API's `detail` — which is human-written and the whole
 * point — would survive `pnpm dev` and vanish from a build. The words stay where the data is.
 * What US-016 added is the retry, which was the part genuinely missing.
 *
 * Same reasoning as backend-unreachable.tsx for the colour: the danger tint is on the card,
 * the words stay --text, because #D6455D fails AA as text.
 */
export function ApiFailureNotice({
  failure,
  className,
}: {
  failure: ApiFailure;
  className?: string;
}) {
  /* The retry sits *beside* BackendUnreachable rather than inside it: that component is one
     sentence about an address and a `make api` instruction, and it is shared with callers that
     are not reporting a failed read. Retrying a dead backend is still worth offering — `make
     api` in the other terminal is exactly the thing a reload would then pick up. */
  if (failure.kind === "network") {
    return (
      <div className={className}>
        <BackendUnreachable />
        <ReloadButton className="mt-3" />
      </div>
    );
  }

  return (
    <Card role="alert" className={cn("border-danger/40 bg-danger/10 text-body", className)}>
      <p className="font-medium">Request failed — HTTP {failure.status}</p>
      <p className="mt-1 text-muted">{failure.message}</p>
      <ReloadButton className="mt-3" />
    </Card>
  );
}
