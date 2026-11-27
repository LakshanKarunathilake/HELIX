# User management, invitations and decision tracking — design

Date: 2026-11-25 (revised: Firebase Google sign-in) · Owner: Lakshan Karunathilake

## 1. Intent

HELIX is used to link mentions to a knowledge base with a human in the loop. Today nobody is
identified: access is one shared Caddy basic-auth login, and the `annotator` is a free-text
name typed into the session config. The owner wants to **invite annotators**, and to know **who
annotated which row and what decision they took** — including when several people annotate the
*same* rows (to measure agreement, as in the IJCKG pilot study) and when a reviewer settles
disagreements.

Success: an admin can invite people by email address, each invitee signs in with their Google
account, annotators work independently on the same rows without seeing each other, every decision
is attributable and timestamped, and a reviewer can resolve disagreements and export a final set.
Running costs stay near zero.

## 2. What exists today (facts that shape the design)

* One `sessions/<name>/annotated.csv` per session, written by full-file read-modify-write under
  one process-wide `_WRITE_LOCK` (`api/main.py`). Two people in one session overwrite each other.
* `SessionConfig.annotator` (one string) is stamped on every saved row, and also by Auto-annotate
  and graph Auto-match, so script output is indistinguishable from human output.
* Per-row provenance (`_method`, `_elapsed_ms`, `_selected_rank`, `_revisions`, …) is frozen on a
  row's first save (`eltool/store.py`). That is per row, so it must become per user.
* `annotated.csv` is read by progress, row fetch, document review, graph optimise, snapshots,
  export, feedback and the eval scripts.
* Hosted mode (`HELIX_HOSTED=1`, see `2026-11-08-hosted-byo-connections-design.md`) runs one
  uvicorn worker behind Caddy with a `sessions` volume that is backed up nightly. Its spec lists
  "user accounts, creator vs annotator roles and session ownership" as the next plan: this is it.
* The web app is a React SPA (`web/`) with `Home`, `Configure`, `Annotate` pages and no login.

## 3. Goals, non-goals, anti-goals

**Goals**
1. Accounts with three roles: **admin**, **reviewer**, **annotator**.
2. Invite-only onboarding: an admin allow-lists an email address; that person signs in with
   Google (through Firebase Authentication).
3. Several annotators annotate the same rows independently (a private annotation layer each).
4. An append-only **decision log** that records who did what, when, to which row, and whether a
   human or a script did it.
5. A reviewer view that shows every annotator's decision per row, flags disagreements, records an
   adjudicated final decision, and exports it.
6. Cheap: Firebase Authentication (Google sign-in, free at this scale — confirm on Firebase's
   pricing page) and SQLite. No passwords to store and no email service to run.

**Non-goals**
* Sign-in providers other than Google (Firebase can add them later without backend changes
  beyond widening an allowed-provider set), self-service sign-up, MFA, real-time collaboration.
* Per-row assignment (annotators all see all rows of the sessions they are members of).
* Automatically attributing the existing single `annotated.csv` files to users (see §7).
* Per-user LLM or KB credentials. Session secrets stay with admins, write-only as today.
* Emailing invitations. The admin sends the sign-in URL themselves.

**Anti-goals** (a failure even if it technically works)
* Changing behaviour when auth is off: `./run.sh`, existing sessions and every existing test must
  behave exactly as today, and must not need Firebase or network access.
* Any Google account being able to sign in: only allow-listed (or already registered) emails.
* An annotator being able to read another annotator's decisions, directly or by guessing an id.
* A script's output being recorded as a human decision, or counting towards agreement.
* Any API or UI path that edits or deletes decision-log entries.

## 4. Approaches considered

**Row sharing** (chosen by the owner: *same rows, all annotate*)
* *Private layer per annotator* — each annotator has their own `annotations/<user_id>.csv` with
  the existing column layout. Provenance, the frozen first-save rule, feedback columns and the
  eval scripts keep working unchanged; blind by construction. Cost: N files per session.
  **Chosen.**
* *One shared file with a decision-per-user table in SQLite* — a single source of truth, but
  every existing endpoint would have to be rewritten against SQLite, and the frozen-provenance
  logic would be reimplemented. Rejected: too invasive.
* *Shared file, rows split between people* — simplest, but cannot measure agreement, which the
  owner needs. Rejected.

**Sign-in** (chosen by the owner: *Firebase Authentication, Google first*)
* *Firebase Auth with Google sign-in; HELIX verifies the Firebase ID token server-side and keeps
  its own invite allow-list, roles and memberships.* No passwords, resets, throttling or SMTP.
  Cost: a Firebase project, the `firebase` web package and Google's `google-auth` library, and
  annotators need a Google account. **Chosen.**
