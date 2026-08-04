# Platform and API research for Pixii Intelligence

**Research date:** 2026-08-04  
**Evidence standard:** official product documentation, browser specifications, and first-party platform policies only.  
**Purpose:** implementation input for a scalable Pixii architecture. This is not a substitute for reviewing the providers' current commercial terms, quotas, data-processing terms, and platform approval requirements before production launch.

## Executive conclusions

1. Pixii can own the full approval, scheduling, and publishing experience. Zernio exposes API operations for drafts, immediate publishing, scheduling, updates, analytics, and delivery webhooks; users do not need to open Zernio for the normal workflow.
2. Treat Zernio as an outbound publishing adapter, not as Pixii's source of truth. Store Pixii's workflow state, audit log, logical idempotency key, Zernio post ID, per-platform result, and webhook receipt independently.
3. Teams notifications should deep-link into Pixii. Use a Teams bot or a Teams Workflow for production notifications; do not base a new system on legacy Microsoft 365 connectors. Keep publish/schedule authorization inside Pixii unless a separately secured bot action is deliberately implemented.
4. A generic capture extension is technically straightforward with Manifest V3, but LinkedIn's policies block the requested LinkedIn scraping/copying behavior. Build only for sites that permit capture; do not enable it for LinkedIn without an official permitted data-access path and written legal/security approval.
5. Put image generation and research behind provider-neutral ports. Model names, capabilities, availability, and billing change quickly. Every artifact should retain provider, model/deployment, prompt version, safety result, generation settings, cost/usage, and input-asset lineage.
6. Separate quick factual lookup from deep research. Deep research is asynchronous, expensive, and citation-bearing; it needs its own job state, evidence store, budgets, cancellation, and human review before evidence influences publishable content.

## 1. Zernio / GetLate publishing integration

Zernio is the current name and API surface; some documentation and compatibility headers still use the legacy Late/GetLate name. Its documented base URL is `https://zernio.com/api/v1`, authenticated with a bearer API key. Keys belong only in Pixii's server-side secret store, never the browser or extension. The [official quickstart](https://docs.zernio.com/) describes profiles, connected accounts, and a single post endpoint for draft, scheduled, and immediate modes.

### Supported Pixii workflow

