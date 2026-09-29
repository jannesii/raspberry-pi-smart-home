# Issue tracker: GitHub

Issues and specs for this repo live in GitHub Issues. Use `gh` from this
repository so it resolves the origin automatically.

## Conventions

- Create: `gh issue create --title "..." --body-file <file>`.
- Read: `gh issue view <number> --comments`; fetch labels as needed.
- List: `gh issue list --state open --json number,title,body,labels,comments`
  with suitable state and label filters.
- Comment: `gh issue comment <number> --body-file <file>`.
- Label: `gh issue edit <number> --add-label "..."` or
  `--remove-label "..."`.
- Close: `gh issue close <number> --comment "..."`.

When a skill says "publish to the issue tracker", create a GitHub issue.
When it says "fetch the relevant ticket", read that issue and its comments.

## Pull requests as a triage surface

**PRs as a request surface: no.**

## Wayfinding operations

For `/wayfinder`, use one issue labeled `wayfinder:map` as the map and
GitHub sub-issues as child tickets. If sub-issues are unavailable, list
children in the map body and put `Part of #<map>` in each child issue.

Label children `wayfinder:<type>` (`research`, `prototype`, `grilling`,
or `task`). Use native issue dependencies for blockers when available;
otherwise record `Blocked by: #<n>` in the child body. An unassigned
child with no open blockers is available to claim. Claim with
`gh issue edit <n> --add-assignee @me`. Resolve by commenting on and
closing the child, then add its result to the map's decisions.