* *Own accounts with scrypt passwords and emailed invite links* — no outside service and works
  with any email, but HELIX would own password handling, resets, throttling and an SMTP setup.
  Rejected by the owner.
* *Google sign-in directly (no Firebase)* — same allow-list model, no Firebase project, but no
  path to other providers. Rejected.
* *Cloudflare Access in front* — ties login to Cloudflare, and HELIX would still need its own
  user table. Rejected.

**Storage of users and the log** (chosen: *SQLite*)
* *One SQLite file `sessions/helix.db`* — no new service, lives in the already-backed-up volume,
  queryable across users and sessions, exports to CSV. **Chosen.**
* *JSONL per session* — matches the file-based style, but cross-user queries (agreement, log
  viewer) get awkward. Rejected.

**Roles** (chosen by the owner: *admin, reviewer, annotator*).

## 5. Design

### 5.1 Switch

`HELIX_AUTH=1` turns accounts on. **Unset (the default), nothing changes**: no login, no Firebase,
the session's `annotator` string is used, output goes to `annotated.csv`. `deploy/hosted` sets both
`HELIX_HOSTED=1` and `HELIX_AUTH=1`. Like `hosted_mode()`, `auth_enabled()` reads the environment
on each call so tests can toggle it.

### 5.2 Data (`sessions/helix.db`, SQLite, WAL mode)

