# Pending: Facebook Login / Page Linking / Messenger Integration

> Written 2026-08-17, immediately before resetting
> `feature/facebook-multi-workspace-messenger` to its last commit
> (`17ada9e`) and discarding everything below. This records what was
> built and what was still missing, so the work can be picked back up
> without starting from zero — but note the code itself is gone; this
> is a description to rebuild from, not a patch to reapply.

## What's still on the branch (committed, kept)

Commits `07c021b` and `17ada9e` — the multi-workspace data-model
foundation: `Workspace`, `WorkspaceMembership`, `UserCapability`,
`BusinessProfile`, plus `marketplace/workspaces.py` (session-based
active-workspace resolution: `get_active_workspace` /
`set_active_workspace`, idempotent workspace creation). Migrations
`0034`–`0040` on this branch (workspace + backfill) still exist under
their original numbers here; a Suppliers-feature branch
(`agents/check-site-compatibility-for-web-wrapper`, now merged to a
commit of its own) independently used `0034`–`0040` for unrelated
tables, so **whichever branch merges second will need its migration
numbers rebased to continue after the other's** — this was partially
done in the discarded work (see below) but needs redoing from
whatever the real tip is when this is resumed.

Workspace **switching is still not wired to any UI**. `dashboard` view
still branches purely on `request.user.role` (client vs tradie) and
never calls `workspaces.get_active_workspace()`/`set_active_workspace`
from any route or template — there's no URL, view, or dashboard toggle
that lets a user switch between their client and provider workspace.
The only code that exercises the workspace-switching functions is the
Facebook Page-linking flow described below (now discarded) and the
test suite.

## What was built but discarded (uncommitted, never merged)

A full Facebook Login / Page linking / Messenger backend, described in
detail in this session's earlier review. Summary of what existed:

- **Models**: `SocialIdentity`, `FacebookPageConnection`,
  `MessengerConnection`, `MetaWebhookEvent`,
  `SocialDataDeletionRequest` — with a migration
  (`0038_meta_integration_models` / renumbered to `0045` before the
  reset) creating all five tables.
- **`marketplace/meta_client.py`** — Graph API wrapper: OAuth dialog
  URL, code-for-token exchange, long-lived token exchange, user
  profile fetch, list managed Pages, `send_messenger_message` (never
  called by anything — see below).
- **`marketplace/meta_security.py`** — Page-token encryption
  (Fernet/MultiFernet, rotation-friendly), webhook signature
  verification (`X-Hub-Signature-256`), the Meta data-deletion
  `signed_request` algorithm, and signed/expiring "Messenger connect"
  deep-link ref tokens (`m.me/<page>?ref=<token>` → (user, workspace,
  page_connection)).
- **`marketplace/messenger_policy.py`** — 24-hour session-window
  delivery-eligibility rules per event type (session-only /
  policy-evaluated / recurring-permission-required categories). The
  two permission-gated modes (`approved_recurring_notification`,
  `approved_message_tag`) were deliberately hardcoded to always return
  `False` — not implementable until a real Meta app has been granted
  those permissions and the actual granted-permission API shape is
  known.
- **`marketplace/social_views.py`** — `facebook_connect` /
  `facebook_callback` (OAuth login, with an explicit
  account-linking-challenge on email collision — never silently merges
  accounts), `facebook_pages_list` / `facebook_pages_connect` (Page
  linking, encrypts the Page token before storing), `messenger_disconnect`,
  `meta_webhook` (GET verification handshake + POST signature-verified
  idempotent event processing), `meta_data_deletion` /
  `meta_data_deletion_status` (Meta's required data-deletion callback).
- **Settings**: `META_APP_ID`/`META_APP_SECRET`/`META_GRAPH_API_VERSION`/
  `META_OAUTH_REDIRECT_URI`/`META_WEBHOOK_VERIFY_TOKEN`/
  `META_TOKEN_ENCRYPTION_KEY`, with a production startup check that
  raises `ImproperlyConfigured` if a Meta feature flag is enabled
  without its required env vars.
- **Tests**: ~719 lines covering the above (this branch had the most
  thorough test coverage of anything reviewed this session — 205
  passing tests total including these).

### What was explicitly stubbed / never built, even in the discarded version

- **No notification engine.** `send_messenger_message()` existed but
  nothing called it — there's no code path that decides "this event
  should message this user via Messenger" and actually sends it. This
  was the single biggest remaining gap before the integration could do
  anything user-visible.
- **No templates/UI.** Page listing and the data-deletion status
  endpoint returned raw JSON; backend scaffolding only.
- Facebook-created accounts skipped `TermsAcceptance` (no onboarding
  wizard existed to collect real consent for that signup path).

## If this gets rebuilt

Start from `17ada9e` (current branch tip). The workspace foundation is
solid and tested — reuse it as-is. The Meta integration itself is
straightforward to redo from the description above (it was clean,
well-tested code); the main sequencing decision is **what actually
sends a Messenger message** — that notification-engine piece needs
designing, not just re-typing what existed before. Whatever migration
numbers are free at that point, take them — don't reuse `0034`–`0045`,
since that range is now owned by the Suppliers branch and this
document's own discarded renumbering attempt.
