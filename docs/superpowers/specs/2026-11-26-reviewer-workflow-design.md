# Reviewer workflow — design

Date: 2026-11-26. Builds on `2026-11-25-user-management-design.md` (PR #7) and
replaces its adjudication screen, final-export rule and reviewer permissions.

## Intent

The reviewer is the judge of the annotators' work: they look at what each
annotator decided and settle the final answer for every row. The reviewed set is
a research gold set, so every final label must be traceable to a person.

**Goals**

- Decide a row from the annotators' decisions in a few clicks.
- Record how each final answer was reached and whose answers it came from.
- Compare multi-concept answers correctly.

**Non-goals:** automatic resolution rules, annotator weighting, AI tie-breaking,
blind names, spot-check sampling, graph hints, sending rows back to annotators.

**Anti-goals:** a final label nobody looked at; a screen that nudges the reviewer
towards one annotator.

## Approaches

| Approach | Trade-off | |
|---|---|---|
| Accept one answer, or combine concepts from several | One click for the common case, still handles mixed answers | **Chosen** |
| Accept exactly one annotator's answer | Simplest; cannot fix an answer that is half right | Rejected |
| Judge every proposed concept correct or wrong | Most detailed; most clicks | Rejected |
| Rules decide, reviewer confirms | Fast; misreads the reviewer's role | Rejected |

## Decided with the project owner

1. The reviewer accepts one annotator's whole answer, or ticks individual
   concepts across annotators.
2. If no annotator is right, the reviewer may enter another concept or NIL. This
   is recorded as an override and requires a note.
3. Disagreements are decided one by one. Rows where annotators agree can be
   confirmed together.
4. The reviewer role cannot annotate. Admins can do both.

## Design (builder's calls unless listed above)

### Comparing answers

An answer for one KB is a set of concept ids: split on `|`, trim, drop blanks
and duplicates. `NIL` is single-valued and never combined with a concept. Two
answers are equal when their sets are equal in every KB. Only human decisions
count; script-written rows stay excluded.

### Row status

| Status | Condition | How it is decided |
|---|---|---|
| `disagree` | Two or more annotators, answers differ | One by one |
| `single` | Exactly one annotator has answered | One by one |
| `agreed` | Two or more annotators, all equal | Bulk or one by one |
| `changed` | Decided, then an annotator's answer changed, appeared or disappeared | One by one |
| `decided` | Has a current reviewer decision | Can be re-decided |
| `waiting` | No annotator answer | Counted, not decidable |

Bulk confirm includes an `agreed` row only when every active session member with
the annotator role has answered every KB. Otherwise the row shows how many
annotators are still pending. The server re-checks each row at save time and
skips, and reports, any that no longer qualify.

### Decision record

`adjudications` stays append-only and gains:

- `basis`: `accepted` (equals one or more annotators' full answer), `combined`
  (every concept was proposed by some annotator for that KB), `override`
  (anything else) or `bulk_agreed`.
- `source_users`: the annotators whose answers the final answer came from.
- `based_on`: each annotator's normalised answer at decision time, used to
  detect `changed`.

The server derives `basis` and `source_users`; the browser cannot set them. An
override without a note is refused. Every KB needs a value or `NIL`. Decisions
saved before this change are kept, shown as `legacy`, and never marked changed.
Existing databases get the new columns on startup.

### Review screen

- Header: agreement panel and **Export final**.
- Filters with counts: To decide (`disagree`, `single`, `changed`), Agreed,
  Decided, All.
- A row list; opening a row shows one decision card with previous/next.
- Card: mention, document id, document context when the session has it, one line
  per annotator with concept names and an **Accept** button, then the final
  answer per KB as tickable chips, a field to add another concept (looked up in
  the KB; unknown ids allowed with a warning), a note and **Confirm**.
- Pre-ticked: only concepts every answering annotator chose, and only when two
  or more answered. Nothing is taken from a single person by default.
- Agreed view: checkboxes and **Confirm selected**.
- Concept names come from the existing KB entity lookup; if the KB cannot be
  reached the id is shown.

### Agreement

Per KB and annotator pair: exact-set match percentage and Cohen's kappa over the
normalised sets, plus mean overlap (Jaccard) so partial agreement is visible.

### Final export

Only reviewer-decided rows get values. New columns `_basis` and `_decided_by`.
Every other row is blank with `_needs_review=1` and `_review_reason` one of
`disagree`, `single_annotator`, `awaiting_review` (agreed, not confirmed),
`changed_since_review`, `script_only`, `no_decisions`. This replaces the earlier
rule that made agreement final automatically.

### Permissions

For the reviewer role, every write to annotations returns 403: row and batch
save, feedback, reflection, auto-annotate, graph optimise and snapshot. The
session list shows reviewers a **Review** button with decided/total counts and no
Annotate button; `/annotate/…` redirects to the review page. Answers a person
saved before becoming a reviewer remain annotator answers.

## Testing

Unit tests for set comparison, status, basis derivation and export reasons; API
tests for each refusal, bulk-confirm skipping, change detection, the column
migration and every blocked reviewer write; a browser pass over the review
screen with two seeded annotators.

## Left to the builder

Component split, endpoint shapes, visual styling within the existing classes.

## Delivery

On `feature/user-management`, as further commits to PR #7.
