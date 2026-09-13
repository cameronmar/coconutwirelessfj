# Google Sign-In

Lets people sign in and sign up with a Google account, alongside the existing
email + password login (which keeps working exactly as it does today). Off by
default behind `GOOGLE_LOGIN_ENABLED`.

## How it works

The browser uses Google Identity Services (GIS) to get a signed ID token
(JWT) directly from Google, then POSTs that token to `/auth/google/`. Django
verifies the token server-side with `google-auth` and never trusts anything
else the request sends — email, name, and Google user id all come from the
verified token claims.

There is **no OAuth redirect/callback dance and no client secret** anywhere
in this flow. That also means the same `/auth/google/` endpoint will serve
the Android app later — native Google Sign-In on Android produces the same
kind of ID token (see "Android" below).

`SocialAccount` (`marketplace/models.py`) is deliberately provider-generic —
Facebook Login (planned on `feature/facebook-ecosystem`) is expected to reuse
it rather than add a second, near-identical model.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `GOOGLE_LOGIN_ENABLED` | `False` | Master switch. If true but `GOOGLE_OAUTH_CLIENT_ID` is empty, Django logs a warning at startup and treats the feature as off (never renders a button that would 500). |
| `GOOGLE_OAUTH_CLIENT_ID` | *(empty)* | The Web application OAuth client id, used both by the browser (GIS init) and by the server (accepted token audience). |
| `GOOGLE_OAUTH_ANDROID_CLIENT_ID` | *(empty)* | The Android OAuth client id. Optional until the Android app ships its native sign-in; when set, `/auth/google/` accepts ID tokens minted for either audience. |

Add these to `.env` locally (see `.env.example`).

## Google Cloud Console setup

1. Create an OAuth 2.0 **Web application** client id.
2. Authorized JavaScript origins: `http://localhost:8000`, the Railway
   staging URL, and `https://coconutwireless.fj` (plus `www.` if used). No
   redirect URIs are needed — GIS callback mode doesn't use them.
3. OAuth consent screen: app name, support email, privacy policy and terms
   URLs pointing at the live pages. Scopes stay at `openid`, `email`,
   `profile` — nothing more, so no Google verification review is triggered.
4. Later, for the Android app: create a separate **Android** OAuth client id
   with the app's package name and SHA-1 signing certificate fingerprint.

## Enabling it

1. Set `GOOGLE_OAUTH_CLIENT_ID` (and, once the Android app is ready,
   `GOOGLE_OAUTH_ANDROID_CLIENT_ID`).
2. Set `GOOGLE_LOGIN_ENABLED=True`.
3. Restart the app. The button appears on the login page and the three
   registration pages (client/tradie/supplier), and in Account settings →
   "Add or merge another account" → Connected accounts.

Nothing changes for any existing user until step 2 — the flag gates the
button's visibility, the `/auth/google/*` endpoints (404 while off), and the
"Connected accounts" link/unlink UI.

## Disabling it in a hurry

Set `GOOGLE_LOGIN_ENABLED=False` (or unset it) and restart. This immediately:

- Hides the button everywhere.
- 404s `/auth/google/` and `/auth/google/complete/` (an unlaunched/disabled
  feature should look like it doesn't exist, not like a locked door).
- Leaves existing `SocialAccount` rows and Google-linked users untouched —
  they just can't use the Google button until it's back on. Their accounts
  are otherwise completely normal; if they also have a usable password they
  can still log in with it, and if not, see "Password login on a Google-only
  account" below.

Nothing about disabling this flag deletes data or logs anyone out.

## Behaviour worth knowing about

- **Every Google signup starts as a Client account.** Tradie/supplier
  registration needs document uploads a Google ID token can't supply, so
  choosing "Local professional" or "Supplier" on the completion step creates
  the Client account first, then routes straight into the existing
  `add_role_tradie` / `add_role_supplier` "add a role" flow.
- **Staff/superuser accounts are never silently linked.** If a Google sign-in
  attempt matches an existing staff/superuser account by email, it's refused
  with a generic message. Staff can still link Google from Account settings
  while already logged in with their password — that's the only supported
  way for a staff account to gain a `SocialAccount`.
- **Matching is always by Google's `sub` claim**, never by email — an
  account's Google email can change; its subject id never does.
- **Password login on a Google-only account** shows "This account uses
  Google sign-in — tap Continue with Google above." instead of the generic
  wrong-password message, but only when the account genuinely has no usable
  password *and* has a linked Google account — it can't be used to
  fingerprint arbitrary registered emails.
- **Password reset on a Google-only account**: the reset request page always
  shows the same generic "if that email is registered..." message (so the
  page itself gives nothing away either way). For a Google-only account, no
  reset link is sent — an informational email pointing them at Google
  sign-in goes out instead, so the reset flow can't be used to bolt a
  password onto an account that deliberately doesn't have one.
- **Unlinking Google is refused** if it would leave the account with no way
  to log in (no usable password, no other linked provider) — the user is
  told to set a password first.
- Hidden entirely inside the Android app's WebView (Google blocks OAuth
  there — `disallowed_useragent`) via a best-effort user-agent check in
  `marketplace/context_processors.py::_looks_like_app_webview`. **This still
  needs confirming against the actual UA string the
  `cameronmar/coconutwireless-android-app` WebView sends** — see the
  `TODO(android)` in that function.

## Android (follow-up ticket, not this PR)

1. Native Google Sign-In in the Kotlin app using the Android OAuth client id,
   requesting the **web** client id as the server client id, so the ID
   token's audience is predictable.
2. App POSTs that token to the same `/auth/google/` endpoint.
3. The endpoint already accepts the Android audience once
   `GOOGLE_OAUTH_ANDROID_CLIENT_ID` is set — no server change needed.
4. Register the FCM token straight after, via the existing
   `register_fcm_token` endpoint.

## Tests

`marketplace/tests.py` — search for `GoogleLogin` — covers: flag-off 404 and
no button, new-signup hold-then-complete (no `User` until the completion
step), existing-SocialAccount direct login, existing-password-user linking,
`email_verified: false` rejection, staff-match refusal, wrong/expired token
rejection, age gate (under-16 lockout, 16–17 guardian consent), closed-beta
gating against the verified Google email, IP rate limiting, `?next=`
host validation, the password-login and unlink-refusal messages, and that the
full existing suite still passes unchanged.
