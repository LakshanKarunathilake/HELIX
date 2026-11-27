# User Management, Invitations and Decision Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add invite-only accounts (admin / reviewer / annotator) signed in with Google through Firebase, a private annotation layer per annotator, an append-only decision log, and a reviewer view with agreement and adjudication, all behind `HELIX_AUTH=1`.

**Architecture:** The browser signs in with Google (Firebase web SDK) and posts the ID token to the API, which verifies it, matches the email to an invite allow-list, and issues its own session cookie. Users, invites, memberships, login sessions, the decision log and adjudications live in one SQLite file, `sessions/helix.db`. In auth mode every annotation endpoint reads and writes `sessions/<name>/annotations/<user_id>.csv` through the existing `init_output` / `write_row`, and appends to the log in the same lock section. With auth off, nothing changes.

**Tech Stack:** FastAPI, stdlib `sqlite3`, `google-auth` (new, API), `firebase` (new, web), pytest `TestClient`; React 18 + react-router + react-query + Tailwind.

**Spec:** [`docs/superpowers/specs/2026-11-25-user-management-design.md`](../specs/2026-11-25-user-management-design.md). Read it first; section numbers below (§5.x) refer to it.

**Baseline (2026-11-25):** `KMP_DUPLICATE_LIB_OK=TRUE /Users/lakshankarunathilake/miniconda3/bin/python -m pytest tests -q` → 401 passed; `cd web && npx tsc --noEmit` → clean. Both must stay green after every task. The web app has no unit-test runner; web tasks are verified with `npx tsc --noEmit`, `npm run build` and a browser check.

## Global Constraints

- New dependencies limited to `google-auth>=2.29` (API, `requirements-api.txt`) and `firebase` (web, pinned to an exact version at least two weeks old). Everything else is the standard library.
- Single uvicorn worker; SQLite writes assume it.
- `HELIX_AUTH=1` turns accounts on; unset means `./run.sh`, existing sessions and every existing test behave exactly as today, with no Firebase and no network. `auth_enabled()` reads the environment on each call.
- Identity: a Firebase ID token verified server-side with `google.oauth2.id_token.verify_firebase_token`, audience `HELIX_FIREBASE_PROJECT_ID`; require `email_verified` and sign-in provider `google.com`. Every refusal returns the same `403 "This Google account is not authorised for HELIX"`.
- HELIX session cookie: random 32-byte token, only its SHA-256 stored; `HttpOnly`, `SameSite=Strict`, `Secure` when hosted; 14 days.
- Invite = allow-list row (email, role, sessions). No token, no expiry, no email sending.
- `decisions` and `adjudications` are append-only: no endpoint updates or deletes them.
- A session the caller may not see returns 404, not 403.
- ID tokens and cookies are never logged.
- Env vars: `HELIX_AUTH`, `HELIX_PUBLIC_URL`, `HELIX_FIREBASE_PROJECT_ID`, `HELIX_FIREBASE_API_KEY`, `HELIX_FIREBASE_AUTH_DOMAIN`.
- `eltool.userdb` must read `eltool.session.SESSIONS_DIR` at call time (`import eltool.session as session_mod`), because tests monkeypatch it.
- Backend tests never call Firebase: they monkeypatch `eltool.idtoken.verify_id_token` or log in through the `login_as` fixture.

## Review Focus

Failure modes the spec implies that no feature task would otherwise test. Each has its test inside the owning task.

1. Two annotators save the same row at the same time → two layer files, two log rows, neither overwritten (Task 6).
2. A deactivated user's existing cookie, an expired cookie, and a token that is unverified, from another Firebase project, or from a non-Google provider are all refused identically (Tasks 2, 3).
3. An uninvited Google account, a revoked invite, and an invite for an already-registered email behave as §5.3–5.4 say (Task 3).
4. The last active admin cannot demote or deactivate themselves (Task 4).
5. In auth mode, helper routes that look harmless (`/api/sessions/parse-file`, `/api/llm/default-prompt`, `/api/feedback/options`) refuse signed-out callers (Task 2).
6. A session created before accounts (legacy `annotated.csv`) opens in auth mode with an empty layer and does not crash (Task 5).
7. An annotator cannot reach another user's layer, `/review`, `/agreement`, or `export?user=` (Tasks 5, 8).

---

## File Structure

