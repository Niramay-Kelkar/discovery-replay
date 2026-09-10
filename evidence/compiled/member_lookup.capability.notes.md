# Compile notes -- member_lookup

Compiled from trajectory `disc-20260910-084016`.

Every entry below is a decision the compiler made that is not a
1:1 copy of the trajectory. The policy layer of the artifact
(risk class, expected outcomes, escalation, denylist) comes
entirely from the hand-authored PolicySpec and is not listed
here.

## Synthesized steps

- step 1 `open_entry`: navigate to entry_path '/'. This is the one step not sourced 1:1 from a TrajectoryStep -- the discovery loop navigates to the entry point before the first model turn, so it never appears as a tool call. It is compiled from `trajectory.entry_path`, a recorded fact, so replay starts from the same place. Mechanical, not policy.

## Locator generalizations

- step for 'Switch the search field to Last name as required by the goal': templated input 'Last name' -> {{search_field}}
- step for 'Open the member record for James Okafor': resolved accessible name 'Open detail for James Okafor, member M1004' contains member-specific data; substring match on the stable prefix 'Open detail for' (the rest -- the member name and id -- is not known before this step); nth=0 -- a substring match can hit several result rows, take the first

## Value parameterizations

- fill step: literal 'Okafor' parameterized to input {{search_term}}
- extract 'full_name': locator + checkpoint compiled from the label 'Full name', not the discovered value 'James Okafor'
- extract 'savings_balance': locator + checkpoint compiled from the label 'Savings balance', not the discovered value '$2,219.75'

## Trajectory gaps (NOT filled by the compiler)

- (declared in PolicySpec) RESOLVED (was: the search_field radio was never exercised). disc-20260910-084016 drives the 'Last name' radio for real, so the compiled capability now has a mechanically-derived choose-search-field step and a closed-enum search_field input covering both modes. Verified by replaying the recompiled artifact against the live app with search_field='Last name' and search_field='Member ID' -- see the 'What changed in this recompile' section of the *.notes.md sidecar and BUILD_LOG.md.
- extract steps carry no `within` scope: discovery resolved the value cells directly and never resolved the enclosing 'Member record for ...' table, so the compiler has no verified accessible name to scope to. Locators fall back to a page-wide rowheader match. A second discovery pass that resolves the record table (or a hand-authored `within`) would harden these against a same-labelled row elsewhere on the page. The compiler does not guess the table's name.

## Guardrail narrowing

- allowlist_routes narrowed to the routes visited on the successful trajectory: /, /member/*, /member/M1004, /search (plus hand-authored extras: /member/*)
- allowlist_action_types set to exactly the verbs the steps use: click, extract, fill, navigate

## What changed in this recompile (v1.0.0 -> v1.1.0)

This artifact supersedes the one compiled from `disc-20260909-182538`.
That earlier run only ever drove a Member-ID search: "Member ID" is the
pre-checked radio, so it was never clicked, and the compiled capability
was honestly narrowed to ID-only with the gap recorded as a known gap.

`disc-20260910-084016` is a fresh live discovery run whose goal forced
the agent to click the "Last name" radio before searching. What that
buys the capability, concretely:

- **`click_search_field` step (new).** Compiled from a real, live-
  resolved `click` on `role=radio name="Last name"`. Its locator is
  `role=radio name={{search_field}}` -- the discovered literal templated
  against the `search_field` InputBinding. Replay selects whichever
  radio the caller's `search_field` value names.
- **`search_field` input (new).** A closed enum, `["Member ID",
  "Last name"]` -- the two accessible names the radio exposes. The
  capability is now two-mode by construction, not by hand-assertion
  (contrast the hand-authored `schema/example_artifact.json`, which
  asserts both modes without a run behind the radio step).
- **`search_term` still parameterized**, now from the last name
  `"Okafor"` rather than the ID `"M1001"`.
- **`extra_allowlist_routes=["/member/*"]`.** The last-name flow lands on
  `/member/M1004`, where `M1004` is the id the search returned, not an
  input value -- so route narrowing leaves it literal. `/member/*` is
  added by hand so replay can reach whichever member it looks up.

Why it is genuinely more complete, not just better documented: the
recompiled artifact was replayed against the live app in **both** modes
--- `search_field="Last name", search_term="Okafor"` (the exact
discovery combination) and `search_field="Member ID", search_term="M1001"`
(a combination never observed during discovery) --- and both returned
Success with the correct record. The Member-ID path is now proven by
execution, and the Last-name path exists at all, which it did not
before.

Compiler change that made this possible: `_generalize_name` now runs the
member-data truncation against the *raw* resolved name before input
templating. Templating a search term that is a substring of an extracted
value (a last name inside a full name -- "Okafor" inside "James Okafor")
would otherwise split the value and hide it from the truncation check,
shipping a broken exact-match locator on the "Open detail for ..." link.
