# Car-heater UI prototype

**Verdict approved by the owner on 2026-10-07: A with B's timeline.**

Keep A's departure-first hierarchy and two-column desktop layout, borrowing B's
start/departure/latest-finish timeline. This combines a calm everyday workflow
with an explicit heating window. On phones, keep that same reading order in a
single column. Variant A now previews the approved combination; B and C remain
available as comparison references.

Question: which hierarchy makes departure planning, confirmed heating status and
windshield observations easiest to use on an iPhone, while supporting desktop
measurement review? The agreed requirements remain in the
[defrost strategy](car-heater-defrost-strategy.md#car-heater-uiux-redesign-discussion-2026-10-07).

The prototype host, switcher, sample states and illustrated camera were removed
from the application tree when the redesign was completed (#18), so none of them
ships with or loads on the operating page. They remain in Git history; to look at
the comparison again, restore them into a scratch checkout rather than the app:

```sh
git worktree add ../prototype-review 9293b68
cd ../prototype-review && python3 scripts/run_car_heater_ui_prototype.py
```

Open <http://127.0.0.1:8765/car_heater?variant=A> (B and C for the references).
It is a standard-library, sample-only local host: no login, database, devices or
external APIs, and its controls are simulations. All layouts have Heating,
Sessions and Settings; sample states cover scheduled, heating, disconnected,
session review and camera capture.

| Layout | Structure | Review focus |
| --- | --- | --- |
| A · Departure first | Large departure time and B's timeline; plan in the main column, manual controls and conditions alongside | Calm everyday use, with a clear reading order on a phone |
| B · Timeline workspace | Departure and a horizontal start/departure/finish timeline; planning, manual control and measurements below | Seeing the heating window at a glance |
| C · Control console | Side navigation on desktop; current plan in a compact control surface, actions below | Compact operation and room for review tools |

The verdict settles the visual direction, not production implementation. This
code should not become the production implementation. On 2026-10-07, the owner
selected `/to-spec` and the full redesign was added to
[spec #2](https://github.com/jannesii/raspberry-pi-smart-home/issues/2), including
both required delivery milestones and the confirmed browser testing approach.
The owner subsequently approved `/to-tickets`: existing tickets #5, #7, #8,
#9, #10 and #12 were updated, with new sub-issues
[#14](https://github.com/jannesii/raspberry-pi-smart-home/issues/14) through
[#18](https://github.com/jannesii/raspberry-pi-smart-home/issues/18) covering manual
controls and the remaining advanced redesign/completion. All native blockers
and visible Blocked by sections were verified after publication. Ticket #14
is required for first-frost commissioning; #15–#18 remain required later work.

The prototype workflow calls for preserving the comparison on a throwaway
branch and linking its verdict from the implementation issue. That capture is
pending explicit commit authorization, following the repository's instruction
not to commit unless asked. The prototype and the approved decision currently
remain uncommitted. The published spec records the verdict independently of
these local artifacts; a committed prototype-source pointer is still pending.