| File | Responsibility |
|---|---|
| `eltool/hosting.py` (modify) | add `auth_enabled()` |
| `eltool/userdb.py` (new) | SQLite connection + schema, users, login sessions, memberships |
| `eltool/idtoken.py` (new) | verify a Firebase ID token |
| `eltool/invites.py` (new) | invite allow-list and first-sign-in provisioning |
| `eltool/decisions.py` (new) | decision log, adjudications, queries |
| `eltool/agreement.py` (new) | pure functions: pairwise agreement, kappa, final-value rule |
| `eltool/users.py` (new) | `python -m eltool.users create-admin` |
| `eltool/auth.py` (new) | FastAPI middleware gate, dependencies, cookie helpers |
| `api/auth_routes.py` (new) | `/api/auth/*`, `/api/admin/*` |
| `api/review_routes.py` (new) | `/api/sessions/{name}/review|agreement|adjudications`, final export |
| `eltool/session.py` (modify) | per-user paths |
| `api/main.py` (modify) | gating, per-user layers, logging |
| `tests/conftest.py` (new) | `auth_env`, `make_user`, `login_as` fixtures |
| `web/src/...` (new/modify) | login, users, review pages; auth context |
| `deploy/hosted/*`, `requirements-api.txt` (modify) | drop basic-auth, Firebase env, README |

---

### Task 1: Auth switch, SQLite schema, users

**Files:**
- Modify: `eltool/hosting.py`
- Create: `eltool/userdb.py`, `tests/conftest.py`, `tests/test_userdb.py`

**Interfaces:**
- Produces: `hosting.auth_enabled() -> bool`; `userdb.connect() -> sqlite3.Connection` (WAL, `foreign_keys=ON`, row factory `sqlite3.Row`, schema of §5.2 created idempotently, `PRAGMA user_version=1`); `@dataclass User(id:int, email:str, name:str, role:str, active:bool)`; `ROLES=("admin","reviewer","annotator")`; `create_user(email:str, name:str, role:str, firebase_uid:str|None=None)->User`; `get_user(id)->User|None`; `get_user_by_email(email)->User|None`; `get_user_by_uid(uid)->User|None`; `link_uid(user_id:int, uid:str)`; `list_users()->list[User]`; `set_role(id,role)`; `set_active(id,active:bool)`; `touch_login(id)`; `token_hash(token:str)->str` (SHA-256 hex). Emails are stripped and lowercased everywhere.
- `tests/conftest.py` fixture `auth_env(tmp_path, monkeypatch)`: sets `HELIX_AUTH=1`, patches `eltool.session.SESSIONS_DIR` to `tmp_path`, returns `tmp_path`.

- [ ] **Step 1: Write failing tests** in `tests/test_userdb.py`: `test_auth_enabled_follows_env`; `test_email_is_lowercased_and_unique` (`create_user("A@x.org",…)` then `get_user_by_email("a@x.org")` found; a second create with `" a@X.org "` raises `ValueError`); `test_role_must_be_valid`; `test_link_uid_and_lookup_by_uid`; `test_uid_is_unique` (linking one uid to two users raises `ValueError`); `test_db_file_is_created_in_sessions_dir`; `test_schema_is_idempotent` (connect twice).
- [ ] **Step 2:** Run `python -m pytest tests/test_userdb.py -q` → FAIL (module missing).
- [ ] **Step 3:** Implement the interface above.
- [ ] **Step 4:** New tests PASS; full suite → 401 + new tests pass.
- [ ] **Step 5:** Commit `feat(auth): userdb with users and the auth switch`.

---

### Task 2: Login sessions, middleware gate, `/api/auth/me|logout`, bootstrap CLI

**Files:**
- Create: `eltool/auth.py`, `api/auth_routes.py`, `eltool/users.py`, `tests/test_auth_api.py`
- Modify: `eltool/userdb.py` (login sessions), `api/main.py` (include router, add middleware, extend `/api/info`), `tests/conftest.py`

