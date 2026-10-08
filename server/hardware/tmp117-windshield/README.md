# TMP117 windshield holder — replacement DRAFT (#20)

This replaces the old tape-retained carrier/foam bridge under
[spec #19](https://github.com/jannesii/raspberry-pi-smart-home/issues/19).
All STEP and STL exports are **nominal drafts**, not physically qualified prints.
Use two identical holders for the 2005 Golf V: lower-middle driver viewing area
and lower driver-side band above the black border. The third TMP117 is a spare.
Board arrival/trial fitting remain in #22, measured finalization in #23;
preparation #11 and subsequent installed commissioning remain open gates.

The taped base surrounds an open PCB window. Nothing printed or taped goes
under the backside of the isolated sensor tongue. Apply thin MX-4 only to the
backside contact patch against glass. The paste does not retain the board.
The 26 × 18.38 mm aperture walls locate the board perimeter with a draft
0.3 mm clearance per side. Four printed retainers reach nominal outer corner lands; these are proposed
bearing locations, requiring inspection of the actual board before tightening.
Do not pass BT3 through the board's 2.5 mm mounting holes, remove connectors,
or bend the tongue. Polyimide tape is optional during trial fitting only.

The airflow cover seats on four structural stops, separately from the board
retainers. Its roof cannot be pulled down by tightening against seated stops.
An initial 8 × 6 mm E30 pad, cut to at most 8 mm thickness from the nominal
10 mm stock, sits on the air-facing tongue region. Check components and cut
its footprint to fit. The pad is replaceable after removing the cover. The
nominal stack has clearance below the roof: **no compression ratio or contact
force is specified**. Adjust pad and contact stack by trial fitting, never by
cover screw torque. The partial side/end shields protect the central pad from
direct cabin airflow; plug and service openings make this intentionally unsealed.

## Parts and hardware per holder

| Bed-oriented STL / exact STEP stem | Quantity | Function |
| --- | ---: | --- |
| `base` | 1 | Tape frame, vertical locating edges, blind-pilot bosses/stops |
| `cover` | 1 | Roof-down printed airflow shield and stopped closure |
| `retainer` | 4 | Top-down printed removable corner-land fingers |
| `cable_clamp` | 2 | Top-down printed independently screwed cable bridges |
| `pilot_coupon` | 1 per trials | 2.45 / 2.55 / 2.65 mm pilot ladder |
| `fit_coupon` | 1 per trials | Reduced-height PCB/plug/retainer/contact-stack trial frame |

See [model catalog](src/README.md). `STEP/assembly.step` places the eight
printed parts in installation orientation; never print this assembly STL.
Board, glass, tape, paste, foam, plugs and screw surrogates are excluded from
exports because their actual interfaces remain unmeasured. Their *allowance*
envelopes are tested separately. Base/assembly envelope: 64 × 36 × 17 mm.

Use **12 BT3×6 screws** per holder: four cover, four retainers, two per clamp.
All structural pilots are 2.55 mm; part clearance holes are 3.4 mm. Vendor
BT3 head envelope is 5.5 mm diameter × 1.9 mm height; H2 driver. The checks
reserve a 6 mm cylindrical tool corridor. The cover and retainer joints give
4 mm nominal engagement; clamps give 2 mm. Longer stocked screws are **not
interchangeable without rechecking tip depth**. No BT3×5 is required. The blind
pilots preserve a floor; seated BT3×6 ends remain at least 2 mm above the base
underside. The coupon establishes whether 2 mm clamp engagement is adequate;
change boss/joint geometry if it strips or slips. Do not substitute pilot
ladder alternatives into production joints without agreed/measured adjustment.

## Nominal facts, allowances and measurements

Manufacturer facts remain in [board-layout.json](src/lib/board-layout.json):
pinned Eagle outline 25.4 × 17.78 mm, rounded corners, sensor/slot and connector
positions. Nominal hole pitch is 20.32 × 12.7 mm and drill 2.5 mm. Product 4821's
25.5 × 17.7 × 4.6 mm overall envelope is **not PCB thickness**. No assembled
vendor STEP is used; the prior step.parts search returned no TMP117 model.

`src/lib/geometry.py` holds the configurable stack and geometry factories.
All dimensions are mm, nominal PCB centred XY; base underside Z=0 and installed
glass at Z=−tape thickness. Draft visualization assumptions are PCB thickness
1.6 mm, installed tape 0.15 mm and paste 0.1 mm. Thus PCB backside Z=−0.05,
PCB top Z=1.55; the finger underside is 0.1 mm above that nominal top. This
small allowance is **not demonstrated retention**: adjust against delivered
lands until the board stays seated without bending. Tape thickness changes
the retainer finger depth; the cover stop remains independent at Z=15.

The two plug envelopes reserve 8 mm reach beyond either board edge, 8 mm
width and 4 mm height starting at Z=2.6. Both exit straight toward clamp
centres X=±27 mm. Leave slack between plug and clamp; bends occur outside the
mount. The initial clamp opening is 4 mm wide × 3 mm high, not a cable diameter
measurement or claim of gripping. Resize its opening after measuring the
insulated bundle and test actual grip/slack; an undersized bundle may slip.
Anchor the heavier extension separately. Intermediate boards can use both
ports; end boards can use one and omit the unused clamp screws/bridge.

Before final release record delivered board revision/outline, thickness,
backside protrusions/flatness, component heights and safe bearing lands;
mated plug envelope, cable bundle/bend radius; installed tape/paste thickness;
actual foam/pad height; local glass curvature, tape attachment and clean removal.
Unknown `_measured` parameters stay `None`. `ready_for_final_print=True`
requires all measurements plus confirmed layout/lands and a passed physical
trial. Invalid/missing dimensions reject release. These flags are an operator
record, not an automated physical certification. Enlarged plugs or changed
boards require revising geometry and its dimensional checks, not bypassing the
gate. Synthetic release-test values never fill delivered measurements.

## Print and coupon sequence

Use PETG, Bambu A1/0.4 mm, a starting 0.2 mm layer height and four walls with
the appropriate spool profile. All supplied STLs start on Z=0. The base/coupons
print frame down; the cover prints roof down; retainers/clamps print flat tops
down with fingers/feet pointing up. No downward faces above the bed remain in
the meshes, so there are no designed bridges or overhangs requiring supports.
**Slicer toolpaths have not been inspected** (no slicer available here); inspect
A1/PETG/0.4 mm toolpaths with supports disabled before printing. Aperture fit needs
preview and coupon confirmation with the 0.4 mm nozzle. Deburr all glass-facing
edges; the frame is planar and has no claim of measured windshield conformity.
Material temperature testing is excluded by owner decision.

1. Print `pilot_coupon` and one clamp/retainer first. Left-to-right along X:
   2.45, 2.55 (selected), 2.65 mm; the common bosses are 7 mm diameter, 6 mm tall
   above a 2 mm floor. Try BT3×6, check thread hold, splitting, and several
   removal cycles. Coupon 6 mm boss engagement differs from the real joints:
   also test the actual clamp's shorter 2 mm engagement on `fit_coupon`.
2. Print `fit_coupon` with four retainers and two clamps. Its board window and
   joint datums match the base; tall cover posts are truncated. Measure printed
   aperture/pilot/clearance sizes and check the actual PCB and both fully mated
   plugs. Trial on flat glass using the actual installed tape thickness.
3. Adjust finger depth/lands, pad and clamp opening. Confirm no tongue/board
   bending, unplugging access, cable grip with slack, and board lift toward
   cabin after releasing retainers. Check several service cycles. Use the coupon
   on both intended windshield sites to detect rocking from local curvature.
4. Only then print full base/cover, repeat trial fitting, and proceed to #23.
   Attachment retention and installed bias/lag/bus tests remain commissioning.

## Assembly and service

1. Deburr/clean prints away from the board. Apply double-sided tape to outer
   frame underside only, entirely outside the PCB window. Confirm local glass
   curvature and nondamaging removal before relying on attachment.
2. Place the intact PCB through the open window onto the thin MX-4 patch.
   Fit retainers on the four base bosses with fingers facing inward. Seat
   BT3×6 carefully; stop if contact bends the board or tongue. The retained
   board must stay in contact with glass when cable loads are applied.
3. Connect one or both plugs. Arrange slack, seat insulated bundles in the two
   external bridges, and fasten each with two BT3×6. Check grip without crushing
   insulation. Clamps are independently accessible with the cover fitted.
4. Lay the trimmed E30 pad on the air-facing tongue region without chip/slot
   side loading. Fit the cover on all four stops and use four BT3×6. Confirm
   seated closure cannot force the pad/tongue. The pad need not be glued.
5. For board service: remove cover/pad, release clamps, unplug gently, remove
   four retainers, then lift the board toward the cabin. The taped base remains
   in place; do not slide the tongue over glass. Reverse to reinstall.

## Reproduce and inspect

Run from this hardware directory; CAD dependencies stay separate from server.

```sh
uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.17 python src/assembly.py
uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.17 python src/pilot_coupon.py
uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.17 python src/fit_coupon.py
uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.17 python checks/check_redesign.py
uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.17 python checks/check_geometry.py
uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.17 python checks/test_release_gate.py
uv run --no-project --with trimesh --with numpy --with scipy python checks/check_mesh.py
uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.17 cadgen step snapshot --job checks/views.json
```

Geometry/mesh outputs are in `reports/`, reviewed snapshots in `views/`.
Read [validation limits](reports/validation.md). Geometry checks prove draft
allowances and topology only; actual components may violate those envelopes.
