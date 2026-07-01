# doc-verify — Requirements

## Purpose
Independently re-review the findings produced by `doc-review` against the source
document: reject false positives, adjust mis-rated/mis-categorized findings,
confirm valid ones, and add clearly-missed major issues. The "review of the
review" — a quality gate favoring precision.

## Scope
- **Read-only, no Polarion access.** Operates only on two input files:
  the content snapshot and the findings JSON (both from `doc-review`).
- Uses the **same finding schema** defined in
  `skills/doc-review/requirements.md`. After verification every finding carries
  a `verdict { status: confirmed|adjusted|rejected, confidence, note }`.

## Why a separate skill (not a rule inside doc-review)
Independence: a separate instance does not inherit the reviewer's reasoning, so
it genuinely re-checks rather than rationalizing. It also keeps each skill's
prompt small and focused.

## Policy
- Re-locate every finding's evidence in the source; reject if absent or already
  satisfied.
- Lean **reject** when uncertain.
- May add a new finding only for a clear **major** miss within the scope of the
  checklist being applied.
- Keep rejected findings in the output (with their rejected verdict) so the
  report can show what was filtered.