**Interfaces:**
- Consumes: Task 1.
- Produces in `userdb`: `create_auth_session(user_id:int)->str` (returns the raw token; stores `token_hash`, 14-day expiry), `user_for_token(token:str)->User|None` (None if unknown, expired, or user inactive), `delete_auth_session(token)`, `delete_user_auth_sessions(user_id:int)`.
- Produces in `eltool.auth`: `COOKIE_NAME="helix_session"`; `PUBLIC_PATHS` (exact `/api/info`, `/api/auth/session`); `async auth_gate(request, call_next)` middleware — when `auth_enabled()`, non-`/api/` paths and `PUBLIC_PATHS` pass; otherwise resolve the cookie to a user, set `request.state.user`, and return `JSONResponse({"detail":"Not signed in"}, 401)` if none; `current_user(request)->User|None` (None when auth is off); `require_admin`, `require_reviewer` (admin or reviewer) dependencies raising 403; `set_login_cookie(response, token)` / `clear_login_cookie(response)` (`Secure` only when `hosted_mode()`).
- Endpoints: `GET /api/auth/me` → `{id,email,name,role}`; `POST /api/auth/logout`; `GET /api/info` → `{"hosted":…, "auth":…, "firebase": {apiKey, authDomain, projectId}|null}` (firebase block only when auth is on and all three env vars are set).
- `python -m eltool.users create-admin --email E [--name N]` creates an active admin with no uid; refuses an existing email.
- conftest fixtures: `make_user(email, role="annotator", name=None)->User` and `login_as(client, user)->client` (sets the cookie from `userdb.create_auth_session`).

- [ ] **Step 1: Write failing tests** (`auth_env`, `make_user`, `login_as`, `TestClient(app)`): `test_signed_out_requests_are_401` (GET `/api/sessions`, POST `/api/sessions/parse-file`, GET `/api/llm/default-prompt`, GET `/api/feedback/options` → 401); `test_info_is_public_and_hides_secrets` (`firebase` has exactly `apiKey`, `authDomain`, `projectId`); `test_me_returns_signed_in_user`; `test_deactivated_user_cookie_stops_working` (login, `set_active(False)`, `/api/auth/me` → 401); `test_expired_session_is_401` (move `expires_at` into the past via SQL); `test_logout_invalidates_cookie`; `test_cookie_is_httponly_samesite_strict` (set via `set_login_cookie` on a response; assert attributes; `Secure` present only when `HELIX_HOSTED=1`); `test_auth_off_changes_nothing` (no `HELIX_AUTH`: `/api/sessions` → 200 with no cookie, `/api/info` has `auth: false`); `test_cli_create_admin_and_duplicate_refused`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Register `app.middleware("http")(auth_gate)` in `api/main.py` and `app.include_router(auth_routes.router)`.
- [ ] **Step 4:** New tests PASS; full suite green.
- [ ] **Step 5:** Commit `feat(auth): login sessions, auth gate, bootstrap CLI`.

---

### Task 3: Invites (allow-list) and Firebase ID-token sign-in

**Files:**
- Create: `eltool/idtoken.py`, `eltool/invites.py`, `tests/test_signin.py`
- Modify: `api/auth_routes.py`, `requirements-api.txt` (add `google-auth>=2.29`)

**Interfaces:**
- Consumes: Tasks 1–2. `userdb.add_member(session_name:str, user_id:int)` is added in this task; the rest of memberships is Task 4.
- Produces in `idtoken`: `class IdTokenError(Exception)`; `verify_id_token(token:str)->dict` returning `{"uid","email","email_verified":bool,"name":str,"provider":str}`; wraps `google.oauth2.id_token.verify_firebase_token(token, google.auth.transport.requests.Request(), audience=os.environ["HELIX_FIREBASE_PROJECT_ID"])` and raises `IdTokenError` on any failure.
- Produces in `invites`: `@dataclass Invite(id, email, role, session_names:list[str], status:str)` with status `pending|accepted|revoked`; `create_invite(email, role, session_names, created_by:int)->tuple[Invite,bool]` (bool = the email already had an account; then memberships are added at once and the invite is stored accepted); `list_invites()->list[Invite]`; `revoke(invite_id)` (pending only); `provision(claims:dict)->User` — the first-sign-in rules of §5.3, raising `NotAuthorised` for every refusal.
- Endpoints: `POST /api/auth/session {id_token}` (public) → sets the login cookie, returns `{id,email,name,role}`; refusal is `403` with the single message in Global Constraints. Admin: `POST /api/admin/invites {email,role,session_names}` → `{invite, sign_in_url, already_registered}`; `GET /api/admin/invites`; `POST /api/admin/invites/{id}/revoke`. `sign_in_url` is `HELIX_PUBLIC_URL` (fallback `http://localhost:5173`).

