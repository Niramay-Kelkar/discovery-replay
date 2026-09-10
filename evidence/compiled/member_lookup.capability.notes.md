# Compile notes -- member_lookup

Compiled from trajectory `disc-20260909-182538`.

Every entry below is a decision the compiler made that is not a
1:1 copy of the trajectory. The policy layer of the artifact
(risk class, expected outcomes, escalation, denylist) comes
entirely from the hand-authored PolicySpec and is not listed
here.

## Synthesized steps

- step 1 `open_entry`: navigate to entry_path '/'. This is the one step not sourced 1:1 from a TrajectoryStep -- the discovery loop navigates to the entry point before the first model turn, so it never appears as a tool call. It is compiled from `trajectory.entry_path`, a recorded fact, so replay starts from the same place. Mechanical, not policy.

## Locator generalizations

- step for "Open the member detail page to see Alice Nguyen's savings balance": resolved accessible name 'Open detail for Alice Nguyen, member M1001' contains member-specific data; substring match on the stable prefix 'Open detail for' (the rest -- the member name and id -- is not known before this step); nth=0 -- a substring match can hit several result rows, take the first

## Value parameterizations

- output name normalized: 'member_name' (discovered) -> 'full_name' (contract)
- fill step: literal 'M1001' parameterized to input {{search_term}}
- extract 'full_name': locator + checkpoint compiled from the label 'Full name', not the discovered value 'Alice Nguyen'
- extract 'savings_balance': locator + checkpoint compiled from the label 'Savings balance', not the discovered value '$18,750.42'

## Trajectory gaps (NOT filled by the compiler)

- (declared in PolicySpec) The 'search_field' radio (Member ID / Last name) was never clicked during discovery -- 'Member ID' is pre-checked and was valid for the M1001 lookup. This capability therefore searches by member ID only. A two-mode capability needs either a second discovery run forcing the 'Last name' path, or a hand-authored 'choose_search_field' step reviewed as a deliberate act. The compiler adds neither: it emits the narrower single-mode capability. (Contrast schema/example_artifact.json, which is hand-authored and does assert both modes.)
- extract steps carry no `within` scope: discovery resolved the value cells directly and never resolved the enclosing 'Member record for ...' table, so the compiler has no verified accessible name to scope to. Locators fall back to a page-wide rowheader match. A second discovery pass that resolves the record table (or a hand-authored `within`) would harden these against a same-labelled row elsewhere on the page. The compiler does not guess the table's name.

## Guardrail narrowing

- allowlist_routes narrowed to the routes visited on the successful trajectory: /, /member/*, /search
- allowlist_action_types set to exactly the verbs the steps use: click, extract, fill, navigate
