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

Run from `server/`, with Python's standard library only:

```sh
python3 scripts/run_car_heater_ui_prototype.py
```

Open <http://127.0.0.1:8765/car_heater?variant=A>. The floating bar changes layouts
with its arrows or the keyboard's left/right arrows. Input fields keep their
normal keyboard behavior. The chosen layout and sample state stay in the URL;
other state lives only in memory and resets on reload.

| Layout | Structure | Review focus |
| --- | --- | --- |
| A · Departure first | Large departure time and B's timeline; plan in the main column, manual controls and conditions alongside | Calm everyday use, with a clear reading order on a phone |
| B · Timeline workspace | Departure and a horizontal start/departure/finish timeline; planning, manual control and measurements below | Seeing the heating window at a glance |
| C · Control console | Side navigation on desktop; current plan in a compact control surface, actions below | Compact operation and room for review tools |

All layouts have Heating, Sessions and Settings. The sample-state selector covers
scheduled, heating, disconnected, session review and camera capture. States can
also be linked directly, for example:

- <http://127.0.0.1:8765/car_heater?variant=B&state=heating>
- <http://127.0.0.1:8765/car_heater?variant=C&state=disconnected>
- <http://127.0.0.1:8765/car_heater?variant=A&state=review>
- <http://127.0.0.1:8765/car_heater?variant=A&state=capture>

Try changing a departure time, choosing a manual duration, stopping an active
session, requesting OFF while disconnected, reviewing a session with missing
measurements, and finishing an observation while heating is still ON. Camera
capture, uploads, countdowns, outcomes, preference saves and restarts are
simulations. The camera image is a labeled SVG illustration; this prototype does
not test iPhone camera access or ultra-wide lens selection. Settings are a
representative layout, not an exhaustive recreation of the existing settings.

The host reproduces the existing `/car_heater` URL in an **isolated local process**
and reuses the application's navbar styling. This deliberately avoids starting
the application's configured integrations or using live data during control
experiments. Other application navigation links are disabled. The production
route, authentication and controls are untouched; the prototype assets and
switcher are loaded only by the isolated runner. No database or external API is
used, and no device commands are sent.

For a phone on the same trusted local network, run:

```sh
python3 scripts/run_car_heater_ui_prototype.py --host 0.0.0.0
```

Then open `http://<this-computer's-LAN-IP>:8765/car_heater?variant=A` on the phone.
This sample-only server has no login. HTTP is sufficient for these simulated
views; the actual camera experiment will require HTTPS and an iPhone trial.

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