- [ ] **Step 1: Write failing tests** (monkeypatch `eltool.idtoken.verify_id_token`): `test_invited_account_signs_in_with_role_and_memberships`; `test_uninvited_account_is_refused`; `test_unverified_email_is_refused`; `test_non_google_provider_is_refused`; `test_token_from_wrong_project_is_refused` (verifier raises `IdTokenError`); `test_revoked_invite_is_refused`; `test_inactive_user_is_refused_with_same_message`; `test_all_refusals_have_identical_body`; `test_second_sign_in_matches_by_uid_after_email_change`; `test_email_match_is_case_insensitive`; `test_bootstrap_admin_links_on_first_sign_in`; `test_invite_for_registered_email_adds_memberships_immediately`; `test_invite_is_marked_accepted_after_first_sign_in`; `test_non_admin_cannot_manage_invites` (403); `test_response_includes_sign_in_url`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. `provision`: find by `firebase_uid`, else by email among registered users (admin row with no uid → `link_uid`), else a pending invite for the email → `create_user` with the invite's role and memberships, name from the claim or the email's local part; update `last_login_at`. Refuse when the user is inactive.
- [ ] **Step 4:** New tests PASS; full suite green.
- [ ] **Step 5:** Commit `feat(auth): invite allow-list and Firebase ID-token sign-in`.

---

### Task 4: Memberships and admin user management

**Files:**
- Modify: `eltool/userdb.py`, `api/auth_routes.py`
- Test: `tests/test_admin_users.py`