| Table | Columns |
|---|---|
| `users` | `id`, `email` (unique, lowercased), `name`, `role` (`admin`/`reviewer`/`annotator`), `firebase_uid` (unique, null until the first sign-in), `active`, `created_at`, `last_login_at` |
| `invites` | `id`, `email`, `role`, `session_names` (JSON list), `created_by`, `created_at`, `accepted_at`, `revoked_at` — an allow-list row, with no token and no expiry |
| `auth_sessions` | `token_hash`, `user_id`, `created_at`, `expires_at` |
| `memberships` | `session_name`, `user_id` (no role column: the user's global role decides what they can do) |
| `decisions` | `id`, `ts`, `session_name`, `row_idx`, `user_id`, `actor` (`human`/`auto_annotate`/`auto_match`), `action` (`save`/`feedback`/`batch`/`auto`), `kb_values` (JSON), `previous` (JSON), `llm_used`, `method`, `elapsed_ms`, `selected_rank`, `revision` |
| `adjudications` | `id`, `ts`, `session_name`, `row_idx`, `reviewer_id`, `kb_values` (JSON), `note` |

`decisions` is append-only: no endpoint updates or deletes it. `adjudications` is also append-only;
the latest row per `(session_name, row_idx)` is the current final decision.

### 5.3 Authentication

* The browser signs in with Google through the Firebase web SDK (popup), gets a Firebase **ID
  token**, and posts it to `POST /api/auth/session`. The API verifies it with
  `google.oauth2.id_token.verify_firebase_token` (audience `HELIX_FIREBASE_PROJECT_ID`) and
  requires `email_verified` and sign-in provider `google.com`.
* The email in the token is matched, lowercased, to a user. A user already linked to that
  `firebase_uid` signs in directly. Otherwise a **pending invite** for the email (or an admin row
  made by the bootstrap command that has no uid yet) creates or links the user: the role and
  memberships come from the invite, `firebase_uid` is stored, and the invite is marked accepted.
  After that the user is identified by uid, so a later change of email on the Google side does not
  lock them out or create a second account.
* Anything else — unknown email, revoked invite, inactive user, bad or foreign-project token,
  unverified email, other provider — returns the same `403 "This Google account is not authorised
  for HELIX"`. No detail about which check failed.
* On success HELIX sets its own cookie: a random 32-byte token in an `HttpOnly`,
  `SameSite=Strict` cookie (`Secure` when hosted), only its SHA-256 stored, 14 days. Every later
  request is checked against that, so deactivating a user takes effect immediately, and the Firebase
  SDK is only used at the login moment. Logout deletes the stored row.
* There is no password anywhere in HELIX and no sign-up route.

### 5.4 Invitation process

1. **Admin invites** — enters an email, a role, and the sessions to join.
   `POST /api/admin/invites` adds an allow-list row and returns the **sign-in URL**
   (`HELIX_PUBLIC_URL`) to copy and send by whatever channel they like.
2. **The invitee opens the URL** and clicks "Sign in with Google" using the Google account whose
   address was invited. The first successful sign-in creates their account (name taken from the
   Google profile, falling back to the part of the email before `@`), applies the role and
   memberships, and marks the invite accepted.
3. **Admin tracks status** — pending / accepted / revoked. Revoking a pending invite stops that
   email signing in.
4. **Email already registered** — inviting an address that already has an account adds the
   memberships immediately and marks the invite accepted; roles are changed on the Users page.
5. **Lost access** — nothing to reset. If someone loses their Google account the admin invites the
   new address; the old account is deactivated on the Users page.

### 5.5 Roles and access

| | admin | reviewer | annotator |
|---|---|---|---|
| Create/edit/delete sessions, KB/graph/LLM settings | ✔ | | |
| Invite/revoke users, change roles, set memberships | ✔ | | |
| See all sessions | ✔ | member sessions | member sessions |
| Annotate (own layer) | ✔ | ✔ | ✔ |
| See other users' decisions | ✔ | member sessions | never |
| Adjudicate, export final set | ✔ | ✔ | |
| View decision log, agreement, export log | ✔ | member sessions | |

Roles are **global** (`users.role`). A membership only grants access to a session; it carries no
role of its own. So a reviewer is a reviewer in every session they are a member of, and an admin
is a member of everything. A reviewer or admin who also annotates has their own layer like anyone
else, and that layer takes part in review and agreement.

In auth mode every `/api/` route except `/api/info`, `/api/auth/session` and the static web app
requires a signed-in user (a gate in front of the whole API, so a future route is protected by
default). The annotation-flow endpoints (`rows`, `candidates`, `graph/*`, `documents*`,
`progress`, `feedback`, `reflection`, `auto-annotate`, `entity` lookup, `snapshot`, `snapshots`)
require membership, then operate on the **caller's own layer**:
* `reflection.json` becomes `reflection.<user_id>.json`, one per user.
* Stage snapshots become `annotated.<stage>.<user_id>.csv`, taken from and listed for the caller's
  own layer, so the study's `retrieval` / `graph` stage workflow works per annotator. An admin can
  download any user's stage file with `?user=<id>`.

Configuration, metrics and the global test endpoints are admin-only. Session list and
`GET /api/sessions/{name}` are filtered to the caller's memberships. A session the caller may not
see returns 404, not 403.

**Deleting a session** is refused in auth mode once the decision log has any entry for it, so the
log is never orphaned and a reused name cannot inherit old members or history. A session with no
decisions can be deleted; its memberships are removed with it.
**Removing a member** blocks their access but keeps their layer and log; their human decisions
still count in review and agreement.
**The last active admin** cannot be demoted or deactivated.

### 5.6 Private layers and the decision log

* In auth mode `output_csv_path(name, user_id)` is `sessions/<name>/annotations/<user_id>.csv`;
  without a `user_id` it is the legacy `annotated.csv`. `init_output` and `write_row` are reused
  unchanged, so the column layout, frozen first-save provenance, `_revisions`, feedback and AI
  columns all behave as they do today, now per user. The `annotator` column holds the user's
  display name.
* Every write (save, batch, feedback, auto-annotate, auto-match) appends to `decisions` **inside
  the same `_WRITE_LOCK` section** as the CSV write, with the previous value so a change is visible.
  If the log insert fails the save fails, so the two never diverge.
* `actor` is `human` for a save made from the annotation UI, `auto_annotate` for Auto-annotate,
  `auto_match` for graph Auto-match. When the annotator clicked to accept the LLM's pick, the
  save is still `human`, with `llm_used` recording the model. Auto-match and Auto-annotate write
  into the caller's own layer only, as before, never over a saved human decision.
* **A row counts as a human decision** for a user when the latest `decisions` entry for that
  user and row (ignoring `feedback`) has `actor = human`. A script-written row therefore stops
  being "script" the moment the annotator opens it and saves it (even just confirming).

### 5.7 Reviewer view, agreement, export

* `GET /api/sessions/{name}/review` returns, per row, every user's decision (name, actor,
  `kb_values`, `llm_used`, time) plus the current adjudication. Script-written rows are shown and
  tagged `script`. `disagree` is true when two or more users' **human** decisions differ in any
  KB column. Annotators receive 403.
* `GET /api/sessions/{name}/agreement` returns pairwise exact-match percent agreement and
  Cohen's kappa per KB column, over rows where **both** users have a human decision. Script rows
  are excluded, and the response also reports how many rows were excluded for that reason.
* `POST /api/sessions/{name}/adjudications` records a final decision for a row (reviewers/admins).
  An adjudication is **per row, across all KB columns** (one `kb_values` JSON); the review UI
  prefills it with the agreed or first value so the reviewer only changes what they dispute.
