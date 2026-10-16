# Sensor-holder design research

Research date: 2026-10-08. Purpose: supply evidence for `/grill-with-docs`
before choosing replacement concepts or generating CAD. This is **not an
approved design, implementation plan, or qualification of either existing holder**.
The owner subsequently confirmed the conceptual design; the authoritative
implementation requirements are published in
[specification #19](https://github.com/jannesii/raspberry-pi-smart-home/issues/19).
Research alternatives below do not override those selected requirements.

## Project context and evidence labels

The [defrost review](car-heater-defrost-strategy.md#sensor-holder-redesign-discussion-2026-10-08)
records the owner's decisions: redesign both holders from scratch; distinguish
windshield surface sensing from cabin air sensing; keep vehicle attachment
removable and nondamaging; service the TMP117 board and insulation without moving
the base; protect the SHT45 board/connector while admitting air; use tape only
when a better solution is unavailable; avoid generated supports unless necessary.
The [preparation issue](https://github.com/jannesii/raspberry-pi-smart-home/issues/11)
tracks receipt and physical mount preparation; installed wiring and thermal
checks belong to later commissioning, not nominal CAD checks.

**Owner/Repo:** three TMP117 4821 boards (two active, one spare), one SHT45 PTFE
6174, four 200 mm connector cables, 4 × 0.14 mm² extension wire, MX-4 paste,
polyimide tape and heat shrink were ordered. PLA, PETG, Bambu TPU for AMS and
nominal 10 mm E30 foam are available, along with M2–M5 bolts, nuts, washers and
BT3×5–BT3×30 self-tapping screws. Receipt is unconfirmed. See the
[order confirmation dated 2026-10-07](https://github.com/jannesii/raspberry-pi-smart-home/issues/11#issuecomment-6044893790).

- **Verified:** directly supported by a linked manufacturer document or source.
- **Inference:** engineering interpretation for this application, requiring agreement/testing.
- **Unknown:** not established by the retrieved sources or actual measurements.

The owner subsequently accepted multiple printed parts when useful for
support-free printing or servicing, year-round installation, and keeping boards
and connectors intact (normal jumper/cable soldering allowed). The printer is a
Bambu Lab A1 with a default 0.4 mm nozzle; 0.2, 0.6 and 0.8 mm are available if
needed. Boards have not arrived. The owner also has 25 mm Bambu HA008 mushroom-head
suction cups, but questions their long-term retention; suction is not selected.
After comparing alternatives, the owner selected available double-sided tape for
windshield-base attachment during current testing. Suction remains unselected.
Cover closure against structural stops and occasional service are agreed;
self-tapping printed-part joints use an owner-specified 2.55 mm pilot. A ventilated
SHT45 housing with a screwed cover and separate mounting adapter is the accepted
concept direction. These later decisions do not qualify the material or attachment.
Both mounting bases use the owner's double-sided tape during testing. PETG is
selected for rigid parts; the owner excludes material temperature testing as a
required gate. The initial TMP117 arrangement uses backside contact beneath the
tongue, thin MX-4 and a small replaceable air-side E30 pad, adjustable for fit.
Printed locating features with BT3-fastened board retainers and compact cable
clamps were subsequently accepted. BT3×5 is excluded from required hardware;
use lengths of 6 mm or longer. The owner confirmed printed retainers as primary
TMP117 retention, with polyimide tape optional during fitting and excluded from
the sensor-to-glass thermal interface. Board servicing leaves the taped base in place.
The TMP117 cover shields the insulated region from cabin airflow without an
airtight requirement; SHT45 uses generous ventilation. Both boards lift toward
the cabin after cables and retainers are released. Later modeling uses the CAD
skill with the agreed requirements and saved-artifact/visual checks; fit unknowns
remain explicit until measured.

## Exact board identity and nominal geometry

| Item | Verified manufacturer facts | Applicability limit |
| --- | --- | --- |
| Adafruit TMP117 **4821** | Assembled product envelope **25.5 × 17.7 × 4.6 mm**; current product revision note describes a silkscreen-only update. [Product](https://www.adafruit.com/product/4821) | Envelope height is not PCB thickness, sensor height, or mated cable height. Delivered revision and underside still need inspection. |
| Adafruit SHT45 PTFE **6174** | Product identifies **SHT45-AD1F-R2**, an integrated PTFE membrane, STEMMA QT connectors, and assembled envelope **25.5 × 17.7 × 4.8 mm**. [Product](https://www.adafruit.com/product/6174) | Membrane protection does not establish waterproof connectors or an IP67 complete breakout/printed enclosure. |

**Verified from pinned Eagle XML, independently re-read during this research:**
coordinates below use the board's lower-left nominal outline as origin, in mm.
Each layout has a 25.4 × 17.78 outline and four mounting-hole centres at
(2.54, 2.54), (22.86, 2.54), (2.54, 15.24), (22.86, 15.24): a
**20.32 × 12.7 mm pitch**. Hole drill is **2.5 mm**; copper pad diameter is
**3.2 mm**. TMP117 connector footprint origins are (2.667, 8.89) and
(22.733, 8.89), sensor centre (12.7, 8.89); its U-shaped isolation slot is
0.508 mm wide. [TMP117 Eagle source](https://github.com/adafruit/Adafruit-TMP117-PCB/blob/874b3a5b27792d4d166547bab075cb963e1bdb32/Adafruit_TMP117.brd)

The generic SHT45 layout places connector origins at (3.175, 8.89) and
(22.225, 8.89), sensor centre (12.7, 8.89), with a U-shaped isolation slot
of 0.8128 mm width. [SHT45 Eagle source](https://github.com/adafruit/Adafruit-SHT40-PCB/blob/40fe2b3aca1aaa3b2185d3d138e77d5b043a56e1/Adafruit%20SHT45.brd)

**Unknown:** the shared SHT PCB repository explicitly lists product **5665**,
not 6174. The shared guide lists both PTFE and other sensor products and provides
Eagle and generic SHT45 3D-model links; that association does not prove its STEP
represents the delivered PTFE assembly. No exact 6174 assembly model was verified.
The TMP117 downloads page provides Eagle/Fritzing resources; no exact assembled
TMP117 STEP was verified there.
[PCB repository](https://github.com/adafruit/Adafruit-SHT40-PCB),
[SHT downloads](https://learn.adafruit.com/adafruit-sht40-temperature-humidity-sensor/downloads),
[TMP117 downloads](https://learn.adafruit.com/adafruit-tmp117-high-accuracy-i2c-temperature-monitor/downloads).

**Inference:** these files support concept envelopes, not a final interference
check. A footprint origin is not a plug's outer boundary. Neither copper pad
size nor drill size specifies a safe washer/post bearing area. Reserve room for
mated plugs, unplugging fingers/tool access, cable bends and the actual component
map. Do not force a BT3/M3 screw through a nominal 2.5 mm board hole. Consider M2
board fastening separately from larger printed-part fasteners; measure the
actual head/washer envelope and electrical clearances first.

**Verified:** the TMP117 product page lists chip operation from −55 to +155 °C,
with accuracy varying by temperature range. The SHT45 6174 page lists sensor
operation from −40 to +125 °C. Neither establishes the full installed assembly's
temperature rating. [TMP117 product](https://www.adafruit.com/product/4821),
[SHT45 PTFE product](https://www.adafruit.com/product/6174).
The [owner's climate description](car-heater-defrost-strategy.md#exterior-frost-formation-on-a-parked-car)
includes cold spells below −40 °C; the earlier preparation decisions acknowledge
that those conditions exceed SHT45's supported range. **Unknown:** actual minimum
and maximum temperatures at each installation site, including summer parking.

Local provenance: [TMP117 layout facts](../../hardware/tmp117-windshield/src/lib/board-layout.json)
and [SHT45 layout facts](../../hardware/sht45-cabin/src/lib/board-layout.json).
Existing [TMP117](../../hardware/tmp117-windshield/README.md) and
[SHT45](../../hardware/sht45-cabin/README.md) drafts remain references, not
accepted mounting/retention choices.

## The thermal purposes require different concepts

### TMP117: couple to glass, limit influence from cabin air

**Verified:** TI's surface-measurement example attaches the opposite PCB face to
the target surface. Sections 3–4 discuss mechanical stress, sensor-to-object
resistance, sensor-to-air resistance and thermal mass; stronger thermal coupling
to the object and less coupling to air reduce installation error. PCB/contact
choices also affect lag. Section 10 identifies self-heating contributions from
operation and bus activity. These are system effects beyond chip accuracy.
[TI SNOA986A, §§3–4, 10](https://www.ti.com.cn/cn/lit/pdf/snoa986)

**Inference:** board-backside contact beneath the isolated tongue is a candidate
worth comparing against alternatives, not a demonstrated glass thermometer.
Keep printed plastic and fasteners out of that contact path; avoid bending the
tongue or applying load through the chip. A removable air-side insulation cover
can reduce air influence, but insulation footprint, contact force, thermal
interface thickness and glass curvature must be tested together. Do not inherit
the previous draft's foam compression percentage as a force specification.

**Verified:** ARCTIC describes MX-4 as electrically nonconductive thermal paste.
[MX-4 manufacturer page](https://www.arctic.de/en/MX-4/ACTCP00024A)

**Inference/Unknown:** use MX-4 only as a candidate interface, with mechanical
retention doing the holding. Its CPU application evidence does not verify
windshield compatibility, cleanup, bond strength, thin-layer behaviour, or
installed bias/lag. E30's owner-supplied thickness is not a spring constant:
compression force, permanent set, insulation performance and compatibility are
unmeasured. Compare insulation/contact variants outside the vehicle before
selecting one; no numerical safe PCB/tongue force is established.

### SHT45: exchange cabin air, avoid sampling the mount's temperature

**Verified:** Sensirion recommends small dead volume, large openings, thermal
separation from heat sources and appropriate sensor placement. Housing and PCB
integration can alter response and accuracy; condensation and temperature
mismatch matter for RH measurement.
[Sensirion design guide, §§2–3, 5](https://sensirion.com/media/documents/FC5BED84/662B494D/Sensirion_Humidity_Temperature_Design_Guide.pdf)

**Inference:** compare an open protective cage with a ventilated shell. Keep a
short unobstructed path from cabin air to the membrane and avoid a large enclosed
warm air pocket. Place the mount away from glass contact, heater discharge,
electronics and concentrated sunlight when assessing representative cabin air.
Avoid a metal fastening path from a hot/cold surface to the sensing island.
This is a thermal-design hypothesis, not a quantified metal-fastener error.

**Verified:** Sensirion's handling instructions warn that chemicals from
adhesives/plastics and cleaning agents can cause sensor drift; ventilation helps
limit buildup. They prohibit mechanical force on the sensor and require the
opening remain free of coating. Their listed adhesive compatibility is
product-specific, not a blanket approval of all products sharing a chemistry.
[Handling instructions, June 2025, §§1.3, 2, 3](https://sensirion.com/media/documents/6D95AA80/6840311F/HT_Handling_Instructions_SHTxx.pdf)

**Inference:** no foam, paste, superglue, print debris or cover should touch or
block the membrane. Cure any assembly adhesive away from the board and assess
its compatibility; generic polyimide tape identity does not prove suitable
adhesive chemistry near a humidity sensor. A protective enclosure should allow
inspection/drying rather than trap condensate. Keep contamination protection
and airflow as separate acceptance checks.

## Mechanical alternatives to discuss

The following comparison is **engineering inference**, not manufacturer-certified
retention, cold performance, or vehicle compatibility. The owner subsequently
selected available double-sided tape for windshield-base testing; its specific
adhesive and removal performance remain unverified. Availability of a sound
mechanical attachment feature at the intended location is still unknown.

| Vehicle attachment concept | Windshield implications | Cabin implications / decision gate |
| --- | --- | --- |
| Existing bracket/feature with padded mechanical clamp | A nearby rigid feature could support an arm ending at the glass; verify arm reach, flex, glass contact and cable loads. Do not assume trim is load-bearing. | Promising if a suitable feature exists; assess marks, squeeze load, vents and actual sensing position. |
| Purchased suction cup with mechanical connection | Candidate removable base, leaving screws to secure a serviceable sensor/cover. Cup displacement from the sensing patch avoids substituting cup temperature for glass contact. Seal, curvature, frost and long-term retention need trials. | Possible on a suitable smooth surface; adds bulk and may constrain sampling location. Printed suction geometry is not an established seal. |
| Locking or pump-operated suction base | A distinct alternative to passive cups; compare mechanism size, service needs, retention and sensor contact separately. | Depends on surface suitability and available space; a locking mechanism does not establish unattended year-round retention. |
| Removable adhesive/tape base with screwed service parts | Fallback if mechanical alternatives cannot maintain stable contact. Keeps daily service independent of glass adhesion, but relocation still requires removing the adhesive. | Fallback only after checking surface compatibility and removal behaviour. |
| Permanent glue to the vehicle or screws into vehicle trim | Conflicts with agreed removable, nondamaging attachment. | Exclude under current requirements. Permanent bonding within a removable assembly is a separate option. |

**Inference:** a screw joining the cover to the base does not solve base-to-glass
attachment. Discuss these interfaces separately. Possible board retention:
M2 fasteners at confirmed safe mounting lands; a removable frame capturing
robust PCB perimeter areas; or a guided sliding cassette with a separate
retaining fastener. A glass-contact sensor needs controlled contact without
clamping its tongue. A cabin board needs protection and minimal thermal coupling.
Hard stops can separate enclosure closing load from sensor contact load, but
stop geometry is not a verified force limiter until tolerances and stiffness
are known. Snap fits remain an option only after service-cycle and cold trials.

**Verified, owned suction cups:** Bambu's HA008 is a 25 mm transparent PVC cup.
Its specification describes decorative applications but provides no holding
load, retention duration or operating-temperature range.
[Manufacturer specification](https://cdn.shopify.com/s/files/1/0584/7236/6216/files/PVC_Suction_Cap_with_Mushroom_Head.pdf?v=1718935627).
Manufacturer STEP/STL geometry is available in the
[model ZIP](https://cdn.shopify.com/s/files/1/0584/7236/6216/files/PVC_Suction_Cap_with_Mushroom_Head.zip?v=1718934093).
**Unknown:** year-round retention on the actual curved windshield. A fit model
or a room-temperature trial does not establish that retention; the owner's
concern is unresolved, not evidence that the cups necessarily fail.

**Verified examples, not selected products:** RAM's twist-lock RAP-B-224-2U is
2.75 inches (about 70 mm) in diameter and intended for glass/nonporous plastic.
[RAM manufacturer page](https://rammount.com/products/rap-b-224-2u).
SeaSucker's 4.5-inch (about 114 mm) pump base has a vacuum-loss indicator; its
guidance says vacuum is lost over time and requires checking/re-pumping.
[SeaSucker manufacturer page](https://www.seasucker.com/collections/automotive-accessories/products/4-1-2-seasucker-black).
**Inference:** these demonstrate alternatives to passive suction but introduce
footprint or maintenance trade-offs. Neither source establishes unattended
year-round suitability for this sensor mount. Compare compactness, removal,
maintenance and glass-contact stability before selecting any attachment.

### Fasteners and cable loads

**Verified:** Bambu's BT3 drawing specifies a **5.5 mm head diameter**, **1.9 mm
head height**, and **H2** drive. It does not provide a validated printed-material
pilot diameter, boss size, tightening torque, service-cycle life or permission
to use M3 nuts.
[BT3 manufacturer drawing](https://store.bblcdn.eu/s8/default/e838d25b74fc4d77b138768fb1423a43/BT3_Socket_Head_Cap_Self_Tapping_Screw_SHCS.pdf)

**Inference:** BT3 is useful for printed-part joints after a pilot/boss coupon;
through bolts with captured nuts avoid relying on a repeatedly serviced plastic
thread, at the cost of nut access and a larger pocket. Provide screwdriver reach,
head clearance and positive limits preventing a long screw reaching glass or
PCB. Measure stocked hardware instead of assuming a generic washer/head size.
Do not interpret the owner's nut-free preference as a requirement to use BT3
for PCB mounting.

**Owner decision:** use 2.55 mm pilot holes for preferred self-tapping printed-part
joints. This specified dimension is separate from PCB holes and clearance holes;
it was supplied by the owner, not derived from the retrieved screw drawing.
Check the printed result and repeated assembly on a coupon using the A1/0.4 mm
baseline. Cover screws close against structural stops; do not use screw torque
to set tongue pressure.

**Verified:** JST describes SH as 1 mm pitch, friction-lock, available side/top
entry. Its generic series range is **−25 to +85 °C** and its listed conductor
range is **0.032–0.081 mm²**. These do not establish the identity/rating of every
compatible Adafruit connector cable.
[JST SH manufacturer specifications and CAD](https://www.jst-mfg.com/product/index.php?lang=2&series=231)

**Inference:** do not directly crimp the ordered 0.14 mm² extension into SH on
this evidence; the ordered pigtail splice approach remains the project context.
Anchor extension cable separately, then provide a gentle strain-relieved pigtail
with slack near the plug. Choose a removable clamp or tie saddle after measuring
insulation/bundle size. Keep cable loads out of the sensor tongue and thermal
contact. The installed cold-temperature requirement must cover connectors,
pigtails, extension insulation, foam and attachment, not just sensor chips.

### Shared bus and physical cable order

**Verified:** the TMP117 board defaults to address 0x48; closing its intended
address jumper selects 0x49. The SHT45 uses 0x44. Their STEMMA QT connectors
support linking boards on the same I²C lines.
[TMP117 pinouts](https://learn.adafruit.com/adafruit-tmp117-high-accuracy-i2c-temperature-monitor/pinouts),
[SHT45 pinouts](https://learn.adafruit.com/adafruit-sht40-temperature-humidity-sensor/pinouts).

**Inference:** physical daisy chaining is a reasonable candidate: the boards
share power, ground, SDA and SCL rather than relaying measurements through one
another. Two TMP117s require distinct addresses. Physical order can follow the
shortest practical route; the cabin board need not be first. Intermediate boards
need access and strain relief for both connectors, unlike the one-port old drafts.
Test the complete installed bus before accepting its length or reliability.
Address-jumper soldering is an intended configuration action allowed by the
owner; destructive PCB or connector modifications have been excluded.

### Golf V cabin placement candidate

**Owner:** the vehicle is a 2005 Volkswagen Golf V. **Verified:** Volkswagen's
archive identifies Golf V as the 2003–2008 generation with multiple equipment
lines; this does not specify the owner's trim geometry.
[Volkswagen archive](https://www.volkswagen-newsroom.com/en/golf-5-20032008-19480).

**Inference:** start with the driver-facing upper side of the centre console,
below the centre stack near the forward gear-lever area. Keep the sensor exposed
to cabin air and separated from trim, with clearance from knees and controls.
This candidate avoids the documented passenger-side heater location and can
share a route toward the lower windshield sensors. Exact mounting geometry,
local sun exposure, HVAC/electronics heat and airflow remain unverified.

A possible physical order is ESP32 → SHT45 → reference TMP117 → lower-band
TMP117; cable-route measurements may favour another order. Do not sacrifice the
sensing location just to shorten wiring. Both connectors must be accessible on
intermediate boards. Accept the cabin position only after comparing heater-off
and heater-on readings with a sensor temporarily exposed farther into cabin air,
and checking actual mechanical clearance and ventilation.

## Printing and material implications

**Verified:** Prusa's modeling guidance recommends considering orientation,
directional strength, chamfers, tolerances and splitting parts to reduce supports.
Test prints are required to establish fit. Its example angles/clearances are
not a qualification of the owner's A1 or any particular nozzle/profile.
[Manufacturer modeling guide](https://help.prusa3d.com/article/modeling-with-3d-printing-in-mind_164135)

**Inference:** compare concepts with their intended print orientation shown.
Prefer flat bases/lids, accessible vertical fastener holes, open nut pockets,
self-supporting vent roofs and chamfers rather than unsupported undersides.
Align clamp/flexure loads with the stronger print directions and avoid making
thin upright tabs carry bending by separating layers. The owner has accepted
multiple pieces when they improve printability or service.
Slice the selected concept with supports disabled first and inspect every layer.

**Verified:** Prusa warns PLA has limited thermal resistance and describes PETG
as tougher and more temperature-resistant. These generic material descriptions
are not ratings of the owner's unspecified spools or loaded printed mounts.
[PLA](https://help.prusa3d.com/article/pla_2062),
[PETG](https://help.prusa3d.com/article/petg_2059).

**Verified:** Bambu TPU for AMS is **68D**, with reported X–Y/Z mechanical
properties differing. Its TDS lists heat-deflection temperature as unavailable;
it gives no installed minimum-temperature rating or long-term preload/creep
qualification. Its specimen preparation differs from an arbitrary household
print, and Bambu describes results as comparative references.
[TPU for AMS TDS v1.0](https://cdn.shopify.com/s/files/1/0584/7236/6216/files/Bambu_TPU_for_AMS_Technical_Data_Sheet.pdf?v=1730944629)

**Inference:** PLA can support geometry mockups; PETG is a candidate for rigid
parts, subject to actual cabin heat/cold and loaded tests. Do not assume TPU for
AMS is a soft gasket, reliable suction cup or qualified cold spring. Keep sustained
preload modest; no numerical creep limit is established for any available spool.
A winter-only mount and a permanently summer-installed mount have different
exposure. **Owner scope decision:** use PETG without a required material
temperature test. Earlier material-validation recommendations are not mandatory
gates for this design; retain fit and fastening checks.

## Measurements and acceptance gates before detailed CAD

These are **proposed checks**, with pass criteria still to agree through grilling.

| Gate | Evidence needed |
| --- | --- |
| Delivered boards | Revision/photos; outline and hole pitch/drill; thickness; underside protrusions/flatness; actual safe bearing areas; sensor/membrane and all component heights. |
| Connectors and service | Fully mated plug envelope, cable exit and unforced bend; connector access; tool sweep; one or two ports in use; unplugging without moving the base. |
| Vehicle site | Location/photos and dimensions of attachment feature; glass curvature; contact footprint; cable route; surfaces that may be touched; installation lifetime and temperature exposure. |
| Printer and hardware | A1/0.4 mm baseline confirmed; actual filament identity and profile; fastener lengths/head/washer/nut dimensions; usable tool access. |
| Small coupons | Hole/pilot ladder and printed boss with stocked BT3; head recess and nut-pocket fit; fastening/service cycles; clip or clamp flexure if proposed; actual PCB/plug fit coupon. |
| TMP117 bench assembly | Thin interface repeatability; contact without visible PCB/tongue bending; cable tug without contact loss; cover removal/reinstallation; paired comparison of insulation/contact variants during thermal changes. |
| SHT45 bench assembly | Membrane clear and inaccessible to knocks; air path visible; response comparison with/without housing; no board contact with contamination sources; condensate escape/inspection. |
| Commissioning later | Installation retention, vehicle removal without marks, bus reliability and measured thermal bias/lag. Material cold/heat testing is excluded by the owner. Two identical sensors agreeing is not independent proof of glass-temperature accuracy. |

Before constructing mounts, agree what cabin-air location means and what glass
point each TMP117 represents. Photographs and site measurements can settle
attachment options; sensor arrival is essential for fit, not for concept discussion.

## Research limitations and next discussion

No validated BT3 printed pilot/torque was found. No exact 6174 assembly CAD,
washer-safe board contact footprint, foam load curve, installed thermal accuracy,
attachment force or full cold-qualified assembly was established. EU Bambu
material-store retrieval failed; the manufacturer-hosted screw drawing and TPU
TDS were accessible. The shared SHT45 3D-model link failed through the research
browser; the Eagle file was retrieved directly. The Sensirion SHT45 catalogue
and one indexed newer datasheet URL failed retrieval, so this note does not infer
extra sensor-package details from those unavailable documents.

This evidence fed `/grill-with-docs`; the owner subsequently confirmed the
purpose-specific concepts and published spec #19. The approved delivery graph
is [TMP117 draft #20](https://github.com/jannesii/raspberry-pi-smart-home/issues/20)
and [SHT45 draft #21](https://github.com/jannesii/raspberry-pi-smart-home/issues/21),
then [owner measurement/trial fitting #22](https://github.com/jannesii/raspberry-pi-smart-home/issues/22),
then [measured-model finalization #23](https://github.com/jannesii/raspberry-pi-smart-home/issues/23).
Actual fit interfaces remain measurement questions. No replacement CAD was
created by this research or ticket publication.