- **Create a Pixii-approved draft in Zernio:** call `POST /v1/posts` with neither `scheduledFor` nor `publishNow`. Zernio saves it as a draft. The [create-post reference](https://docs.zernio.com/posts/create-post) is the canonical request schema.
- **Schedule from Pixii:** update the draft through `PUT /v1/posts/{postId}` with `isDraft: false`, `scheduledFor`, and `timezone`. Supplying only `scheduledFor` leaves an existing draft in draft status, so `isDraft: false` is essential. See [Update post](https://docs.zernio.com/posts/update-post).
- **Publish now from Pixii:** update the draft with `isDraft: false` and `publishNow: true`. Immediate create requests can also set `publishNow: true`, but retaining the draft-to-approved transition gives Pixii a cleaner audit trail.
- **Queue-based scheduling:** Zernio also supports `queuedFromProfile` and optional `queueId`; this is useful if scheduling policy is centrally configured in Zernio, but Pixii should still store the resolved schedule returned by Zernio.
- **Inspect and reconcile:** `GET /v1/posts` supports status filters including `draft`, `scheduled`, `published`, and `failed`, plus pagination and search. Published items expose platform URLs. See [List posts](https://docs.zernio.com/core/posts).
- **Retry failures:** Zernio documents a retry endpoint that retries only failed platform targets while skipping already-published targets. See [Error handling](https://docs.zernio.com/guides/error-handling).

This supports the desired UX: Teams notification -> Pixii approval screen -> `Schedule` or `Publish now` -> Zernio API -> status displayed back in Pixii.

The initial social-account OAuth connection and occasional reconnect remain necessary, but Pixii can initiate the Zernio connect/redirect flow from its own settings screen. Users should not need Zernio's dashboard during the normal editorial workflow.

### Duplicate prevention and idempotency

`POST /v1/posts` has two documented duplicate-protection layers:

1. An `x-request-id` identifies the logical request for an approximately five-minute window. A repeat with the same ID returns the original post in `existingPost` rather than creating another.
2. A content fingerprint over platform, account, content, and media URLs rejects duplicates to the same account within 24 hours with HTTP 409 and supplies `existingPostId`.

The official SDK generates an ID per call, but Pixii must generate and persist its own UUID per logical publish/schedule command so a retry after an ambiguous timeout reuses the same value. Never generate a new ID for each retry. Full behavior is documented under [Create post: Idempotency](https://docs.zernio.com/posts/create-post).

Recommended local guarantees:

- A unique database constraint on `(workspace_id, publication_command_id)`.
- A single outbox record for each externally visible command.
- Save the request fingerprint, `x-request-id`, Zernio post ID, response, and terminal outcome.
- Serialize commands for a draft (`approve`, `schedule`, `publish`, `cancel`) with optimistic version checks.
- Retry only timeouts, connection failures, 429, and documented transient 5xx responses; use exponential backoff with jitter and a cap.
- Treat a 409 content duplicate as a reconciliation case, not automatically as a fresh failure.

### Webhooks and state reconciliation

Zernio supports lifecycle events including `post.scheduled`, `post.published`, `post.failed`, `post.partial`, per-platform publish/failure events, and external-post create/update/delete events. Its webhook deliveries are **at least once**. Each event has a stable `payload.id` / `X-Zernio-Event-Id`; use that ID as a unique deduplication key. Signed deliveries use `X-Zernio-Signature`, which is an HMAC-SHA256 over the **raw request body**. Compare signatures in constant time before JSON parsing. See [Zernio webhooks](https://docs.zernio.com/webhooks).

Additional operational facts:

- Webhooks are automatically disabled after ten consecutive delivery failures.
- Delivery logs are retained for 30 days and can be queried by status, event, webhook, or event ID. See [Webhook delivery logs](https://docs.zernio.com/webhooks/get-webhook-logs).
- Webhook handling should acknowledge quickly, commit the verified raw event to an inbox table, and process asynchronously.
- Webhooks do not eliminate reconciliation. Run a periodic status poll for nonterminal posts and an alerting check for a disabled or stale webhook.

### Analytics constraints

`GET /v1/analytics` supports a single post or a paginated date range; a single-post request may return 202 while a sync is pending. For LinkedIn personal accounts, Zernio documents analytics only for posts published through Zernio; organization-page analytics can cover all posts. See [Get post analytics](https://docs.zernio.com/core/analytics). Therefore:

- Preserve Zernio and native platform IDs for every publication.
- Do not promise equivalent analytics coverage for browser-captured reference posts.
- Store immutable metric snapshots with `observed_at`, provider, and raw payload/version, then derive current metrics from snapshots.
- Treat missing/unavailable metrics differently from a true zero.

### Integration acceptance tests

1. Create a draft and verify it remains a draft.
2. Schedule that draft with `isDraft: false`; verify the returned time and timezone.
3. Repeat the exact command with the same `x-request-id`; verify only one Zernio post exists.
4. Simulate a client timeout after Zernio accepts the request; retry and reconcile to the original ID.
5. Verify signature rejection, replay deduplication, out-of-order delivery tolerance, `partial` handling, and the disabled-webhook monitor.
6. Publish to a test account, receive per-platform and aggregate terminal events, and compare them with a later API poll.
7. Request analytics until the record is ready, then prove snapshots remain immutable.

## 2. Microsoft Teams notifications and actions

Legacy Microsoft 365 / Office 365 Connectors, including connector-based incoming webhooks, are retired. Microsoft's final retirement schedule disabled them during 2026-05-18 through 2026-05-22. A new Pixii integration must not depend on an old connector URL. See Microsoft's [Office 365 Connectors retirement notice](https://devblogs.microsoft.com/microsoft365dev/retirement-of-office-365-connectors-within-microsoft-teams/).

### Recommended staged design

**Stage 1: Teams Workflow notification with a Pixii deep link.** Create a tenant-owned Power Automate / Teams Workflow whose authenticated HTTP trigger accepts a small notification command from Pixii. The workflow posts an Adaptive Card showing status, preview, proposed time, owner, and an `Action.OpenUrl` button to the canonical Pixii review URL. `Action.OpenUrl` is documented in the [Adaptive Cards schema](https://learn.microsoft.com/en-us/adaptive-cards/schema-explorer/action-open-url). This meets the stated requirement: users receive the daily notification in Teams and perform the security-sensitive approve/schedule/publish operation in Pixii.

Security and ownership requirements:

- Restrict the HTTP trigger to the Pixii service principal or specific identities in the tenant; do not use anonymous / `Anyone` access. Microsoft documents the authentication modes under [OAuth authentication for HTTP request triggers](https://learn.microsoft.com/en-us/power-automate/oauth-authentication).
- Assign at least two organization-controlled co-owners and monitor connection health so a workflow does not become orphaned when its creator leaves.
- Put no Zernio credential or publish capability in a card URL or field. The card contains an opaque notification/post ID and a normal HTTPS deep link only.
- Require normal Pixii SSO and RBAC after the link opens. A Teams notification is not proof of authorization.
- Keep payloads compact. The [Microsoft Teams connector reference](https://learn.microsoft.com/en-us/connectors/teams/) documents an approximate 28 KB Teams message limit and other connector/action constraints.

**Stage 2: optional Teams bot for in-card actions.** If future users must approve, schedule, or publish without opening Pixii, build a custom Teams notification bot with Microsoft Entra authentication and Adaptive Card `Action.Execute`. The app must be installed for the user/team, and the service must retain the conversation reference needed for proactive delivery; see [Proactive messages in Teams](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/send-proactive-messages) and [executing Adaptive Card actions](https://learn.microsoft.com/en-us/microsoftteams/platform/teams-sdk/in-depth-guides/adaptive-cards/executing-actions).

Bot actions require the same controls as Pixii's web UI:

- Resolve the authenticated Teams/Entra identity server-side and map it to a Pixii user and workspace role.
- Never trust a user ID, role, post version, scheduled time, or approval flag embedded as a hidden card field.
- Issue an opaque, one-time command ID; enforce expiry, intended tenant/user, allowed transition, and uniqueness.
- Re-read the draft and enforce an optimistic version check immediately before the side effect.
- Display a confirmation for `publish now`; audit actor, old/new state, command ID, IP/client metadata where appropriate, and Zernio result.

Power Automate also has a "post Adaptive Card and wait for a response" action, but Microsoft documents it as a waiting, effectively single-response flow where later submissions are ignored. That can work for a small one-approver MVP, but it is not the right long-term state store for multi-user publishing. See [Adaptive Cards in Power Automate](https://learn.microsoft.com/en-us/power-automate/overview-adaptive-cards). Pixii's database must remain authoritative regardless of delivery mechanism.

### Notification behavior to implement

- A scheduler creates a daily review digest and near-deadline reminders from Pixii's database, using each workspace's timezone and quiet-hour policy.
- A notification outbox makes delivery retriable and idempotent. Deduplicate by `(user_or_channel, notification_type, post_id, post_version, delivery_window)`.
- Cards link to a version-specific review page but the page always reloads current state; stale cards show "already handled" rather than repeating an action.
- Notify on `ready_for_review`, impending schedule deadline, publish success, partial publish, permanent failure, and disconnected Zernio account. Avoid a message for every internal generation step.
- Store delivery status and provider message/conversation IDs, not the entire Teams payload indefinitely unless required for support.

## 3. Chrome / Edge Manifest V3 capture extension

### Technically sound MV3 pattern for sites that permit capture

For an allowed website, prefer an explicit click-to-capture design:

1. Declare Manifest V3, `activeTab`, `scripting`, and `storage`; avoid broad permanent host access. [`activeTab`](https://developer.chrome.com/docs/extensions/develop/concepts/activeTab) grants temporary access only after a user gesture.
2. When the user presses **Save to Pixii**, inject a small packaged content script into the active tab. Extract only the user-selected/confirmed fields, not the whole browsing session.
3. Send a structured message from the content script to the extension service worker. Validate the message shape and sender before processing.
4. The service worker sends the capture over HTTPS to a narrowly scoped Pixii ingestion endpoint. Persist upload state in `chrome.storage`, because MV3 workers can terminate when idle.
5. Keep all executable JavaScript in the signed extension package; MV3 prohibits remotely hosted code.
6. Request only the Pixii API origin as a host permission if needed. Never embed a permanent API key. Use [`chrome.identity`](https://developer.chrome.com/docs/extensions/reference/api/identity) with Pixii's OAuth/OIDC authorization-code flow and PKCE, issue a short-lived audience-restricted access token, and avoid storing a long-lived refresh secret where possible. The backend must validate issuer, audience, expiry, subject, workspace access, and scopes; it must not treat the extension origin as authentication.
7. The API authenticates the user and workspace, enforces rate/size/type limits, assigns a server-generated capture ID, scans any media, and records provenance and consent.

Official implementation references: [Chrome content scripts](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts), [extension message passing](https://developer.chrome.com/docs/extensions/develop/concepts/messaging), [MV3 service-worker lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle), [MV3 and remotely hosted code](https://developer.chrome.com/docs/extensions/develop/migrate/what-is-mv3), and [Chrome Web Store user-data/minimum-permission policy](https://developer.chrome.com/docs/webstore/program-policies/user-data-faq). Edge documents broad Chrome extension API/manifest compatibility in its [porting guide](https://learn.microsoft.com/en-us/microsoft-edge/extensions-chromium/developer-guide/port-chrome-extension).

### LinkedIn is a policy blocker for the requested capture behavior

A LinkedIn DOM-scraping extension should **not** be built or distributed. LinkedIn explicitly prohibits browser plugins/extensions that scrape or copy the service, modify its appearance, or automate activity, and says accounts can be restricted or closed. See [LinkedIn prohibited software and extensions](https://www.linkedin.com/help/linkedin/answer/a1341387/prohibited-software-and-extensions?lang=en) and section 8.2 of the [LinkedIn User Agreement](https://www.linkedin.com/legal/user-agreement).

LinkedIn's [API Terms of Use](https://www.linkedin.com/legal/l/api-terms-of-use) also restrict an application from accessing or storing "Non-Official Content" obtained through scraping, including material provided indirectly by a customer or third party. Its official [Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api?view=li-lms-2026-04) covers posts authored by the authenticated member or an organization the member administers; personal retrieval requires restricted `r_member_social` approval. There is no documented general API for arbitrary posts a user encounters in the LinkedIn feed.

This is a product constraint, not merely a technical inconvenience. Do not fetch LinkedIn pages server-side, read arbitrary feed DOM/text/images, automate screenshots, reuse LinkedIn cookies, or attempt anti-bot workarounds.

### Compliant alternatives

- Import the user's own/authorized posts through Zernio external sync; Zernio documents external/native post events and post listing.
- Apply for LinkedIn's restricted official API only if owned-member or administered-organization posts are a product requirement and the use case qualifies.
- Let a user add a **reference URL plus their own notes** in Pixii. Do not have Pixii fetch or scrape the LinkedIn URL.
- Accept a file or text only when the user confirms they own it, have permission, or it is licensed for the intended use. Record source URL, contributor, owner/author, capture time, consent/license basis, permitted uses, retention, and deletion status.
- A generic "Save to Pixii" extension can be built for websites whose terms allow it, but explicitly block `linkedin.com` and other disallowed domains by policy/configuration.

Record this decision in an ADR before implementation. Any later proposal to enable LinkedIn capture needs written legal/security approval and an official permitted data-access path, not only a technical proof of concept.

## 4. Google Gemini image generation (Nano Banana family)

"Nano Banana" is Google's product name for Gemini native image generation, not one fixed model. As of the research date, Google's [image-generation guide](https://ai.google.dev/gemini-api/docs/image-generation) lists:

| Product name | API model ID | Best fit documented by Google |
|---|---|---|
| Nano Banana 2 Lite | `gemini-3.1-flash-lite-image` | Lowest latency/cost and scale; not optimized for multiple references or sequential multi-turn editing |
| Nano Banana 2 | `gemini-3.1-flash-image` | Recommended general-purpose balance; up to 4K, stronger text rendering, multi-reference consistency |
| Nano Banana Pro | `gemini-3-pro-image` | Highest-end professional assets, complex instructions, localization, brand consistency, precision control |
| Nano Banana (legacy) | `gemini-2.5-flash-image` | Legacy high-volume/low-latency model; Google recommends migrating |

Google recommends `gemini-3.1-flash-image` as the general workhorse and Pro for the hardest professional assets. Current Gemini 3 image models support 1K, 2K, and 4K output (with model-specific differences), conversational text-plus-image generation/editing, multiple reference images, and improved text rendering. The guide also documents these important constraints:

- All generated images include a SynthID watermark.
- Output count may not exactly match the requested count.
- Reference-image fidelity and character/object limits vary by model.
- For text inside an image, Google recommends generating/validating the text first and then asking the image model to render it.
- Image generation is subject to safety filtering and supported-language guidance.
- Batch generation trades higher limits for asynchronous completion that can take up to 24 hours.
- Google says Imagen models are deprecated and scheduled for shutdown on 2026-08-17; new work should target the Nano Banana models.

Gemini image models can also use Google Search grounding for real-time factual imagery where supported. That is valuable for charts or topical visuals, but factual charts should still be regenerated deterministically from verified structured data whenever possible. Search grounding has separate billing, attribution, and data-processing implications; Google's [Grounding with Google Search](https://ai.google.dev/gemini-api/docs/google-search) documentation returns source annotations and search steps, while its [zero-data-retention documentation](https://ai.google.dev/gemini-api/docs/zdr) states that prompts, context, and output for Grounding with Google Search are stored for 30 days to provide grounded results and search suggestions.

### Recommended Pixii use

- Use `gemini-3.1-flash-image` for default generation and iterative edits.
- Route premium, approved brand campaigns to `gemini-3-pro-image` only when evaluation data justifies the cost and latency.
- Use Lite for high-volume thumbnails or cheap exploration, not final assets that depend on several brand references.
- Pin an explicit model ID and record it on the artifact; do not rely on a marketing name.
- Pass approved brand references from object storage, not arbitrary URLs fetched by the model.
- Require a deterministic post-generation quality gate: dimensions/aspect ratio, file size, OCR/spelling, logo placement, contrast/readability, unsafe-content result, and human approval.

## 5. Azure OpenAI image generation

Microsoft's current [Azure OpenAI image-generation guide](https://learn.microsoft.com/en-ca/azure/foundry/openai/how-to/dall-e?view=foundry) lists `gpt-image-2` as generally available and `gpt-image-1.5`, `gpt-image-1`, and `gpt-image-1-mini` as limited-access preview offerings. It also states that DALL-E 3 retired on 2026-03-04 and existing deployments no longer function. Do not build new Pixii code around DALL-E.

Documented model trade-offs:

- `gpt-image-2`: production default candidate; 4K support, broad aspect ratios, improved editing, high fidelity, and GA status.
- `gpt-image-1.5`: strong realism/instruction following and improved efficiency over `gpt-image-1`, but limited-access preview.
- `gpt-image-1`: high-fidelity baseline, limited-access preview.
- `gpt-image-1-mini`: cheaper/faster bulk exploration and no dedicated face-preservation capability, limited-access preview.

The Azure guide documents text-plus-image inputs, 1–10 outputs per request, image editing/inpainting, `low`/`medium`/`high` quality, PNG/JPEG output, transparency, and partial-image streaming. `gpt-image-2` accepts arbitrary dimensions subject to both edges being multiples of 16, a 3:1 maximum aspect ratio, a 3840-pixel long edge, and a bounded total pixel count. The API returns base64 image data, so the backend must decode, scan, and upload results to object storage rather than sending large base64 payloads through the frontend.

Azure recommends Microsoft Entra ID with managed identity for workloads running in Azure. If API keys are used, store them in Key Vault and rotate them; never expose them to the web or extension. Azure applies input/output content moderation and abuse monitoring. Region/model availability and quota are deployment-specific and must be checked before selecting a production region; see [Azure model region availability](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure-region-availability) and [Azure OpenAI quotas and limits](https://learn.microsoft.com/en-us/azure/foundry/openai/quotas-limits).

### Recommended Pixii use

If the company already has Azure credits and a suitable region, start the production evaluation with `gpt-image-2`. Keep Gemini as a second provider for A/B quality testing and provider resilience. Do not assume either provider is universally better: build a fixed evaluation set of content briefs and score instruction adherence, text accuracy, brand consistency, edit locality, human preference, latency, rejection rate, and cost.

The adapter contract should accept a provider-neutral request such as `brief`, `reference_asset_ids`, `aspect_ratio`, `quality_tier`, `background`, `seed_if_supported`, and `safety_context`, and return an immutable artifact record with provider-native details. Unsupported settings must fail validation rather than be silently ignored.

## 6. Web search and deep-research APIs

### Provider capabilities from official documentation

**OpenAI.** The [Responses API web-search guide](https://developers.openai.com/api/docs/guides/tools-web-search) documents `web_search` for current information with URL citations, optional controls, and model-managed multi-step searching. It requires citations shown to end users to be clearly visible and clickable. For long research, current guidance is a GPT-5 reasoning model with high effort, `background: true`, and optionally a larger `return_token_budget`; unlimited search output can materially increase latency and cost. The separate [deep-research guide](https://developers.openai.com/api/docs/guides/deep-research) documents research over web search, file search/vector stores, remote MCP sources, and code interpreter. Because model recommendations and legacy tool names change, select models from configuration and revalidate the catalog before rollout.

**Google.** [Grounding with Google Search](https://ai.google.dev/gemini-api/docs/google-search) lets the model decide and execute one or more searches, returning structured source annotations and search-call steps. It can combine search with URL context on supported models. Google has UI/attribution requirements for search suggestions and bills search differently across model generations; preserve the returned grounding metadata rather than flattening it to plain prose.

**Microsoft Azure.** Foundry Agent Service's [web-search tool](https://learn.microsoft.com/en-ca/azure/ai-foundry/agents/how-to/tools/web-search?view=foundry) grounds model output with public web information and inline citations and supports longer research using a deep-research model. Important compliance caveat: Microsoft states that Grounding with Bing incurs extra cost, data transfers occur outside Azure compliance/geographic boundaries, and the Microsoft Data Protection Addendum does not apply to data sent to that grounding service. The related [Bing grounding guide](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/bing-tools) also imposes display requirements and says raw search content is not exposed to developers/end users. Security and legal review is required before sending confidential briefs or customer information into search queries.

### Provider-neutral research architecture

Do not invoke web search invisibly inside the final copy-writing call. Model research as a first-class, reviewable artifact:

```text
research request -> scoped plan -> search/fetch loop -> evidence normalization
                 -> claim/citation map -> research brief -> human review
                 -> content-generation context
```

Recommended components and contracts:

- `ResearchJob`: question, audience, freshness needs, allowed/blocked domains, locale, requested depth, deadline, cost/call/token budget, and requester.
- `SearchProvider` port: query with domain/freshness/locale controls; return provider-native call ID, normalized sources, usage, and raw metadata pointer.
- `EvidenceItem`: canonical URL, title, publisher, retrieved time, publication time when known, content hash, bounded excerpt or facts, rights/retention metadata, and provider provenance.
- `Claim`: normalized statement linked to one or more evidence items with exact citation spans and a verification status.
- `ResearchBrief`: answer, uncertainty, conflicts, missing evidence, and citation map; versioned and immutable after approval.
- `ResearchPolicy`: primary-source preference, domain allow/deny lists, maximum age, minimum independent sources for material claims, confidential-query redaction, and prohibited topics/actions.

Operational requirements:

- Run deep research asynchronously with explicit states (`queued`, `running`, `needs_review`, `completed`, `failed`, `cancelled`, `expired`) and cancellation.
- Enforce per-job and per-workspace budgets and hard limits on queries, pages, tokens, elapsed time, and retries.
- Validate every final citation: URL present, claim span mapped, source fetched/returned in the run, and no unsupported claim added during copy generation.
- Preserve provider-returned citations and required attribution UI. Never fabricate a citation from a model's prose.
- Protect any independent fetcher from SSRF: permit only HTTPS, resolve and block private/link-local addresses on every redirect, limit redirects/size/time/MIME types, scan documents, and isolate parsing.
- Treat web content as untrusted prompt-injection input. Delimit it as evidence, strip active content, never grant it tool authority, and do not follow instructions found inside sources.
- Cache only where provider terms permit; record retention/deletion rules per provider and workspace.
- Keep search grounded facts separate from creative suggestions. Content cannot move to `approved` while material factual claims are uncited or disputed.

### Suggested staged rollout

1. **Quick research:** one provider, bounded web search, citations required, 30–60 second budget.
2. **Deep research:** asynchronous multi-step jobs, primary-source policy, evidence/claim review UI, cancellation and cost budgets.
3. **Internal knowledge:** add approved corpus/document retrieval behind the same evidence interface; enforce tenant isolation.
4. **Provider resilience:** add a second adapter and run contract/evaluation tests; do not automatically combine providers until citation normalization is proven.

## Production due-diligence checklist

- Confirm Zernio plan entitlements, rate limits, webhook limits, LinkedIn account type, and test-account access.
- Confirm Teams tenant policy permits Workflows or custom apps/bots and identify the owner/co-owners of each workflow.
- Obtain legal/security approval for the exact LinkedIn capture behavior and retained fields before distributing an extension.
- Confirm Google/Azure model and region availability, data residency, retention, safety review, quota, and credits.
- Review every provider's current terms immediately before implementation; this document is a dated snapshot.
- Build contract tests against sandbox/test accounts and nightly drift checks for documented capabilities that the product depends on.