**Interfaces:**
- Produces in `userdb` (`add_member` already exists from Task 3): `remove_member(session_name, user_id)`, `members(session_name)->list[User]`, `sessions_for(user_id)->list[str]`, `is_member(session_name, user_id)->bool`, `count_active_admins()->int`.
- Endpoints (admin): `GET /api/admin/users` (with each user's session list); `PATCH /api/admin/users/{id} {role?, active?}`; `PUT /api/admin/users/{id}/sessions {session_names:[…]}` (replaces memberships; unknown session names → 400).
- Rule: an update that would leave zero active admins returns 409 `"Cannot remove the last admin"`.

- [ ] **Step 1: Write failing tests:** `test_admin_lists_users_with_sessions`; `test_change_role_and_deactivate`; `test_last_admin_cannot_demote_or_deactivate_self` (both 409); `test_second_admin_can_demote_first`; `test_membership_replace_adds_and_removes`; `test_unknown_session_name_is_400`; `test_deactivating_user_deletes_their_login_sessions`; `test_annotator_cannot_call_admin_users` (403).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** PASS + full suite.
- [ ] **Step 5:** Commit `feat(auth): memberships and admin user endpoints`.

---

### Task 5: Per-user layers and access gating of existing endpoints

**Files:**
- Modify: `eltool/session.py`, `api/main.py`, `eltool/auth.py`
- Test: `tests/test_layers.py`

**Interfaces:**
- Produces in `session`: `output_csv_path(name, user_id:int|None=None)` (`None` → today's `annotated.csv`; else `sessions/<name>/annotations/<user_id>.csv`); `output_snapshot_path(name, stage, user_id=None)` (`annotated.<stage>.<user_id>.csv`); `reflection_path(name, user_id=None)` (`reflection.<user_id>.json`).
- Produces in `eltool.auth`: dependency `session_access(name:str, request)->User|None` — `None` when auth is off; otherwise the user if admin or member of `name`, else `HTTPException(404)`.
- In `api/main.py`: helpers `_out(name, user)` (→ `output_csv_path(name, user.id if user else None)`) and `_who(cfg, user)` (→ `user.name if user else cfg.annotator`). Every annotation-flow endpoint listed in §5.5 (`rows/*`, `rows/batch`, `candidates`, `graph/*`, `documents*`, `progress`, `feedback`, `reflection`, `auto-annotate`, `entity`, `snapshot`, `snapshots`, `export`) gains `user=Depends(session_access)` and uses `_out` / `_who` in place of `output_csv_path(name)` / `cfg.annotator`. `export` accepts `?user=<id>` for admins only (others → 403) and, in auth mode without it, returns the caller's own layer. `GET /api/sessions` returns only the caller's sessions (all for admins). Session create / update / delete / context upload / `parse-file`, `/api/kb/test`, `/api/graph/test`, `/api/metrics/*`, `graph/stats*`, `metrics/*` depend on `require_admin`. In auth mode `create_session` ignores the request's `annotator` and stores the admin's name.

- [ ] **Step 1: Write failing tests** (sessions built with the `_make_session` pattern from `tests/test_api_provenance.py`): `test_user_a_and_b_get_separate_layer_files` (each saves row 0 with a different id; `annotations/<a>.csv` and `<b>.csv` differ; legacy `annotated.csv` not created); `test_annotator_sees_only_own_saved_value` (A saves; B's `GET rows/0` shows empty selection); `test_non_member_gets_404_on_every_flow_endpoint` (parametrised over the routes above); `test_session_list_is_filtered_to_memberships`; `test_annotator_cannot_create_or_edit_sessions` (403) and `test_admin_can`; `test_legacy_session_opens_with_empty_layer` (session with an existing `annotated.csv`, member opens `progress` → 0 done, no error); `test_reflection_is_per_user`; `test_snapshots_are_per_user_and_listing_excludes_legacy_and_other_users`; `test_export_user_param_is_admin_only`; `test_save_row_stamps_user_name_in_annotator_column`; `test_auth_off_paths_unchanged` (one save with auth off → `annotated.csv`, `cfg.annotator`).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement the interface above. `list_snapshots` in auth mode globs `annotated.*.<uid>.csv`; stage names cannot contain `.` (`_STAGE_RE`), so parsing is unambiguous. Keep the frozen-provenance logic untouched: it operates on whichever layer file it is handed.
- [ ] **Step 4:** New tests PASS; the original 401 still pass.
- [ ] **Step 5:** Commit `feat(auth): private annotation layers and endpoint access matrix`.

---

### Task 6: Decision log, session-delete rule, log export

**Files:**
- Create: `eltool/decisions.py`, `tests/test_decisions.py`
- Modify: `api/main.py`, `api/auth_routes.py`

**Interfaces:**
- Produces in `decisions`: `@dataclass Decision(id, ts, session_name, row_idx, user_id, actor, action, kb_values:dict, previous:dict, llm_used, method, elapsed_ms, selected_rank, revision)`; `transaction()` context manager yielding an object with `add(session_name,row_idx,user_id,actor,action,kb_values,previous,*,llm_used=None,method="",elapsed_ms=None,selected_rank=None,revision=None)` — inserts run immediately inside one SQLite transaction that commits on clean exit and rolls back on exception; `has_decisions(session_name)->bool`; `list_decisions(session_name:str|None=None, user_id:int|None=None)->list[Decision]`; `latest_human_or_script(session_name)->dict[tuple[int,int],Decision]` keyed `(user_id,row_idx)`, ignoring `action="feedback"`. No update or delete function exists.
- Actors: `human` for `save_row`; `auto_annotate` for `auto-annotate`; `auto_match` for `rows/batch`, `graph/optimize`, `graph/optimize-all`. A `save_row` with `llm_used` stays `human`.
- In `api/main.py`: each write endpoint, when `user` is not None, wraps the CSV write in `with decisions.transaction() as tx:` and calls `tx.add(...)` for every row written, with `previous` read from the layer before the write; the insert comes before `write_row`/`save_output` so a failed insert prevents the save. `save_row` takes `method`/`elapsed_ms`/`selected_rank` from its provenance dict when present and `revision` from `_revisions` after the write. `save_feedback` logs `action="feedback"` with empty `kb_values`. With auth off nothing is logged.
- `DELETE /api/sessions/{name}`: in auth mode returns 409 `"This session has logged decisions and cannot be deleted"` when `has_decisions`; otherwise deletes and removes its memberships.
- Endpoints (admin): `GET /api/admin/decisions?session=&user=` (JSON), `GET /api/admin/decisions.csv?session=` (CSV).

- [ ] **Step 1: Write failing tests:** `test_save_row_logs_human_decision_with_previous_value` (save `C1` then `C2` → two entries, second has `previous={"UMLS":"C1"}`); `test_batch_and_optimize_log_auto_match_actor`; `test_auto_annotate_logs_auto_annotate_actor`; `test_llm_accept_is_human_with_llm_used`; `test_failed_log_insert_prevents_the_save` (monkeypatch `tx.add` to raise → 500 and layer file unchanged); `test_two_users_saving_same_row_concurrently` (two threads, same idx → two layer files, two log rows); `test_auth_off_writes_no_log`; `test_delete_refused_after_decisions_and_allowed_before`; `test_decisions_csv_is_admin_only_and_has_header`; `test_no_route_modifies_decisions` (introspect `app.routes`: no non-GET route path contains `decisions`).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** PASS + full suite.
- [ ] **Step 5:** Commit `feat(auth): append-only decision log`.

---

### Task 7: Agreement and final-value rules (pure functions)

**Files:**
- Create: `eltool/agreement.py`, `tests/test_agreement.py`

**Interfaces:**
- Produces: `pairwise_agreement(values:dict[int,dict[int,str]])->list[dict]` — `values[user_id][row_idx]=value` for one KB, human decisions only; returns one dict per user pair `{"a":int,"b":int,"n":int,"percent":float|None,"kappa":float|None}` over rows both have; `percent` is a 0–100 float, `None` when `n==0`; `kappa` is Cohen's, `None` when `n<2` or expected agreement is 1.
  `final_rows(human:dict[int,dict[int,dict[str,str]]], adjudications:dict[int,dict[str,str]], row_count:int, kb_names:list[str], script_only_rows:frozenset[int]=frozenset())->dict[int,FinalRow]` where `human[user_id][row_idx]={kb:value}` and `FinalRow(values:dict[str,str], needs_review:bool, reason:str)`. Rule, in order: adjudication present → its values, `reason=""`; ≥2 users with a human decision whose values match in every KB column → that value, `reason=""`; ≥2 humans that differ → blank, `needs_review=True`, `reason="disagree"`; exactly 1 human → blank, `"single_annotator"`; 0 humans and the row is in `script_only_rows` → blank, `"script_only"`; otherwise blank, `"no_decisions"`.

- [ ] **Step 1: Write failing tests:** `test_percent_agreement_counts_shared_rows_only`; `test_kappa_perfect_and_chance`; `test_kappa_none_for_under_two_shared_rows`; `test_kappa_none_when_expected_agreement_is_one`; `test_three_users_give_three_pairs`; `test_final_prefers_adjudication`; `test_final_needs_two_matching_humans`; `test_final_disagree_in_one_kb_column_blocks_value`; `test_final_single_human_is_flagged_single_annotator`; `test_final_script_only_row_is_flagged`; `test_final_untouched_row_is_no_decisions`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(review): agreement and final-value rules`.

---

### Task 8: Review, agreement, adjudication and final export endpoints

**Files:**
- Create: `api/review_routes.py`, `tests/test_review_api.py`
- Modify: `eltool/decisions.py` (adjudications), `api/main.py` (include router)

**Interfaces:**
- Consumes: Tasks 6–7.
- Produces in `decisions`: `add_adjudication(session_name,row_idx,reviewer_id,kb_values:dict,note:str="")`; `current_adjudications(session_name)->dict[int,dict]` (latest per row).
- Endpoints (reviewer/admin, and the caller must be a member or admin; annotators → 403, non-members → 404): `GET /api/sessions/{name}/review` → `{kb_names, rows:[{idx,label,decisions:[{user_id,user_name,actor,script:bool,kb_values,llm_used,ts}],disagree:bool,adjudication|null}]}`; `GET /api/sessions/{name}/agreement` → `{by_kb:{kb:[pairwise…]}, excluded_script_rows:int}`; `POST /api/sessions/{name}/adjudications {row_idx, kb_values, note}`; `GET /api/sessions/{name}/export?final=1` → CSV of input columns + each KB final value + `_needs_review` + `_review_reason`; the endpoint computes `script_only_rows` from the log. Source of truth is `decisions.latest_human_or_script`; `script` is true when the latest non-feedback actor is not `human`; `disagree` considers human decisions only; removed members' work is still included.

- [ ] **Step 1: Write failing tests:** `test_review_lists_every_users_decision_with_script_tag`; `test_disagree_ignores_script_rows` (A human `C1`, B auto-match `C2` → `disagree` false); `test_agreement_excludes_script_rows_and_reports_count`; `test_script_row_counts_once_human_saves_it` (A auto-match, A then saves → counts); `test_adjudication_is_append_only_latest_wins`; `test_final_export_blank_when_only_one_human`; `test_final_export_value_when_two_humans_match`; `test_final_export_uses_adjudication`; `test_annotator_gets_403_on_review_agreement_adjudications_final_export`; `test_non_member_reviewer_gets_404`; `test_removed_member_decisions_still_counted`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** PASS + full suite.
- [ ] **Step 5:** Commit `feat(review): reviewer endpoints, adjudication, final export`.

---

### Task 9: Web auth foundation and Google login

**Files:**
- Create: `web/src/hooks/useAuth.tsx`, `web/src/pages/Login.tsx`
- Modify: `web/package.json` (add `firebase`), `web/src/api/client.ts`, `web/src/types/index.ts`, `web/src/App.tsx`, `web/src/components/NavBar.tsx`, `web/src/pages/Home.tsx`, `web/src/pages/Configure.tsx`, `web/src/pages/Annotate.tsx:556,566`

**Interfaces:**
- Produces in `client.ts`: `getInfo()` → `{hosted:boolean, auth:boolean, firebase:{apiKey,authDomain,projectId}|null}`; `sessionLogin(idToken:string)`, `logout()`, `me()`. `req()` throws an `ApiError` carrying `status`; a 401 outside `/auth/session` calls a registered `onUnauthorized` handler.
- Produces: `AuthProvider` + `useAuth()` → `{auth:boolean, user:User|null, loading:boolean, signInWithGoogle():Promise<void>, logout():Promise<void>, error:string|null}` with `User = {id,email,name,role}`; `RequireAuth` wrapper redirecting to `/login`; `RequireRole roles=[…]` hiding children. `signInWithGoogle` lazily initialises Firebase from `getInfo().firebase`, runs `signInWithPopup(GoogleAuthProvider)`, sends `await user.getIdToken()` to `sessionLogin`, then signs out of the Firebase client (HELIX's own cookie takes over).
- Behaviour: with `auth:false` the app is exactly today's. `/login` is public. Home hides "New session" and the Configure link unless admin. Configure hides "Annotator name" when `auth` is true. Annotate shows `user.name` instead of `cfg.annotator` when `auth` is true. NavBar shows name, role and Sign out. A 403 from `sessionLogin` shows "This Google account is not authorised for HELIX".

- [ ] **Step 1:** `cd web && npm install firebase@<pinned exact version>`; add the client functions and types; `npx tsc --noEmit` clean.
- [ ] **Step 2:** Build `useAuth`, `Login` (single "Sign in with Google" button, error text from the API) and the route / NavBar / Home / Configure / Annotate changes.
- [ ] **Step 3:** `cd web && npx tsc --noEmit && npm run build` both succeed.
- [ ] **Step 4:** Browser check with the API started `HELIX_AUTH=1` and an admin from `python -m eltool.users create-admin`: signed-out visit redirects to `/login`; with `HELIX_AUTH` unset the app loads with no login. **The Google popup itself needs the owner's Firebase project and their own Google account, so the owner performs that click; I verify everything around it** (redirect, error text for a refused account using a stubbed 403, signed-in layout via a cookie from `login_as`-equivalent script, Sign out).
- [ ] **Step 5:** Commit `feat(web): Google login and auth context`.

---

### Task 10: Users admin page

**Files:**
- Create: `web/src/pages/Users.tsx`
- Modify: `web/src/api/client.ts`, `web/src/App.tsx`, `web/src/components/NavBar.tsx`

**Interfaces:**
- Consumes: Task 3–4 endpoints.
- Produces: route `/admin/users` (admin only; NavBar link "Users"). Sections: **Invite** form (email, role select, session multi-select; on submit shows the returned sign-in URL with a Copy button and "already registered — sessions added" when applicable); **Invites** table (email, role, status pending/accepted/revoked, Revoke on pending); **Users** table (name, email, role select, active toggle, session membership editor). Show the API's 409 message when the last admin is protected.

- [ ] **Step 1:** Add client functions: `listUsers`, `patchUser`, `setUserSessions`, `listInvites`, `createInvite`, `revokeInvite`.
- [ ] **Step 2:** Build the page with react-query, matching the existing `card` / `btn-primary` Tailwind classes.
- [ ] **Step 3:** Verify `npx tsc --noEmit && npm run build`.
- [ ] **Step 4:** Browser check (signed in through a seeded cookie): invite an address → URL appears and the invite shows pending; revoke it; invite an already-registered address → "sessions added"; try demoting the only admin and see the 409 text; an annotator account gets a "not allowed" state at `/admin/users`.
- [ ] **Step 5:** Commit `feat(web): users and invitations admin page`.

---

### Task 11: Review page

**Files:**
- Create: `web/src/pages/Review.tsx`
- Modify: `web/src/api/client.ts`, `web/src/App.tsx`, `web/src/pages/Home.tsx` (session row gets a "Review" link for reviewer/admin)

**Interfaces:**
- Consumes: Task 8 endpoints.
- Produces: route `/review/:sessionName` (reviewer/admin). Layout: summary strip (per-KB pairwise % agreement and kappa, `excluded_script_rows`), filter chips (All / Disagreements / Script-only / Needs review), a row table showing the mention label and one column per annotator with their value, a "script" badge on script rows, disagreements highlighted, an adjudicate action (inline KB value inputs prefilled with the agreed or first human value, optional note) and an "Export final" button linking `/api/sessions/{name}/export?final=1`.

- [ ] **Step 1:** Add client functions: `getReview`, `getAgreement`, `postAdjudication`.
- [ ] **Step 2:** Build the page.
- [ ] **Step 3:** Verify `npx tsc --noEmit && npm run build`.
- [ ] **Step 4:** Browser check with two annotator accounts (seeded cookies) on one session: they annotate the same three rows (two matching, one different) — the review page highlights the one disagreement, agreement shows 66.7 %, adjudicating it removes the highlight, and the final export contains all three values with no `_needs_review`. An annotator account sees a "not allowed" state at `/review/…`.
- [ ] **Step 5:** Commit `feat(web): reviewer page with agreement and adjudication`.

---

### Task 12: Deployment, docs, end-to-end smoke

**Files:**
- Modify: `deploy/hosted/Caddyfile`, `deploy/hosted/docker-compose.yml`, `deploy/hosted/.env.example`, `deploy/hosted/README.md`, `.env.example`, `README.md`

**Interfaces:** none.

- [ ] **Step 1:** Caddyfile: remove the `basic_auth` block (keep `reverse_proxy web:80`). Compose: drop `HELIX_BASIC_USER` / `HELIX_BASIC_HASH`; add `HELIX_AUTH: "1"`, `HELIX_PUBLIC_URL: https://${HELIX_DOMAIN}` and pass-through `HELIX_FIREBASE_PROJECT_ID`, `HELIX_FIREBASE_API_KEY`, `HELIX_FIREBASE_AUTH_DOMAIN` to the `api` service. `.env.example`: remove the basic-auth lines, add the three Firebase values with a one-line note that they are public web identifiers.
- [ ] **Step 2:** `deploy/hosted/README.md`: replace the basic-auth text with "Firebase and accounts": create a Firebase project, enable the Google provider, add `HELIX_DOMAIN` to authorised domains, copy the web config into `.env`; bootstrap with `docker compose … exec api python -m eltool.users create-admin --email …`; inviting (copy the sign-in URL); losing a Google account; review and final export; the backup now contains `helix.db`. Rewrite the smoke checklist: signed-out `/api/sessions` → 401; sign in with Google as the admin; invite a second Google account → it signs in and sees only the granted session; an uninvited account is refused; two annotators on one session produce two files under `annotations/`; `docker compose … ps` still shows only 80/443. Add a short "Accounts" paragraph to the root `README.md`.
- [ ] **Step 3:** Verify: `docker compose -f deploy/hosted/docker-compose.yml --env-file deploy/hosted/.env.example config` parses (with a dummy `HELIX_DOMAIN`); full `pytest tests -q` green; `cd web && npx tsc --noEmit && npm run build` green.
- [ ] **Step 4:** End-to-end local run with the owner's Firebase project: admin signs in, creates a session from a small CSV, invites two annotators and a reviewer, both annotators complete the same rows (one runs Auto-match on a row), the reviewer sees the script tag, resolves the disagreement and downloads the final export; `GET /api/admin/decisions.csv` shows every action with the right actor. The Google sign-ins are done by the owner; record what was seen in the commit body.
- [ ] **Step 5:** Commit `feat(auth): hosted deployment uses app accounts; docs`.

---

## Self-review notes

- **Spec coverage:** §5.1 switch → T1/T2; §5.2 tables → T1/T2/T3/T4/T6/T8; §5.3 authentication → T2/T3 (+ T9); §5.4 invitations → T3 (+ T10); §5.5 roles/access → T2/T4/T5; §5.6 layers and log → T5/T6; §5.7 review/agreement/export → T7/T8/T11; §5.8 bootstrap/deploy → T2/T12; §5.9 UI → T9–T11. Non-goals (other providers, self sign-up, MFA, assignment, email sending, legacy adoption script) have no task, as specified.
- **Names used across tasks:** `session_access`, `require_admin`, `require_reviewer`, `_out`, `_who`, `login_as`, `make_user`, `decisions.transaction().add(...)`, `latest_human_or_script`, `current_adjudications` are defined once and used identically in later tasks.
- **Branch and commits:** every task ends with a commit; cut `feature/user-management` before Task 1, from `main` (or from `fix/auto-match-keeps-saved` if that fix is not yet merged — the study needs it).
- **Owner steps:** create the Firebase project and enable Google sign-in (Task 12 README), and perform the Google sign-in clicks in Tasks 9 and 12; I cannot enter Google credentials.