* `GET /api/sessions/{name}/export?final=1` returns one CSV: the input columns plus, per KB, the
  final value: the adjudication if present; otherwise the common value when **at least two users
  have a human decision on the row and all of them match in every KB column**; otherwise blank.
  Blank rows carry `_needs_review=1` and `_review_reason` (`disagree`, `single_annotator`,
  `script_only`, or `no_decisions`). `GET /api/admin/decisions.csv?session=…` exports the log.
* A user's own layer is exported the usual way (`export` without `final`, or `?stage=`).

### 5.8 Bootstrap and deployment

* First admin: `python -m eltool.users create-admin --email … [--name …]` creates an admin row
  with no password and no uid; it links at that person's first Google sign-in. It also works as
  `docker compose exec api python -m eltool.users …`.
* Firebase setup (one-off, in the Firebase console): create a project, enable the Google sign-in
  provider, add the HELIX hostname to the authorised domains. API environment:
  `HELIX_FIREBASE_PROJECT_ID`, and the web config values `HELIX_FIREBASE_API_KEY` and
  `HELIX_FIREBASE_AUTH_DOMAIN`, which `GET /api/info` hands to the browser (they are public
  identifiers, not secrets). `HELIX_PUBLIC_URL` is used for the sign-in URL.
* `deploy/hosted`: the Caddy `basic_auth` block is removed and `HELIX_BASIC_*` dropped from the
  compose file and `.env.example`; app login replaces it. The README gets a "Firebase and accounts"
  section and an updated smoke checklist.
* One uvicorn worker stays: the SQLite writes assume one process.

### 5.9 Web UI

* **Login** page with a "Sign in with Google" button; all other routes redirect to it when signed
  out (auth mode only). A refused account sees the generic "not authorised" message.
* **NavBar** shows the user's name, role and a sign-out button.
* **Home** lists only the caller's sessions; "New session" and "Settings" are admin-only.
* **Configure**: the "Annotator name" field is hidden in auth mode (the signed-in user is the
  annotator).
* **Users** page (admin): invite form that returns the sign-in URL with a Copy button, invite
  status table with revoke, user table with role change, deactivate and per-session membership.
* **Review** page (reviewer/admin) per session: row table with each annotator's decision side by
  side, disagreements highlighted and filterable, an adjudicate action, agreement summary, and
  final-set export.
* The Annotate page itself is unchanged apart from calling the same endpoints as the signed-in user.

## 6. Constraints

* New dependencies are limited to `google-auth` (API, `requirements-api.txt`) and `firebase`
  (web, pinned). Everything else is the standard library (`sqlite3`).
* Single uvicorn worker.
* Tests follow the repo's pytest pattern; auth is toggled with the environment like hosted mode.
  Backend tests never call Firebase: they replace the token verifier and log users in by creating
  a login session directly.
* ID tokens and session cookies are never logged.
* Signing in with Google needs internet access and a Firebase project; running with auth off
  does not.

## 7. Left to the builder

1. Exact module layout (suggested: `eltool/auth.py`, `eltool/idtoken.py`, `eltool/userdb.py`, a
   `Depends`-based current-user dependency in `api/`).
2. SQL schema details, indexes, and migration/version table.
3. Column order and styling of the new UI pages; the shape of the review table.
4. Cohen's kappa edge cases (single category, <2 shared rows → null).
5. *Existing sessions*: they keep working in local mode. In auth mode their legacy
   `annotated.csv` is visible to admins via export only. A small
   `scripts/adopt_legacy_annotations.py --session S --user EMAIL` that copies it into a user's
   layer is welcome but optional.

## 8. Calls made by the author (please check)

* `SameSite=Strict` cookie and no CORS in hosted mode as the CSRF defence.
* HELIX issues its own cookie after verifying the Firebase ID token once, rather than sending the
  Firebase token on every request: deactivation is immediate and no token refresh logic is needed.
* Only the `google.com` provider is accepted for now; the user is matched by uid after the first
  sign-in, by email only for the first.
* Invites are an allow-list with no token and no expiry; HELIX sends no email.
* Reviewers see annotators' names (review is not blind); annotators are blind to each other.
* Admin-only: configuration, metrics and the global test endpoints. Snapshots and reflections are
  per user and open to annotators, because the pilot study's stage workflow needs them.
* Roles are global; a membership only grants access to a session.
* Deleting a session with logged decisions is refused (no archive feature in this plan).
* Removed members keep their layer and log, and their human decisions still count.
* Final export needs at least two human decisions that all match; anything less is blank and
  flagged, so a single annotator's value is never published as final by accident.
* Cohen's kappa is reported next to percent agreement; for entity IDs kappa is a rough measure
  because of the many categories, so percent agreement is the headline number.
* Removing Caddy basic-auth from the hosted compose file (rather than keeping both layers).
