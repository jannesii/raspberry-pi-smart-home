# Car Heater Defrost Strategy

Research and design study, 2026-09-29, at revision `b95b30a`. No production code
was changed. It builds on the
[car heater physics and control audit](car-heater-physics-and-control-audit.md).
That audit asked whether the Ready-by implementation is correct. This study asks
a different question: is heating the cabin air to a target temperature the right
way to have clear windows at departure?

Updated on the same day with the owner's answers to the open questions. They
are summarised in [Open Questions](#open-questions) and applied throughout;
facts from them are labelled **[Owner]**.

> **Bottom line.** For removing exterior frost, cabin air temperature is neither
> necessary nor sufficient. With the cabin held at +10 °C and −10 °C outside, the
> glass/frost interface ends up anywhere from about 0 °C to −6 °C depending on
> sky and wind. Neither the cabin sensor nor the power meter can see that
> difference.
>
> The most promising direction has three parts:
>
> - **Plan** heating as a weather-keyed duration, as every Nordic pre-heating
>   product examined does.
> - **Confirm and hold** readiness on a measured windshield inner-surface
>   temperature.
> - **Learn** start times from that measurement, which labels every session
>   automatically.
>
> The one-node cabin model stays useful for diagnostics and comfort, but should
> stop defining "ready". No real outcome data exists yet. The first step is a
> small instrumented data collection with explicit decision rules; see
> [Data Collection Plan](#data-collection-plan).

Evidence labels used throughout:

| Label | Meaning |
| --- | --- |
| **[Lit]** | Stated by a cited standard, paper, manufacturer document or official data service |
| **[Repo]** | Verified in this repository, or its local firmware checkout, at `b95b30a` |
| **[Model]** | Result of this study's synthetic model; not a measurement of this car |
| **[Derived]** | This study's own calculation from cited physics, with inputs stated |
| **[Hyp]** | Hypothesis about this car or installation; unverified |
| **[Owner]** | Stated by the car's owner on 2026-09-29; not independently verified |

Source numbers `[n]` refer to [Sources](#sources). Some physics values and
derivations come from research notes compiled for this study. Where the owning
source was not traced, they are marked as typical values.

## Actual Goal

The goal: **when the driver reaches the parked car at the requested departure
time, the windows are clear enough of exterior frost and ice to drive without
scraping, and heating started as late as reasonably possible.** Cabin comfort is
secondary. A cool cabin with clear windows is a success; a warm cabin with a
frozen windshield is a failure.

### Four different physical problems

| Problem | What it is | When it forms | What cabin heating does | Priority |
| --- | --- | --- | --- | --- |
| Exterior frost (hoarfrost) | Vapour deposited as ice crystals on the outer glass | Glass below the air's frost point: typically clear, calm nights (radiative cooling) | Must bring the glass/frost interface to 0 °C and melt its base | **Primary** |
| Exterior ice | Frozen dew, glaze (freezing rain or drizzle), rime (supercooled fog), refrozen slush or meltwater | Precipitation or fog near 0 °C; melt/refreeze cycles | As for frost, but dense, strongly adhered, and 3–10× more latent heat per m² | **Primary** |
| Interior frost | Ice on the inner surface from cabin moisture (breath, snow, wet mats) | Inner glass below the cabin air's frost point overnight | Sits on the heated side and clears first, but its meltwater raises cabin humidity | Secondary |
| Interior fog | Liquid condensate on the inner surface | Inner glass below the cabin air's dew point | Can *appear* during heating: the inner glass stays near +1…+2 °C while exterior frost melts, while evaporating moisture raises the dew point | Secondary; must not be made worse |

Snow cover has to be brushed off whatever the heating strategy, so it is out of
scope. It does change the glass heat balance (see
[Physics](#physics-of-windshield-frost-and-defrost)).

### What "ready" should mean

The regulations that define defrosting treat an area as defrosted when its
outside surface is **dry, or covered with melted or partially melted wet frost
that the wipers can remove**. Dry frost does not count [Lit: 2, 4, 5, 7]. Wipers
may be used without manual help [Lit: 1, 4]. Success is scored as the **percentage
of the driver's vision areas cleared over time**, not as a temperature. The EU
test, for example, requires 80 % of zone A at 20 min and 95 % of zone B at
40 min [Lit: 4].

| Candidate definition | Physically meaningful? | Measurable automatically here? | Assessment |
| --- | --- | --- | --- |
| Entire windshield above freezing | Stricter than needed; the lower band near the cowl clears last | No; one point sensor cannot cover the glass | Impractical and wasteful |
| Driver's primary viewing area clear, wipers allowed | Yes; matches the regulatory notion | Only by the user or a camera. A glass sensor inside that area is a proxy | **Validation definition** |
| No visible exterior frost anywhere | Yes, but stricter than needed at the edges | User or camera | Useful label, not a control input |
| Predicted sufficient melt: glass at a reference point held above a threshold for a dwell time | Yes, once threshold and dwell are validated | **Yes, with a glass temperature sensor** | **Control definition** (needs validation) |
| Cabin air above a target | Only indirectly | Yes | Not a readiness definition (next section) |

**Proposed definition:**

- **Control on:** glass inner-surface temperature at a reference point in the
  driver's area is at least θ for at least τ, and is still held at departure.
- **Validate against:** the user reports that the driver's area was clear, with
  wipers allowed.

"Still held" matters. The glass time constant is about 5–10 min [Derived]. If
heating stops early, the wet film can refreeze as clear ice before the driver
arrives [Hyp: physically expected, not observed here].

## Why Cabin Temperature May or May Not Be the Right Proxy

The current abstraction [Repo: `app/services/car_heater/ready_by_service.py:202–305`,
`app/services/car_heater/keep_at_temp_service.py:90–117`] is:

`predict time for cabin air to reach target → start at deadline − ETA → on reaching
target, hand over to an untimed thermostat at the same target`

The Ready-by UI defaults the target to **−5 °C** [Repo:
`app/templates/car_heater.html:150–155`, since commit `201c98c`]. That suggests
the feature was never really a comfort feature, although the intent is not
recorded.

### What decides whether exterior frost melts

In quasi-steady state, the outer glass (interface) temperature is a weighted
average of three temperatures [Derived]:

`T_outer ≈ (U_in·T_in + h_c,o·T_air + h_r·T_sky) / (U_in + h_c,o + h_r)`

- `T_in` is the cabin air and interior surfaces the glass sees. `U_in` is the
  coupling to them: about 5–8 W/m²K with natural convection and interior
  radiation, and 20 W/m²K or more when the heater blows on the glass.
- `h_c,o` is exterior convection, which depends on wind.
- `h_r` ≈ 3–4.6 W/m²K is radiation to a sky that, on clear nights, is 18–30 K
  colder than the air [Lit: 8, 9; Derived].

Cabin air is only one of the three temperatures. With natural-convection
coupling its weight is about 0.2–0.45 [Derived]. A cabin 10 K warmer therefore
moves the outer glass by only about 2–4 K, while sky and wind move it by
5–15 K from one morning to the next [Derived].

### Evidence

1. **Same cabin temperature, different outcome.** Holding the cabin air at
   +10 °C with −10 °C outside air, the steady interface temperature spreads over
   4–7 K between calm/breezy and overcast/clear mornings [Model, E2]:

   | Heater airflow at the glass | Calm, overcast | Calm, clear | Breezy, overcast | Breezy, clear | Power to hold +10 °C |
   | --- | --- | --- | --- | --- | --- |
   | Natural convection | −3.0 °C | −7.3 °C | −5.4 °C | −8.1 °C | 887–928 W |
   | Fan-mixed cabin | −0.4 °C | −3.8 °C | −3.3 °C | −5.6 °C | 920–988 W |
   | Aimed at the glass | +3.0 °C | +0.7 °C | 0.0 °C | −1.7 °C | 962–1079 W |

   The power needed to hold the cabin temperature changes by only 5–12 %,
   because the windshield is a small part of the cabin's total heat loss.
   **Neither the cabin sensor nor the Shelly power reading can tell these
   mornings apart.**

2. **Cabin air needed for a 0 °C interface.** In steady state this ranges from
   about +3 °C (−5 °C, calm, overcast, heater aimed at the glass) to above
   +50 °C (−10 °C, windy, clear, natural convection) [Model, E1]. An independent
   hand calculation found the same pattern [Derived]. At −10 °C with
   h_i = 8 W/m²K, it needs +10 °C when calm and overcast but +18 °C when calm
   and clear; at −20 °C, +21 °C and +29 °C. Many of these exceed what a
   1–1.5 kW heater can sustain.

3. **At the moment of clearing.** Across the transient scenario grid, cabin air
   ranged from about 6.5 °C to 28 °C. The inner glass surface ranged only from
   +0.4 °C to +2.6 °C [Model, E3].

4. **Manufacturer chamber data** [Lit: 18; readings of a chart and a small
   photo]. DEFA's handbook reports a −20 °C chamber test with a "windscreen"
   measurement point; it does not say whether that point measures glass or the
   air beside it.

   | Car and heater configuration | Windscreen point crossed 0 °C after | Other readings |
   | --- | --- | --- |
   | Large SUV, one 2.1 kW heater | ≈20–22 min | — |
   | Mid-size car, labelled "Termina 1400 + Termini 1350" | ≈70–75 min | Side-window point already ≈ +11 °C at that time |

   After 2 hours, a band along the lower edge of the windscreen still appeared
   frosted.

### When cabin temperature correlates well, and when it does not

A fixed cabin target can track clearing reasonably well when [Model/Derived]:

- the heater's airflow is aimed at the windshield, so the glass is tightly
  coupled to the cabin air;
- mornings are mildly cold (about −2 to −8 °C), and calm or overcast;
- the frost is light hoarfrost, so the latent-heat dwell is short;
- the cabin sensor and the heater never move.

It correlates poorly when:

- only natural convection reaches the glass;
- the sky is clear. Net long-wave loss continues during defrost, about
  70–110 W/m² isothermal at clear skies [Derived from 8];
- it is windy. Exterior convection rises to 2–6× its calm value;
- it is very cold. A 0 °C interface then needs cabin temperatures close to the
  most the heater can sustain;
- the deposit is thick or adhered ice, so the latent-heat dwell runs to tens of
  minutes [Model, E3];
- the cabin sensor sits near the heater outlet or high in the warm upper cabin;
- the heater has PTC elements, which deliver less power as the cabin warms.
  DEFA gives about 20 % less power per 20 K rise [Lit: 18].

### Is targeting a cabin temperature fundamentally unnecessary?

**For the defrost goal, yes.** Cabin air temperature is:

- **Not necessary.** With airflow aimed at the glass, clearing happened at
  6.5–15.7 °C cabin air [Model].
- **Not sufficient.** At −10 °C under a clear sky, holding +15 °C cabin air left
  the interface at −1…−7 °C [Model, E2 at +15 °C].

It remains a useful *input*: evidence that the heater works, the driving
temperature for any glass model, a comfort quantity and a safety limit.

### Does the one-node model still have predictive value?

For **cabin air**, yes. It supports a comfort ETA, heater-fault detection (no
temperature rise while drawing power), and a feature for a defrost-time model.
For defrost it has three limits that remain even with every audit defect fixed:

1. It has no glass, sky or frost state. Wind enters only through parameter
   buckets.
2. Its parameters are effective ratios, not identifiable physical quantities
   [audit F10; Derived].
3. **It is least reliable exactly where defrost needs it.** In cold weather the
   cabin temperature that clears the glass lies close to the heater's ceiling.
   In the model, at −10 °C with fan-mixed airflow, it is 64 % of the reachable
   rise on a calm, overcast morning, 84–94 % on clear or breezy ones, and out
   of reach on a breezy, clear one. A first-order ETA is ill-conditioned near
   the ceiling. Using the audit's default τ = 42 min [Derived]:

   | Target as fraction of reachable rise | True ETA | Predicted if rise is over-estimated by 10 % | Predicted if rise is under-estimated by 10 % |
   | --- | ---: | --- | --- |
   | 0.50 | 29 min | 25 min (4 min late) | 34 min (5 min early) |
   | 0.70 | 51 min | 42 min (8 min late) | 63 min (13 min early) |
   | 0.80 | 68 min | 55 min (13 min late) | 92 min (25 min early) |
   | 0.90 | 97 min | 72 min (25 min late) | "unreachable", so it starts immediately |
   | 0.95 | 126 min | 84 min (42 min late) | "unreachable", so it starts immediately |

   A 10 % error in the asymptote is small by the standard of this system: the
   audit's parameter-recovery experiments produced much larger errors.

These are limits of the concept, separate from the audit's software defects
(F01–F14).

## Physics of Windshield Frost and Defrost

### Exterior frost formation on a parked car

- **Radiative cooling.** Downwelling long-wave radiation is `ε_sky·σ·T_air⁴`
  [Lit: 8]. EnergyPlus's default clear-sky emissivity (Clark–Allen, with
  Walton's cloud correction) gives an effective clear-sky temperature of about
  −28 °C at −10 °C air. The range across the models EnergyPlus lists is
  −28…−38 °C. At −20 °C air it gives about −40 °C [Lit: 8; Derived at 95 % RH
  over ice].
- **The glass falls below air temperature.** A windshield tilted about 30° sees
  mostly sky: an effective sky fraction of 0.87–0.93, against about 0.35 for a
  vertical side window, using EnergyPlus's view-factor formulation [Lit: 9;
  Derived]. On a clear night the windshield settles about 5–6 K below air
  temperature when calm, 3–4 K at 2 m/s local wind and 2–3 K at 5 m/s
  [Derived]. This is why the windshield and roof frost first and side windows
  often stay clear. It is consistent with Volvo's description of radiation frost
  [Lit: 12, abstract].
- **Deposition.** Frost forms when the surface is below the frost point, that
  is when `e_air > e_ice,sat(T_surface)`, computed with the WMO Magnus formulas
  over ice [Lit: 10]. In humid winter air the frost point is only 0–3 K below
  air temperature. At −10 °C and 85 % RH (with respect to water) it is
  −10.7 °C [Derived from 10]. Clear, calm nights therefore almost always frost
  the windshield.
- **Amount.** Hoarfrost growth is limited by vapour transport. It is roughly
  0.1–0.16 kg/m² after a 10-hour clear night at −10 °C, and 0.03–0.05 kg/m² at
  −25 °C, because colder air holds less vapour [Derived; upper-bound style].
  The regulatory test ice is 0.044–0.046 g/cm², i.e. 0.44–0.46 kg/m² or about
  0.5 mm of solid ice [Lit: 2, 3, 4].
- **Surface emissivity matters.** A low-emissivity outer coating markedly
  reduces radiation frost. Volvo reported more than 80 % fewer mornings needing
  scraping in a coastal Swedish climate [Lit: 11, 12, abstracts]. This car's
  windshield is ordinary laminated safety glass, most likely green-tinted
  solar-control glass, neither heated nor acoustic [Owner]. A body tint absorbs
  solar near-infrared and does not imply a low-emissivity surface [Hyp], so no
  frost reduction from the glass should be expected.
- **This parking spot.** The car stands in the open in Seinäjoki town [Owner]:
  - The only obstruction is a tall building about 30 m to the west. It hides
    part of the western sky, and its facade radiates more warmly than the sky
    would. It also shelters the car from westerly winds, so wind at the car is
    probably at the low end of the 0.3–0.65 scaling [Hyp].
  - The reference station, Seinäjoki Pelmaa (fmisid 101486), is about 23 km to
    the north-west, in an open river valley. The airport station (137188) is
    about 12.7 km to the south [Derived from station coordinates].
  - On calm, clear nights a field station in a valley can run colder than a
    sheltered town lot [Hyp]. HARMONIE forecasts can be requested for the car's
    own coordinates.
- **Local climate** [Owner]:
  - The windows are frosted almost every winter morning.
  - Mid-winter mornings are typically −8…−14 °C, with cold spells down to
    −35…−45 °C.
  - That typical range is the model's marginal zone: at −10 °C a 1.5 kW heater
    cleared the frost within 4 h only if its airflow reached the glass, and at
    −15 °C only when aimed at it on a calm morning. At −20 °C and below, no
    modelled configuration cleared within 4 h [Model, E3]. For this car,
    heater placement probably decides whether typical mornings clear at all,
    and cold spells need an honest "cannot clear" answer [Hyp].
- **Current heater placement** [Owner]. The heater stands in the passenger
  footwell against the centre-tunnel wall, blowing about 45° upward toward the
  passenger headrest. The windshield therefore gets only mixed cabin air, the
  model's natural-to-fan-mixed cases. At −10 °C those cleared heavy hoarfrost
  in about 1.6–3.5 h on calm, overcast mornings. On calm, clear mornings only
  the fan-mixed case cleared (3.7 h), and when breezy and clear neither cleared
  within 4 h. At −15 °C neither cleared within 4 h [Model, E3, 1500 W]. The driver's
  side, farthest from the heater, probably clears last [Hyp].

### Interior frost and fog are separate problems

Interior fog forms when the inner glass is below the cabin air's dew point, and
interior frost when it is below the frost point [Lit: 4 definition of mist;
10]. A recirculating electric heater does not dehumidify. During exterior melt
the inner glass sits near +1…+2 °C, which corresponds to a cabin dew point of
about +1…+2 °C, roughly 5 g/m³ of water vapour or about 30 % RH at +20 °C. In a
cabin of about 3 m³, evaporating 20 g of meltwater or snow from the mats lifts
the dew point to about +9 °C [Derived]. A few tens of grams of water can
therefore fog the windshield from inside for as long as the glass stays cold.
Cabin temperature cannot reveal this. Production anti-fog modules measure
humidity and temperature at the windshield to compute the dew point there
[Lit: 24–26].

### Heat path when defrosting from inside

| Layer | Typical resistance (m²K/W) | Notes |
| --- | --- | --- |
| Interior film, natural convection + radiation | 0.125–0.2 (h ≈ 5–8 W/m²K) | The radiative part is weak early, because interior surfaces lag the air |
| Interior film, jet aimed at the glass | 0.01–0.05 (h ≈ 20–100 W/m²K) | Order of magnitude only; not measured here |
| Laminated glass, 2.1 mm + 0.76 mm PVB + 2.1 mm | ≈ 0.008 | Glass ≈ 1 W/mK, PVB ≈ 0.2 W/mK (typical values) |
| Hoarfrost, 1 mm | 0.005–0.02 | Porous; insulates little |
| Test ice, 0.5 mm | ≈ 0.0002 | |
| Snow, 5 cm | 0.4–1.0 | Strong insulation; must be brushed off |
| Exterior film, wind convection + radiation | 0.04–0.17 (h ≈ 6–28 W/m²K) | Plus the offset from the colder sky temperature |

[Derived. Property values are typical handbook values compiled in this study's
physics research note; not all were traced to their owning sources.]

Consequences [Derived]:

- **The glass is thermally thin.** Its resistance is about 6 % of the interior
  film's. The outer temperature is therefore set by how strongly the glass is
  coupled inside versus outside. This is consistent with Du et al.'s finding
  that windshield thickness and conductivity matter little [Lit: 13, abstract].
- **The inner surface reads warmer than the interface** by `q·R_glass`: about
  0.5–4 K for 60–500 W/m² of heat flowing through the glass.
- **The glass lags the cabin by minutes.** Its heat capacity is about
  8.5–10 kJ/m²K and its time constant about 5–10 min.

### What must happen for the window to clear

1. **The interface must reach 0 °C.** Below that there is only slow
   sublimation, which costs about 8.5× the heat of melting [Derived].
2. **The base must melt.** Wet, partly melted frost counts as defrosted if the
   wipers remove it [Lit: 2, 4]. Adhered ice needs a melt film at the
   interface before wipers help: "wipers may slide-over and offer little help
   in removing ice" [Lit: 14].
3. **Latent heat is secondary for hoarfrost, but not for ice.** Hoarfrost of
   0.05–0.2 kg/m² needs 17–67 kJ/m², and the test ice about 150 kJ/m². Warming
   the glass itself from −20 °C to 0 °C needs 170–210 kJ/m² [Derived]. For
   hoarfrost the hard part is **reaching and holding 0 °C against wind and sky
   losses**. For ice, the melting dwell also takes time.
4. **Heating must continue.** If it stops, the glass falls back below 0 °C
   within its 5–10 min time constant, and the melt can refreeze
   [Derived/Hyp].
5. **Evaporation competes** in wind and dry air. In a CFD study at −4 °C with
   defroster jets, one high-wind, low-humidity case lost about 50 % of the heat
   to evaporation and showed the start of refreezing [Lit: 14].

**Benchmarks with production defrosters.** These blow hot air at the glass with
the engine running.

- NHTSA FMVSS 103 compliance test at about −18 °C: nothing had cleared at 5 min,
  and 75–89 % of the vision areas were clear at 20 min. Outlet air was only
  ≈ 11 °C at 5 min and 24–27 °C at 20 min [Lit: 3].
- The CFD study above: first breakthrough at ≈ 10 min and 83 % clear at 40 min,
  at −4 °C with 10 m/s wind [Lit: 14].
- Production defroster airflow is strongest at the lower windshield [Lit: 16].
  Even so, the first area to clear sits below the driver's eye level, "some
  considerable time" after the blower starts [Lit: 15, abstract]. A
  floor-standing cabin heater has no such duct.

[Hyp] A parked car heated by a floor-standing 1–2 kW heater will clear more
slowly than these benchmarks, with the lower band last, as DEFA's photo
suggests.

### Relevant variables, and which are worth measuring

| Variable | Physical role | Known today | Worth measuring? |
| --- | --- | --- | --- |
| Outer glass (interface) temperature | Decides melting | No | Not directly (outside, under frost); use the inner surface as a proxy |
| Inner glass surface temperature | Proxy: outer + 0.5–4 K | No | **Yes: the single most informative addition** |
| Cabin air temperature | Drives heat supply to the glass | Yes (BMP280) | Keep as input, safety limit and comfort |
| Heater airflow reaching the glass | Largest single lever on interior coupling | No | Not with a sensor. Fix it by heater placement; learned implicitly |
| Outside air temperature | Exterior heat sink | Yes (FMI station) | Keep. A sensor at the car is optional |
| Sky radiation / cloud cover | Drives frost formation and defrost loss | Partly: missing at station 101486, available at the airport station and in forecasts | Yes, from existing FMI data |
| Wind at the car | Exterior convection | Station wind at 10 m. At car height roughly 0.3–0.65× that (log law) [Derived] | Yes, from FMI with a scale factor |
| Outside humidity / frost point | Frost formation and amount | FMI `rh`, `td` | Yes, as a frost-likelihood feature |
| Cabin humidity | Interior fog and frost risk | No | Nice to have |
| Initial soak (glass and cabin start temperatures) | Sensible heat to supply | Cabin only | A glass sensor provides the glass start temperature |
| Heater power / delivered energy | Heat input; PTC power falls as the cabin warms | Yes (`apower`, `aenergy`) | Keep, to verify heating and account energy |
| Frost/ice type and mass | Latent heat and adhesion | No | User label, weather-based estimate, or a camera (optional) |
| Snow cover | Must be brushed; insulates | No | User label only |
| Heating duration | The control variable | Yes | — |

### Synthetic experiments

**Model** [Model]. The model has four lumped states: cabin air plus light
surfaces (`C_a`), interior mass (`C_m`), windshield glass (`C_g`, area 1.3 m²),
and exterior frost mass per m². Heat flows along these paths:

- heater → air. All electrical power becomes heat inside the cabin.
- air ↔ inner glass by convection `h_i`, and interior mass ↔ inner glass by
  radiation `h_ri = 3.5 W/m²K`.
- glass → ½·R_glass → interface → frost `R_f` → outside air and sky:
  - outside air by convection `h_c,o = 4 + 4v`, an ISO 6946-style form, not
    checked against the standard text here;
  - sky by linearised radiation. The sky sits `ΔT_sky` below air temperature:
    3 K overcast, 12 K partly cloudy, 25 K clear.
- air → outside through the rest of the shell, `UA_rest = 35 W/K`;
  interior mass → outside, `UA_m = 5 W/K`; air ↔ mass, `H_am`.

When the interface would exceed 0 °C it is clamped there, and the net flux at
the interface melts frost (`L_f = 333.55 kJ/kg`). Transient runs start from a
12-hour unheated equilibrium under the same sky, which leaves cabin and glass
slightly below air temperature.

| Parameter | Values used |
| --- | --- |
| Heater power | 1000 W, 1500 W |
| Interior coupling `h_i` | 3.5 (natural), 8 (fan-mixed), 20 (aimed at the glass) W/m²K |
| Cabin dynamics | Fast: `C_a` 15 kJ/K, `H_am` 50 W/K. Slow: 40 kJ/K, 100 W/K. `C_m` 150 kJ/K in both |
| Glass | `C_g` 13 kJ/K, `R_glass` 0.008 m²K/W |
| Frost | Light hoarfrost 0.03 kg/m² (100 kg/m³, k 0.05); heavy hoarfrost 0.15 kg/m² (200, 0.15); glaze 0.44 kg/m² (917, 2.2) |
| Wind at the car | 0.5, 2 or 3 ("breezy"), 5 m/s |
| Outside air | −2 to −25 °C |

At 1 kW the steady cabin rise is about 22 K; the audit's default model gives
15 K. The owner's heater is rated 1.7 kW, and PTC heaters deliver less as the
cabin warms [Owner; Lit: 18], so the 1500 W runs are the closer match. The model is meant to show **structure and ranges**, not to predict this
car. Results are most sensitive to `h_i` and to how the cabin splits into fast
and slow parts. The steady-state results (E1, E2) do not depend on heat
capacities at all.

**E1 — Steady cabin air needed for a 0 °C interface under heavy hoarfrost**
(excerpt from 36 cases):

| Outside | Wind at car | Sky | Natural convection | Aimed at glass | Cabin reachable at 1 kW (natural / aimed) |
| --- | --- | --- | ---: | ---: | --- |
| −5 °C | 0.5 m/s | overcast | +10 °C | +3 °C | +17.5 / +15.7 °C |
| −5 °C | 0.5 m/s | clear | +22 °C | +7 °C | +16.7 / +14.5 °C |
| −10 °C | 0.5 m/s | overcast | +18 °C | +6 °C | +12.6 / +10.8 °C |
| −10 °C | 0.5 m/s | clear | +28 °C | +9 °C | +11.8 / +9.6 °C |
| −10 °C | 5 m/s | clear | +52 °C | +16 °C | +11.5 / +8.0 °C |
| −20 °C | 0.5 m/s | overcast | +32 °C | +10 °C | +2.6 / +0.9 °C |
| −20 °C | 5 m/s | clear | +90 °C | +28 °C | +1.6 / −1.9 °C |

At these points the inner glass surface sat between +0.5 °C and +4.3 °C,
higher where more heat flows through the glass.

**E2 — Cabin held by a thermostat.** Table in [Evidence](#evidence).

- At −10 °C and +10 °C cabin air, the interface temperature spreads over 4–7 K.
- At −20 °C with the cabin held at +15 °C, only calm mornings with airflow aimed
  at the glass lift the interface above 0 °C (+1.5…+3.3 °C). Every other
  combination stays between −2 °C and −14 °C.

**E3 — Transient heating, 1500 W, heavy hoarfrost, fast cabin.** Minutes until
the frost is fully melted ("—" = not within 240 min):

| Outside | Condition | Natural | Fan-mixed | Aimed at glass | Cabin reaches +10 °C after |
| --- | --- | ---: | ---: | ---: | --- |
| −5 °C | calm, overcast | 71 | 36 | 17 | 6–9 min |
| −5 °C | calm, clear | 209 | 85 | 32 | 7–14 min |
| −5 °C | breezy, clear | — | 132 | 43 | 7–13 min |
| −10 °C | calm, overcast | 208 | 97 | 44 | 25–39 min |
| −10 °C | calm, clear | — | 220 | 80 | 30–47 min |
| −10 °C | breezy, clear | — | — | 162 | 28–50 min |
| −15 °C | calm, overcast | — | — | 108 | 65–89 min |
| −20 °C | any | — | — | — | 138 min to never |

Further results from the grid:

- **At the moment of clearing**, cabin air was 10.5–15.7 °C with aimed airflow,
  15.6–22.0 °C fan-mixed and 20.5–26.2 °C with natural convection. The inner
  glass was +0.8…+2.4 °C in every case.
- **The slow-cabin variant** reached +10 °C cabin air 20–30 min later, but
  changed clearing times by only −12 to +18 min. In two aimed-airflow cases the
  glass cleared while the cabin never reached +10 °C.
- **In the fast-cabin grid** (648 runs, targets −5 to +20 °C), the glass was
  never already clear when the cabin first reached its target.
- **Latent-heat dwell** from melt onset to full clearing had a median of 10 min
  for light hoarfrost, 28 min for heavy hoarfrost and 49 min for glaze, with
  long tails when the surplus heat flux was small.

**E4 — ETA conditioning.** Table in
[Does the one-node model still have predictive value?](#does-the-one-node-model-still-have-predictive-value).

**What the experiments show, and what they do not.** They show that plausible,
literature-range parameters produce large, condition-dependent gaps between
cabin air and glass state, while the inner-glass temperature at clearing stays
in a narrow band. They also show that the heater's airflow at the glass is the
largest single lever: aiming it at the glass cleared frost 2–5× faster in the
model and extended the range of temperatures where clearing is possible at all.
They do **not** establish this car's times, which depend on the heater's power
and placement and on the cabin. Only field data can.

<details>
<summary>Reproducing E1/E2 (steady state) and E3 (transient)</summary>

The steady state is linear in heater power `P`. Unknowns are cabin air `Ta`,
interior mass `Tm`, glass mid-plane `Tg` and inner surface `Tgi`:

```python
SIGMA = 5.670374419e-8

def steady(P, To, v, dTsky, h_i, R_f=0.005, eps=0.95, A=1.3, R_g=0.008,
           h_ri=3.5, H=50.0, UA_r=35.0, UA_m=5.0):
    Tsky = To - dTsky
    h_co = 4 + 4 * v
    h_ro = 4 * eps * SIGMA * ((To + Tsky) / 2 + 273.15) ** 3
    T_env = (h_co * To + h_ro * Tsky) / (h_co + h_ro)
    Rh, R_out = R_g / 2, R_g / 2 + R_f + 1 / (h_co + h_ro)
    a = [[-(UA_r + H + A * h_i), H, 0, A * h_i],
         [H, -(H + UA_m + A * h_ri), 0, A * h_ri],
         [h_i, h_ri, 1 / Rh, -(h_i + h_ri + 1 / Rh)],
         [0, 0, -(1 / Rh + 1 / R_out), 1 / Rh]]
    b = [-P - UA_r * To, -UA_m * To, 0, -T_env / R_out]
    Ta, Tm, Tg, Tgi = solve(a, b)  # any 4x4 linear solver
    T_int = Tg - (Tg - T_env) / R_out * Rh
    return Ta, Tgi, T_int
```

- **E1:** solve for the `P` that gives `T_int = 0` (it is linear), then report
  `Ta` and `Tgi`.
- **E2:** solve for the `P` that gives the held `Ta`, then report `T_int`.
- **E3** integrates the same balances with explicit Euler, `dt = 2 s`:

```text
C_a dTa/dt = P − UA_r(Ta−To) − H(Ta−Tm) − A·h_i(Ta−Tgi)
C_m dTm/dt = H(Ta−Tm) − UA_m(Tm−To) − A·h_ri(Tm−Tgi)
C_g dTg/dt = A·[h_i(Ta−Tgi) + h_ri(Tm−Tgi)] − A·q_out
interface below 0 °C:  q_out = (Tg − T_env) / (R_g/2 + R_f(m) + R_o)
while melting:         q_out = Tg / (R_g/2)
                       dm/dt = −[Tg/(R_g/2) − (0 − T_env)/(R_f(m) + R_o)] / L_f
```

`R_f(m)` shrinks as the frost mass `m` melts. The scripts were kept outside the
repository.

</details>

## Current Available Signals

### Hardware

[Repo] The car device is a Seeed XIAO ESP32-C3 with a **BMP280** sensor, which
measures temperature and pressure but not humidity. It switches a **Shelly
PM1** relay through the Shelly's local RPC API. The firmware lives in a separate local
checkout, `server/ESP32C3-Car-Heater/`, which the repository root `.gitignore`
excludes (line 23); that is why the audit did not find it.

- **Telemetry** is sent every 5–10 s (`src/io/PosterTask.cpp:258–285`). Each
  frame carries `timestamp`, `temperature` and the raw Shelly
  `Switch.GetStatus` JSON (`src/io/WebSocketTask.cpp:439–445`). Pressure is
  measured but not transmitted.
- **The load is only the cabin heater**, a DEFA Termini II 1700 [Owner]. The
  outlet has continuous power, with no timer in the circuit [Owner].
  - The firmware README's "car block heater" (`ESP32C3-Car-Heater/README.md:3`)
    is outdated.
  - The server's nominal 1000 W
    (`app/services/car_heater/kfactor/constants.py:22–26`) is below the model's
    1.7 kW name rating.
  - DEFA's Termini heaters use PTC elements that deliver about 20 % less power
    per 20 K of temperature rise [Lit: 18]. The Termini II 1700's own power
    settings and thermostat were not verified here.
  - Because the Shelly now measures the heater alone, its measured powered-on
    draw is a clean estimate of future heating power (audit F01). It also shows
    PTC decline and any thermostat cycling directly.
- **Battery charge mode is no longer used** [Owner]
  (`app/services/car_heater/car_heater_service.py:39–45`). Retiring it removes
  one competing command source (audit F11) and makes audit F14 moot.
- **The cabin sensor sits on the dashboard just below the windscreen**, and its
  exact position has never been fixed [Owner]. So:
  - It reads the air at the base of the windscreen and the dashboard surface:
    the cold air sliding off the glass, sun on the dashboard, and possibly the
    heater's jet. That is not mid-cabin air.
  - Its position varies between sessions, which makes historical calibration
    sessions less comparable with each other [Hyp].
- `ESP32_temperature/` is the home DHT sensor firmware, not the car's.

### What the software knows

| Quantity | Source and storage | Used by control? | Notes |
| --- | --- | --- | --- |
| Cabin air temperature | BMP280 → `car_heater_status.ambient_temp`, every frame | Ready-by, Keep at Temperature, calibration | On the dashboard below the windscreen, position not fixed [Owner] |
| Relay state | Shelly `output` → `is_heater_on` | Yes | Relay on is not proof of heat [Lit: 35] |
| Instantaneous power | Shelly `apower` → `instant_power_w` | Ready-by ETA (audit F01), charge mode | Measured active power, not a rating [Lit: 35]. PTC heaters draw less as the cabin warms [Lit: 18] |
| Delivered energy | Shelly `aenergy` total, last-minute energy and minute timestamp → `energy_*` columns (`app/core/schema.py:152–168`) | No | Gives energy per session without power aliasing |
| Voltage, current, Shelly device temperature | Status row | No | |
| Outside air temperature | FMI `t2m` at Seinäjoki Pelmaa (fmisid 101486). 10-min step, 2 h lookback, 120 s cache; the stale cache is kept on fetch failure (`app/services/weather/weather_service.py:15–25, 62–68, 156–176`) | Ready-by, calibration | Not stored with heater status. Stored as `esp32_temphum` rows labelled "Pelmaa" as a side effect of home-sensor posts (`app/blueprints/api/esp32_api.py:311–360`) |
| Wind | FMI `ws_10min` | Calibration bucket selection only | Only a per-session mean is persisted |
| Outside RH | FMI `rh` | No (displayed) | Stored with the "Pelmaa" rows. FMI also offers `td` (dew point), which is not fetched |
| Cloud cover | FMI `n_man` is fetched but **NaN at station 101486** | No | Checked live 2026-09-29 [Lit: 36]: airport station 137188 reports `n_man`, `wawa` and `vis`. HARMONIE point forecasts give hourly `TotalCloudCover`, `DewPoint`, `WindSpeedMS` and `RadiationLW` about 50 h ahead. `RadiationLW` is labelled only "long wave radiation"; its magnitude implies downward flux [Lit: 50] |
| Deadline, target, plan | Ready-by schedule JSON, a single row (`app/core/schema.py:288–295`) | Yes | **No history of past deadlines or outcomes** |
| Heating sessions | kFactor session summaries: outside mean/min/max, wind mean, cabin start/end/max, duration, quality, accepted | Calibration | No defrost outcome. The prediction-outcome table has no runtime writer (audit F13) |
| Raw history retention | Legacy SQLite triggers delete status rows older than 30 days (`app/core/database.py:290–297`), with a similar trigger for `esp32_temphum` | — | No PostgreSQL retention was found in migrations, so production may still hold last winter's traces (not queried) |

The software cannot currently know:

- the glass temperature;
- whether frost formed, and its type and amount;
- whether the driver had to scrape;
- the actual departure time;
- interior humidity;
- the heater's orientation;
- snow cover.

### Which additional measurement would matter

**The largest single gain in observability comes from a windshield inner-surface
temperature measured at a reference point in the driver's area.** It combines
the three unobservable influences into one number: how strongly the heater's
air reaches the glass, how much heat wind and sky remove, and the initial soak.
In the model, the inner-glass temperature at clearing stayed within
+0.4…+2.6 °C across all conditions, while cabin air at clearing ranged from 6.5
to 28 °C.

**How to measure it.**

- **Any sensor inside the car sees only the inner surface.** Glass does not
  transmit thermal infrared above about 5 µm, and uncoated glass has an
  emissivity of about 0.85 at 8–14 µm [Lit: 37, 38]. The outer surface has to
  be inferred through the laminate, which reads 0.8 K warmer on the inside at
  100 W/m² and 4 K warmer at 500 W/m² [Derived; PVB properties from 43].
- **Prefer a contact sensor, backed with foam and kept out of the airflow.** A
  bonded sensor reads a mix of glass and air:
  `offset = (T_glass − T_air)·R_so/(R_so + R_sa)`, where `R_so` is sensor-to-glass
  and `R_sa` sensor-to-air resistance. TI recommends a thin, thermally
  conductive film at the contact, insulating foam over the exposed side, and
  placement out of any airflow [Lit: 40]. Sensirion's design guide likewise
  warns that heated air in contact with a sensor raises its reading [Lit: 41].
  The error matters in practice [Derived]:
  - bare in fan airflow (`R_so:R_sa` ≈ 1:3), a sensor can read about +1 °C
    while the glass is still −5 °C: a false "ready";
  - foam-backed (≈ 1:20), the error falls to about 1.2 K.
- **An IR thermometer needs compensation.**
  - With emissivity ≈ 0.85, about 15 % of the signal is cabin radiation
    reflected off the glass. For glass at −5 °C in +20 °C surroundings, a
    reading with emissivity set to 1 shows about −0.8 °C: 4 K too warm, again in
    the "ready too early" direction [Derived].
  - A typical sensor datasheet guarantees accuracy only under isothermal
    conditions with the target filling the field of view [Lit: 39].
  - A low-e coating on the cabin-side surface (one patent gives emissivity
    0.17–0.22) makes interior IR unusable [Lit: 44]. Contact sensing is
    unaffected by coatings.
- **Placement.**
  - Keep off the black frit band at the edge, which heats differently from the
    vision area [secondary: 52].
  - The usual OEM spot for glass sensors, at the mirror base, is convenient but
    not necessarily where the driver's view clears last [Lit: 42].
- **Overnight use.** With the heater off, cabin air and glass are close in
  temperature, so the same sensor also tracks the glass's overnight radiative
  cooling. That reading can be compared with the outside frost point (see E).

| Candidate | Physical quantity | Why it helps | Inference it replaces | Placement | Necessary? |
| --- | --- | --- | --- | --- | --- |
| Windshield inner-surface temperature (contact sensor) | Glass temperature at one point | Readiness, hold, early warning, and an automatic label every session. With the heater off, it also tracks overnight cooling | Cabin target as readiness proxy; weather-based guesses about the glass | Bonded with thin conductive film to the inner glass in the driver's area, at the spot that clears last (lower-middle unless shown otherwise). Foam-backed, out of the heater's jet, off the frit band | **Yes**, for closed-loop readiness and fast learning |
| Second glass point (lower band) | Spatial spread | Checks that the reference point is representative | Assumption of uniform clearing | Near the cowl edge, driver's side | First winter only (validation) |
| Cabin RH (e.g. replace the BMP280 with a temperature/humidity sensor) | Cabin dew point | Interior fog risk from snow and wet mats. Does not predict exterior frost | Nothing today | A fixed, recorded position, shaded and away from the heater outlet (not loose on the dashboard) | Cheap enough to do together with the glass sensor: the owner is happy to swap the BMP280 [Owner]. RH sensors drift upward after long exposure near saturation [Lit: 45] |
| Outside temperature/RH at the car | Local microclimate | Station versus parking-spot differences (e.g. cold-air pooling) | Pelmaa station values | Radiation shield, ventilated, near car height, away from car and house heat | Nice to have; adds little over FMI plus the glass sensor |
| Camera inside, facing the glass | Visible frost (scattering, texture) | True outcome label | User report | Focused on the glass; grazing LED light at night | Optional labelling aid, not a control input. Needs a second board (see G) |
| Dedicated frost/ice sensor | Deposit presence at one spot | Whether frost formed overnight | Weather-based frost estimate | Simplest is an outside proxy plate with a thermistor at windshield tilt and similar sky view | Not justified for control. Proxy surfaces need per-site calibration against the real surface [Lit: 45]. Electrodes on the outer glass would not survive wipers or scraping [Derived] |

## Candidate Architectures

### A — Corrected current system

**How it works.** Fix the audit's defects, keep
`cabin ETA → start → reach target → thermostat hold`, and choose the cabin target
empirically for defrost, preferably reaching it a dwell period before departure.

**Sensors and data.** Existing only: cabin temperature, expected powered-on
heater power (not current power; audit F01), outside temperature, wind, and
calibration sessions.

**Strengths.**

- Most of the code exists, and comfort comes with it.
- ETA and "unreachable" are shown to the user.
- The physics extrapolates the *cabin* trajectory to conditions not yet seen.

**Weaknesses.**

- The target that clears the glass depends on sky, wind, airflow and frost type
  ([Evidence](#evidence)). Any single target is too high in mild weather or too
  low in cold, clear, windy weather.
- Cold-weather defrost targets fall where the ETA is ill-conditioned (E4). A
  10 % asymptote error means about 25 min late, or an immediate start hours
  early.
- It learns cabin parameters, never the outcome.
- It carries the heaviest calibration machinery and most of the audit's defects
  (F02–F06, F09, F10).

**Could an empirically chosen target be good enough?** Only if the heater's
airflow reaches the glass and most frost mornings are mild. Then something like
"+10…+12 °C, reached 15–20 min before departure and held" might clear reliably;
with aimed airflow the model cleared at 6.5–15.7 °C cabin air. That is "A plus a
dwell", already halfway to a duration rule, and it must be proven with data
(rule D2 in the data plan).

**Failure modes.**

- The glass is still frozen when the target is reached.
- The target is unreachable, so Ready-by starts immediately (audit E3).
- The thermostat holds a temperature that never clears the glass.
- Parameters jump after restarts (audit F06).

**Complexity.** High: the audit's option B remediation scope.

**Reuse.** Retains nearly everything (see [Comparison](#comparison)).

**Validation.** Record the cabin temperature at the observed clearing time on at
least 10 frost mornings. A is viable only if that temperature stays within a
narrow band well below what the heater can reach.

### B — Weather-based adaptive preheat duration

**How it works.**

- `duration = f(conditions) × margin`, and start = departure − duration.
- The heater runs continuously until departure plus an overrun, with a hard cap.
- `f` is a small table keyed on outside temperature, optionally adjusted for
  clear skies and wind.
- The margin is one number learned from outcomes.

**How simple can it be?** Industry practice is simpler still.

- DEFA, Calix, GARO and Theben timers all map one outside (or engine-bay)
  temperature to minutes before departure. They cap it around 3 h and keep
  heating 15–120 min past the departure time [Lit: 17–22].
- None uses cabin temperature as its stop criterion, and none uses humidity,
  wind, cloud cover or learning [Lit]. DEFA's optional cabin thermostat only
  holds a minimum temperature inside the timed window [Lit: 17].
- The tables differ by 20–40 % at the same temperature. At −10 °C: DEFA 150,
  GARO 135, Theben 120 min [Lit: 17, 21, 22].
- They appear designed around engine-heater equilibrium, which DEFA says takes
  about 3 hours [Lit: 18; interpretation].
- Motiva, the Finnish energy agency, advises half an hour of engine pre-heating
  in cool weather and at most two hours in hard frost [Lit: 23].

For this project, B needs a 4–6-point temperature table, a clear-sky/wind
multiplier and one learned log-margin: fewer than ten numbers.

**Sensors and data.**

- FMI: outside temperature (observed at planning time, forecast for departure),
  wind, cloud cover (airport station or HARMONIE) and dew point.
- Shelly power, to confirm the heater is actually drawing power.
- User outcome labels.

**Strengths.**

- Simple, explainable and predictable in energy use; matches proven industry
  practice.
- Almost stateless: a table and one number survive restarts.
- Independent of where the cabin sensor sits, and of the thermal model.

**Weaknesses.**

- **Open loop.** It cannot tell whether it worked, or warn that it will not.
- **The margin must absorb everything unmodelled.** In the model, clearing time
  at a fixed outside temperature varies 2–4× with sky and wind (E3).
- **Learning from one-bit labels is slow** [Derived: learning research note
  §5.2]:
  - at 95 % reliability, only about 5 failures occur per 100 departures;
  - about 108 labelled departures are needed to pin the typical duration within
    ±10 %.
- It cannot detect "cannot clear today", for example −20 °C without airflow at
  the glass.

**Failure modes.**

- Late on clear or windy mornings.
- Energy wasted on frost-free mornings.
- Heater faults go unnoticed without a power check.
- The user stops reporting outcomes.

**Complexity.** Low.

**Validation.**

- Collect outcome labels and evaluate by forward chaining (each morning judged
  only by what was known before it).
- Report the failure rate with exact binomial bounds.
- Track energy per departure.

### C — Empirical self-learning defrost-time model

**How it works.** Predict the time until clear, `T(x)`, from features known at
planning time, and plan at a high quantile of that prediction.

**Input features.**

- Outside temperature, now and forecast for departure.
- Wind.
- Cloud cover or long-wave radiation.
- Dew/frost point and overnight indicators: clear-sky hours, hours with the air
  near ice saturation.
- Cabin temperature at start (overnight soak, or recent driving).
- Measured powered-on heater power.
- Recent precipitation (glaze risk) and snow-depth change.

**How outcomes are labelled.**

- **User report at departure:** clear / wipers enough / partial scrape / full
  scrape. This is *censored*: "clear" says only that the required duration was
  no longer than the run.
- **Automatic time-to-threshold from a glass sensor:** continuous, and available
  on every session, including those without a departure.
- At a 95 % operating point, one continuous measurement is worth about 4.5
  binary labels. On frost-free mornings, binary labels are worth even less
  [Derived: learning research note §5.1].

**Cold start.** Use B's table as the prior, fitted by Bayesian or ridge
regression with priors centred on it, and keep margins wide until data
accumulates.

**A mature analogue.** Building automation solves the same "ready by time X"
problem with *optimal start*. LBNL's reference optimal-start block learns a
single number, the heating rate, as a moving average over the last 3 days of
measured time-to-setpoint, and caps the early start at 3 h [Lit: 28]. It learns
from a continuous measurement every day, not from success/failure. That is the
role a glass sensor would play here.

**Could a regression outperform the current calibration?** For the defrost goal,
in principle yes. A 2–3-coefficient log-duration regression is fitted to the
outcome itself. The calibration fits cabin-air curves whose relation to the
outcome varies from morning to morning (see
[the previous section](#why-cabin-temperature-may-or-may-not-be-the-right-proxy)).
With binary labels only, C collapses into "B plus one learned margin": at 95 %
reliability, one winter yields too few failures to fit even one more
coefficient by common rules of thumb [Derived: learning research note §4].

**Avoiding overfitting.**

- At most 2–3 coefficients plus a spread.
- Pool across conditions instead of fitting per bucket.
- Validate by forward chaining only.
- Never promote a model on in-sample RMSE or R², which is the lesson of audit
  F04.

**Strengths.** Optimises the actual outcome, and absorbs car-specific effects
such as heater placement implicitly.

**Weaknesses.**

- Needs labels.
- Extrapolates poorly to the first very cold morning.
- Drifts when the heater is moved.
- One winter of data is small.

**Failure modes.**

- Overconfidence after mild months.
- Noisy or missing labels.
- A "clear" frost-free morning mistaken for evidence of adequate heating.

**Complexity.** Medium; low if limited to 2–3 coefficients.

**Validation.** Score C and B on the same sessions, paired and forward-chained;
this needs continuous labels. Roughly 30–130 sessions are needed to detect a
meaningful difference [Derived: learning research note §5.3].

### D — Windshield-temperature based control

**How it works.**

- A contact sensor on the inner glass surface at a reference point in the
  driver's area.
- **Ready** when `T_glass,in ≥ θ` for at least `τ`. After that, **hold** the
  glass above `θ_hold` until departure plus a grace period.
- A planner (B, C or F) still decides when to start. D provides the success
  condition, the hold, early warning and automatic labels.

**Which threshold?** [Model/Derived]

| Threshold | Assessment |
| --- | --- |
| Inner glass above 0 °C | **Insufficient.** The inner surface is 0.5–4 K warmer than the interface while heat flows outward |
| Glass above the frost point | **Wrong criterion for removal.** Melting needs 0 °C at the interface. The frost point matters for frost *formation* (E) |
| Empirically learned threshold | **Yes.** Start with `θ = +3 °C` and `τ = 10 min`, then fit `θ` and `τ` to observed clearing |
| Threshold plus dwell | **Yes.** The dwell covers latent heat and the lower band, which clears last |

The model suggests a signature to look for. While the interface melts, the inner
surface sits at +0.3…+2.6 °C, and it rises only after the frost at that point
has gone. **A melting plateau followed by a rise** is therefore the expected
pattern [Model; Hyp for the real car].

**Limitations.**

- It measures one point on a windshield that clears unevenly.
- High heat flux (−20 °C with wind) pushes the plateau up to about +4 °C.
- A sensor in the heater's jet, or poorly bonded, reads cabin air instead of
  glass. Sun on the sensor also biases it.
- It knows nothing about frost mass or snow, and cannot tell "no frost formed"
  from "frost melted" (E can).

**Strengths.**

- It observes the variable that decides melting.
- Closed-loop confirmation, and an honest "not ready yet" warning.
- A continuous label on every session, automatically.
- A glass-based hold replaces an arbitrary cabin-temperature hold.
- It can detect "the heater cannot win today": the glass plateaus below θ
  while the cabin warms and the Shelly shows power drawn. Causes include too
  cold or windy weather, thermostat cycling, or airflow not reaching the glass.
- Sensor faults can be detected by comparing with cabin and weather readings.

**Failure modes.**

- The sensor comes unbonded or moves.
- The reference point is unrepresentative.
- Condensation forms on the sensor.
- A patch is clear while thin ice elsewhere still catches the wipers.

**Complexity.** Low to medium: one sensor on the existing ESP32, one field in the
payload, DTO and schema, and the readiness logic.

**Hardware.** One contact temperature sensor. See
[Which additional measurement would matter](#which-additional-measurement-would-matter).

**Validation.**

- On at least 5 dedicated mornings, take photos every 5–10 min and compare when
  the driver's area actually clears with when the threshold-plus-dwell rule
  fired.
- Optionally, add a second point in the lower band during the first winter.

### E — Glass temperature plus humidity, dew point and frost point

**How it works.** Adds humidity to D in two ways:

- **Interior fog margin:** cabin RH near the glass gives `T_glass,in − T_dew,cabin`.
  This is the logic of production anti-fog modules [Lit: 24–26].
- **Overnight frost formation:** FMI dew point gives the outside frost point.
  Hours with the glass below that frost point indicate that frost formed, and
  roughly how much.

**Which problem it addresses.**

| Problem | Does E help? |
| --- | --- |
| Interior fog prevention | **Directly**; this is production practice |
| Exterior frost prevention (glass kept above the frost point all night) | Physically possible. A Subaru patent keeps the glass slightly above saturation with an intermittently powered windshield heater [Lit: 27]. With a 1–2 kW cabin heater it is probably expensive [Hyp] |
| Active defrosting | Not directly: melting needs 0 °C regardless of humidity |
| Frost presence and severity | **Yes.** Skip or shorten heating on frost-free mornings, and give C a severity feature without asking the user |

**A proven analogue for predicting overnight frost.** FMI's RoadSurf road-weather
model predicts surface frost from a surface energy balance [Lit: 49]:

- its inputs are air temperature, dew point, wind, precipitation and incoming
  long-wave and short-wave radiation;
- frost ("deposit") accumulates by deposition while the surface is below the
  frost point.

FMI open data supplies every one of those inputs: forecast `RadiationLW`, and
observed incoming long-wave radiation at six stations [Lit: 36, 50]. Digitraffic
road stations also publish road-surface temperature minus frost point in real
time [Lit: 51]. For a windshield, the model's ground heat flux would become
conduction to the cabin, with a small heat capacity and the windshield's sky
view. A road surface is a biased proxy, though: a windshield usually cools
faster, so no frost on the road does not mean no frost on the glass [Derived].

**Strengths.**

- Covers a real visibility failure mode, interior fog during melting. The owner
  does not think it is a practical problem for this car, though [Owner].
- It saves energy by not heating on frost-free mornings. Here that mainly
  applies in autumn and spring, since in mid-winter the windows are frosted
  almost every morning [Owner].

**Weaknesses.**

- RH sensors drift or condense near saturation and below 0 °C.
- Frost formed overnight can sublimate or blow away before morning.
- Outside humidity comes from a station, not the car.

**Complexity.** Medium.

**Hardware.** A glass sensor plus a cabin temperature/RH sensor. Swapping the
BMP280 for a temperature/RH part makes the cabin half nearly free [Owner].

**Validation.**

- Compare predicted frost/no-frost with user labels on both kinds of morning.
- Compare interior fog events with the margin.

### F — Hybrid physics plus empirical model

**How it works.** Physics supplies the structure and data supplies a small
correction. For example:

- `ln T_clear = β0 + β1·ln t_phys(x)`, where `t_phys` is a physics-predicted
  time from a cabin ETA or a glass-node model; or
- a two-node cabin-plus-glass model whose exterior coupling depends on wind and
  sky, calibrated on glass measurements.

**Does it extrapolate better with less data?** It does when the structure is
right: radiation, wind and the heater's ceiling. A physics feature carries the
shape into colder or windier conditions than any seen, so fewer parameters have
to be learned from data [Derived: learning research note §1.1].

Without a glass sensor, though, the glass node cannot be observed. Fitting it
from cabin data repeats the audit's identifiability problem (F10). **F
therefore requires D.**

**Strengths.**

- Uses data efficiently.
- Its effective parameters are interpretable.
- Principled "cannot clear today" detection from the steady-state ceiling.

**Weaknesses.**

- The highest modelling complexity.
- Risks repeating the audit's optimizer, gate and promotion problems.
- Needs careful out-of-sample validation.

**Complexity.** High.

**Validation.** Must beat C and B on held-out sessions, especially when
extrapolating to colder weather.

### G — Direct frost detection

A camera behind the mirror, or an optical sensor, would observe the outcome
itself. The idea has been proposed:

- Mobileye patents classify image patches from an inside camera focused on the
  glass as frost, clear or other, with a pulsed light for night use [Lit: 46].
- An Ambarella patent uses camera frames of a car parked outdoors to detect
  windshield frost [Lit: 46].

Against it:

- **No evidence of accuracy.** No peer-reviewed study of detecting frost on
  windshields from images was found. The closest analogue tracks frost growth on
  a lab heat exchanger [Lit: 47].
- **Hardware.** Espressif's camera driver does not list the ESP32-C3, so a
  second board would be needed [Lit: 48].
- **Night imaging.** It needs its own light. Grazing light, and frames
  differenced with the LED on and off, would be needed to avoid reflections
  [Derived].
- **Confounders.** Interior fog, snow cover, lens condensation, and clear glaze
  ice, which is nearly transparent.
- **Privacy.** An outward-facing camera captures people and number plates.
- **No production precedent.** No production system that measures exterior
  frost was found, only patents and rain sensors that stop wiping on ice
  [Lit: 26, 27].

A phone photo taken by the user at departure gives the same label for data
collection at no engineering cost.

**Verdict:** not justified as a control input; at most a temporary labelling aid.

## Comparison

| Criterion | A: cabin target | B: duration | C: learned time | D: glass control | E: glass + humidity | F: hybrid | G: frost detection |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Ready on time | Low–medium; the right target depends on conditions | Medium; high with a generous margin | Medium–high once labelled | High for confirmation; planning via B/C | As D, plus awareness of interior fog | High if validated | Detection only; planning via B/C |
| Unnecessary early heating | High in mild weather, or when "unreachable" | Medium (the margin) | Low–medium | Low–medium | Lowest in autumn and spring (skips frost-free mornings); like D in mid-winter | Low | Low |
| Energy per departure | Variable; hours of overspend on unreachable targets | Predictable overspend, sized by the margin | Lower after learning | Lower after learning; holds on glass | Lowest | Low | Low |
| Sensitivity to weather or forecast error | Medium | High | Medium–high | Low (measured) | Low–medium | Medium | Low |
| Sensitivity to sensor error | High: success is defined on one cabin point | Low | Medium (label quality) | Medium (bonding, location) | Medium–high (RH near saturation) | Medium | High (lighting, fog) |
| Learning / calibration time | Long, and learns the wrong quantity | None to start; the margin learns slowly | One winter with continuous labels; several with binary | A few mornings to validate θ and τ | Needs both frost and frost-free mornings | Longest | Classifier validation |
| Reliability after restart or data loss | Medium (bucket and override state, audit F06) | High | Medium (falls back to the prior) | High (live measurement) | High | Medium | Medium |
| Implementation complexity | High | Low | Medium | Low–medium | Medium | High | High |
| Additional hardware | None | None | None, or a glass sensor | Glass sensor | Glass sensor + cabin RH | Glass sensor | Camera and lighting |

**Energy and timing** [Derived; the electricity price is an assumption]:

- **A safety margin is cheap.** 15 extra minutes at 1.7 kW is 0.43 kWh, about
  €0.06 at 0.15 €/kWh; over 100 heated departures, about €6.
- **The cost-optimal reliability is therefore high.** Valuing lateness at even
  €0.10–1.00 per minute puts it at about 96–99.6 % [Derived: learning research
  note §3].
- **Long waste matters more than minutes.** The energy that matters is the
  avoidable hours:
  - A's "unreachable, so start now" branch, e.g. 3 h × 1.7 kW = 5.1 kWh;
  - an untimed thermostat hold;
  - heating on mornings with no frost.
- **Reliability comes first.** A conservative five-minute-early plan is
  preferable to an optimiser that is occasionally late.

**What happens to existing components** (merits first; migration cost is only a
tie-breaker):

| Component | A | B | C | D | E | F | G |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `ThermalPhysics` | Retain unchanged | Retain, change role (diagnostics, optional comfort) | Retain, change role (feature) | Retain, change role | Retain, change role | Refactor (add glass node) | Retain, change role |
| kFactor calibration | Refactor (audit scope) | Supersede for scheduling. Keep the passive session archive; autonomous mode stays off and is removed once unused | Refactor into defrost-session extraction | Supersede | Supersede | Refactor (fit the correction) | Supersede |
| Ready-by scheduling | Retain and fix | Refactor. Keep schedule, persistence, UI and commands; plan from duration; complete at departure + overrun | Refactor (learned plan) | Refactor (glass readiness and hold) | As D | As D | As D |
| Keep at Temperature | Retain | Retain for comfort only, out of the defrost path | As B | Refactor into a glass hold, or keep for comfort only | As D | As D | As D |
| Shelly power telemetry | Retain, plus an expected powered-on estimate | Retain, change role (confirms heating; energy per session) | Retain, change role (feature, energy) | Retain | Retain | Retain (model input) | Retain |
| Weather integration | Retain, plus freshness checks | Refactor: forecast, cloud cover (airport or HARMONIE), dew point; store with each session | Refactor (features) | Retain | Refactor (dew and frost point) | Refactor (radiation, wind) | Retain |
| Historical calibration data | Retain | Retain as archive | Retain, change role (weak prior on cabin warm-up) | Archive | Archive | Retain (initial cabin priors) | Archive |

"Remove" applies only to the unused prediction-outcome table, which a defrost
outcome record would supersede. Autonomous calibration can also go eventually,
once nothing consumes cabin-model parameters. The remove candidates are few
because the costly part, the calibration stack, simply leaves the critical
path.

## Recommended Direction

**Make windshield state the success condition, plan with a weather-keyed
duration, and learn start times from measured glass temperature.** That is a B
planner with D readiness and hold, then C- or F-style learning once data exists.
The one-node cabin model continues as a diagnostic and comfort tool.

Why this suits this project:

1. **Physics.** Cabin air and power cannot observe the conditions that decide
   exterior defrost. At the same cabin temperature, the interface temperature
   spread over 4–7 K between mornings (E2). The inner-glass temperature at
   clearing was nearly independent of conditions (E3).
2. **Industry.** Every mains-powered Nordic product examined plans by outside
   temperature and duration, and none uses cabin temperature as its stop
   criterion [Lit: 17–22]. Measuring temperature at the windshield is
   established automotive practice, albeit for interior fog [Lit: 24–26].
   Regulations define success as cleared vision area, not temperature
   [Lit: 1, 4].
3. **Learning.** Only a continuous per-session label makes learning feasible
   within a winter: about 24 sessions instead of about 108 labelled departures
   to pin the typical duration, and it enables paired comparison of candidate
   models [Derived: learning research note §5].
4. **Existing code.** The Ready-by schedule, persistence, UI and command path
   can all stay. The part with most of the audit's defects, the calibration
   stack, moves off the critical path.
5. **Simplicity and reliability.** B survives restarts and is easy to reason
   about. D adds verification and an early warning. A conservative margin costs
   cents.

**Zero-code lever.** The model's largest single lever is physical: whether the
heater's airflow reaches the windshield. Test aiming the heater at the glass if
the heater's instructions allow it, and record the placement as configuration.
Typical mid-winter mornings here are −8…−14 °C [Owner], where the model is
marginal. Placement is likely the difference between a 1.7 kW heater clearing
the glass and not clearing it [Model/Hyp]. Today the heater blows at the
passenger headrest, not the glass [Owner]. The cheapest experiment in this
whole study is to turn it toward the base of the windscreen, within the
heater's placement rules, and compare mornings (rule D4). Heating the passenger
seat does nothing for the windshield.

**Cold spells need an honest answer.** At −20 °C and below, no modelled
configuration cleared the frost within 4 h [Model]. The system should say
"expect to scrape" instead of implying readiness. The glass sensor detects this
as a plateau below the threshold.

**No-hardware fallback.** If a glass sensor is unacceptable, B with outcome
labels and one learned margin still works. It is reliable only with a generous
margin, learns slowly, and cannot warn in time.

**Is the evidence enough to choose?** Physics and industry practice strongly
support the direction: success on glass state, duration-based planning. They do
not support any specific threshold, duration or model structure for this car.
Hence the data-first minimum viable version below, with explicit decision rules.

**Audit findings that still matter under this direction:**

- F07 (cancel losing OFF);
- F11 (mode ownership and command lifetime);
- F12 (stale data and expiry policy);
- F09's lesson to record missing values as missing, not 0;
- F01's powered-on power semantics, wherever power feeds a prediction.

F02–F06 matter only if the cabin model stays in a decision path, which is
likely, since the owner wants to keep the comfort mode for now [Owner]. F14 is
moot, because charge mode is no longer used; retiring it also simplifies F11.

## Minimum Viable Version

The simplest version worth building first. This is a description, not an
implementation spec.

1. **Sensors at fixed, recorded positions.**
   - A contact temperature sensor bonded to the inner windshield in the
     driver's area, lower-middle, outside the heater's jet, with its back
     insulated. The ESP32 sends it as an additional field next to
     `temperature`.
   - At the same time, replace the loose dashboard BMP280 with a
     temperature/RH sensor in a fixed spot [Owner: swap acceptable].
   - Fix the heater's placement and orientation, and record them. It
     currently blows toward the passenger headrest [Owner]. Run the first
     observation mornings with that placement as the baseline, then aimed at
     the windscreen base (rule D4).
2. **Duration planner.**
   - Start with the most conservative of the published timer tables: roughly
     DEFA's automatic chart, 90 min at 0 °C, about 120 min at −5 °C, 150 min at
     −10 °C and 180 min at −15 °C or below [Lit: 17]. Cap at 180 min.
   - When the table says the cap is not enough, tell the user (for example
     "expect to scrape").
   - Log cloud cover, wind and dew point, but do not use them yet.
   - **The 2-hour question will come up immediately.** The owner would revisit
     run-time safety if heating creeps past 2 h [Owner]. At the typical
     mid-winter −8…−14 °C, DEFA's chart gives about 140–175 min. DEFA's tables
     are sized for engine heating, and a cabin-only defrost may need less.
     Measured glass clearing times from the first cold weeks will show
     whether 2 h suffices. Alternatively, cap at 120 min from the start and
     accept some scraping while learning; that is the owner's choice.
3. **Execution.** Heat continuously from the planned start until departure plus
   30 min, then send an explicit OFF with a retry. Keep a cabin over-temperature
   cutoff. Keep at Temperature is not part of this mode.
4. **Readiness indicator, display only.** "Windshield probably clear" when the
   glass is at least +3 °C for at least 10 min. It does not control anything
   until it has been validated.
5. **Outcome label.** A single tap at departure: clear / wipers enough /
   partial scrape / full scrape, plus the frost type.
6. **Session record.** Every field in the
   [Data Collection Plan](#data-collection-plan).

This version is deliberately conservative and wastes some energy at first. Its
job is to be reliable while it produces the data that will shorten it.

## Evolution Path

| Stage | Adds | Advance when |
| --- | --- | --- |
| 0 | The MVP: fixed duration table, glass sensor for display and logging, outcome labels | Installed |
| 1 | Glass-confirmed readiness and a hold on the glass; early "not ready" warning; one learned log-margin on the table via quantile tracking (a failure moves the margin up by `η(1−α)`, a success moves it down by `ηα`) [Lit: 29, 30; 32, 33] | Rule D1 passes (glass proxy valid) |
| 2 | A C-style model: 2–3 coefficients on log time-to-threshold (outside temperature, wind, cloud cover or long-wave radiation, soak), planned at the 90–95 % quantile with the tracker on top. A distribution-free upper bound by conformal prediction needs at least 19 calibration sessions at 95 % [Lit: 31] | About 20–30 sessions with glass data, and forward-chained error beats the table |
| 3 | E: an overnight frost-formation estimate to skip or shorten heating, plus cabin RH for interior fog. F: a glass-node physics feature if residuals show structure, for example cold extrapolation errors | Forward-chained gains on held-out mornings; seen frost and frost-free mornings |
| 4 | G, a camera for labels | Only if user labels prove too unreliable |

At each stage, keep the previous stage's planner as the fallback when inputs go
missing: no glass data → table; no weather → last-known conservative duration.

## Data Collection Plan

### Per heating session (automatic)

| Field | Source | Notes |
| --- | --- | --- |
| Session id and trigger (schedule, manual, keep, calibration) | Server | |
| Requested departure time | Schedule | Persist per session; it is currently overwritten |
| Planned start, command sent, relay-on confirmed, first power above threshold | Server, Shelly | Separates the plan from actual heating |
| Heater-off time and reason | Server | |
| Actual departure | User label, or inferred from telemetry loss or unplugging | |
| Cabin temperature (and RH, after the sensor swap) trajectory | Existing status rows | Keep raw rows permanently [Owner] |
| Glass inner-surface temperature trajectory | New sensor | In the same frames |
| Relay state, power, `aenergy` counters | Existing | Gives delivered energy per session |
| Weather at planning, start and departure: `t2m`, `td`, `rh`, `ws_10min`, `wg_10min`, `ri_10min`/`r_1h`, `snow_aws` (Pelmaa); `n_man`, `wawa`, `vis` (airport 137188); HARMONIE forecast for departure (`Temperature`, `DewPoint`, `WindSpeedMS`, `TotalCloudCover`, `RadiationLW`) | FMI | Store the values used, with their observation times (audit F12) |
| Overnight history since parking: minimum temperature, clear-sky hours, hours with air at or above ice saturation, precipitation | FMI; can be re-fetched later | Frost-likelihood features |
| Soak: time since the last heating session and the last drive (telemetry gap); cabin minus outside temperature at start | Server | |
| Configuration: heater model and orientation, sensor positions, firmware, software and planner versions | User, config | Log every change |
| Missing values | — | Record as missing, never as 0 (audit F09) |

### Per departure (user, one tap, optional detail)

- Driver's area: clear / wipers only / partial scrape / full scrape.
- Other windows needed for driving: clear or not.
- Frost type: none / light hoarfrost / heavy hoarfrost / ice (glaze or frozen) /
  snow cover (brushed).
- Interior fog or frost: yes or no.
- When it was observed, if not at departure; optionally a photo.

### Dedicated observation mornings (at least 5)

- **Photos:** from heater start, photograph the windshield every 5–10 min from
  the same position until it is clear. Note when the driver's area first
  becomes clearable with wipers. This gives an uncensored time-to-clear.
- **Range of conditions:** include at least one clear-sky frost morning below
  −8 °C and one near 0 °C (with ice or glaze if it occurs).
- **Heater placement:** compare the current placement (passenger footwell,
  blowing at the passenger headrest [Owner]) with the heater aimed at the
  windscreen base, on mornings with similar conditions.

### Retention and retrospective data

- **Requirement** [Owner]: this data must never be deleted. That covers raw
  status rows, the weather values used, session records and labels.
- **Current state:**
  - The legacy SQLite schema deletes `car_heater_status` and `esp32_temphum`
    rows older than 30 days (`app/core/database.py:290–297`).
  - No PostgreSQL retention was found in migrations.
  - The owner believes last winter's history is gone.
- **Before collection starts:**
  - check read-only what production actually holds;
  - make sure nothing deletes these tables;
  - back them up.

  This is an implementation follow-up, not done in this study.
- **If any of last winter's rows survive:** export the heating sessions and
  re-fetch FMI history for those times. This measures cabin warm-up and the
  heater's reachable cabin temperature at each outside temperature, which bears
  on A's feasibility. It gives no defrost outcomes. The loose, varying sensor
  position limits what it can show [Owner].
- **Otherwise** the dataset starts this winter.

### Minimum data to choose, and decision rules

**Minimum dataset:**

- About 10–15 frost mornings with glass trajectories and labels.
- At least 5 of them dedicated observation mornings.
- Two temperature bands: about −2…−8 °C and −10 °C or colder. The owner's
  typical −8…−14 °C mornings fall in both.
- Both clear and cloudy nights.
- If one occurs, at least one cold-spell morning (−20 °C or colder), to confirm
  that "cannot clear" is detected and announced.

**Decision rules:**

| Rule | Question | Criterion | Consequence |
| --- | --- | --- | --- |
| D1 | Is the glass proxy valid? | Threshold-plus-dwell time within ±10 min of observed clearing on at least 4 of 5 observed mornings | Adopt D readiness. Otherwise move the sensor or change θ and τ |
| D2 | Is A viable? | Cabin temperature at observed clearing spans at most 4 K across conditions and lies at least 3 K below the heater's reachable cabin temperature | A fixed cabin target plus dwell is viable. Otherwise reject A |
| D3 | Is B enough without closed loop? | Residual log-SD of time-to-clear after a temperature-only fit | ≤ 0.2: B with a ×1.3–1.4 margin suffices. ≥ 0.35: add cloud and wind features, rely on D, or both |
| D4 | Does heater placement matter? | Aiming the heater at the glass cuts time-to-clear by at least 30 % in similar conditions | Adopt that placement, if the heater's instructions allow |

**Choosing an architecture versus proving reliability.**

- With continuous labels, about 24 sessions pin the typical duration to ±10 %
  (log-SD 0.25) [Derived: learning research note §5.2].
- With binary outcomes, claiming a failure rate below 5 % at 95 % confidence
  needs 59 departures without a failure [Derived: learning research note §5.3].

So the architecture choice can be made from 10–15 instrumented mornings, while
reliability is demonstrated over the season.

## Open Questions

### Answered by the owner (2026-09-29)

| Question | Answer [Owner] | Effect on this study |
| --- | --- | --- |
| What the Shelly switches | Only the cabin heater, a DEFA Termini II 1700. Battery charge mode is no longer needed | Measured power is pure heater draw, a clean estimate of future heating power. Charge mode can be retired (audit F11, F14) |
| Cabin sensor position | On the dashboard just below the windscreen; never fixed | It reads near-glass air, not mid-cabin air, and varies between sessions. Fix its position (MVP) |
| Windshield | Laminated safety glass, probably green-tinted solar-control; not heated, not acoustic | No low-e benefit against frost. The contact glass sensor is unaffected |
| Parking spot | In the open; a tall building 30 m to the west; car at 62.8000 N, 22.8260 E | Pelmaa is about 23 km away, the airport station about 12.7 km. Use forecasts at the car's coordinates; some shelter from westerly wind |
| Frost frequency and temperatures | Frosted almost every winter morning; typically −8…−14 °C, cold spells to −35…−45 °C | Typical mornings sit in the model's marginal zone; cold spells need "cannot clear" handling; skipping frost-free mornings helps mainly in autumn and spring |
| Comfort | Keep the comfort mode for now; value unknown until tried | A stays, as a comfort mode outside the defrost path |
| Run-time policy | Ignore for now, but revisit if heating creeps past 2 h | DEFA's chart already exceeds 2 h at typical temperatures (see MVP) |
| Power window | None; the outlet timer was removed | The planner fully controls start time |
| History | Probably gone; car heater data must never be deleted in future | Dataset starts this winter; retention must be verified and disabled |
| Interior fog | Probably not a problem; swapping the BMP280 for a humidity-capable sensor is fine | Cabin RH comes almost free with the sensor swap |
| Heater placement | Passenger footwell, against its left (centre-tunnel) wall, blowing about 45° upward, roughly at the passenger headrest | The jet does not reach the windshield, so the model's natural-to-fan-mixed cases apply, not "aimed at the glass". Test rule D4 early |

### Still open

- **Heater settings and placement rules:** the Termini II 1700's actual power
  settings and thermostat behaviour, which the Shelly power history will show,
  and where its manual allows it to be placed. DEFA's product page could not be
  fetched.
- **Car orientation:** which way the windshield faces, relative to the building
  and the morning sun. Late-winter departures after sunrise may get solar help
  that the model ignores.
- **Sensor positions:** the fixed positions for the glass and cabin sensors,
  once chosen.
- **Surviving history:** whether production still holds any heater rows (a
  read-only check).
- **The 2-hour threshold:** to be revisited when the first cold-week clearing
  times are in.
- **Comfort:** whether the comfort mode earns its keep, after a season of use.

## Sources

External sources were consulted on 2026-09-29. "Abstract only" means only the
abstract or bibliographic record was verified. Derivations marked [Derived] and
the synthetic model are this study's own work. No source is presented as a
measurement of this installation.

**Standards and regulations**

1. 49 CFR 571.103, FMVSS No. 103, Windshield defrosting and defogging systems — https://www.ecfr.gov/current/title-49/subtitle-B/chapter-V/part-571/subpart-B/section-571.103
2. SAE J902a (1967), Passenger Car Windshield Defrosting Systems, as incorporated by reference (archive copy) — https://archive.org/stream/gov.law.sae.j902a.1967/sae.j902a.1967_djvu.txt
3. NHTSA compliance test report 103-GTL-20-003, 2020 Honda Insight (TP-103-13) — https://static.nhtsa.gov/odi/ctr/9999/TRTR-646404-2020-001.pdf
4. Commission Regulation (EU) No 672/2010, Article 2 and Annex II, as adopted — https://www.legislation.gov.uk/eur/2010/672/body/adopted and https://www.legislation.gov.uk/eur/2010/672/annex/II/adopted (EUR-Lex: https://eur-lex.europa.eu/eli/reg/2010/672/oj/eng). Repealed from 6 July 2022; see [6].
5. Council Directive 78/317/EEC — https://www.legislation.gov.uk/eudr/1978/317/contents
6. Commission Implementing Regulation (EU) 2021/535, Annex VI, successor to 672/2010 — https://eur-lex.europa.eu/eli/reg_impl/2021/535/oj/eng (not fetched. A secondary test-house summary states the technical requirements are unchanged apart from battery charge state for electrified vehicles: https://www.atic-ts.com/the-updates-of-new-regulation-eu-2021535-regarding-general-vehicle-construction/)
7. GB 11555-2009 (Wikisource transcription) — https://zh.wikisource.org/wiki/GB_11555-2009_%E6%B1%BD%E8%BD%A6%E9%A3%8E%E7%AA%97%E7%8E%BB%E7%92%83%E9%99%A4%E9%9C%9C%E5%92%8C%E9%99%A4%E9%9B%BE%E7%B3%BB%E7%BB%9F%E7%9A%84%E6%80%A7%E8%83%BD%E5%92%8C%E8%AF%95%E9%AA%8C%E6%96%B9%E6%B3%95

**Physics**

8. EnergyPlus Engineering Reference v24.2, Climate Calculations — Sky Radiation Modeling — https://bigladdersoftware.com/epx/docs/24-2/engineering-reference/climate-calculations.html
9. EnergyPlus Engineering Reference v24.2, Outside Surface Heat Balance — External Longwave Radiation — https://bigladdersoftware.com/epx/docs/24-2/engineering-reference/outside-surface-heat-balance.html
10. WMO-No. 8, Guide to Meteorological Instruments and Methods of Observation (2008), Part I Ch. 4 and Annex 4.B — https://community.wmo.int/en/activity-areas/imop/cimo-guide (copy used: https://www.weather.gov/media/epz/mesonet/CWOP-WMO8.pdf)
11. Hamberg I. et al. (1987), Radiative cooling and frost formation on surfaces with different thermal emittance, Applied Optics 26(11):2131–2136 — https://opg.optica.org/ao/abstract.cfm?uri=ao-26-11-2131 (abstract only)
12. Norin F. et al. (1987), Prevention of Frost Formation on Automobile Glazing, SAE 870038 — https://saemobilus.sae.org/papers/prevention-frost-formation-automobile-glazing-870038 (abstract only)
13. Du X. et al. (2020), A numerical prediction and potential control of typical icing process on automobile windshield under nocturnal radiative cooling and subfreezing conditions, Proc. IMechE Part D 234(5):1480–1496 — https://doi.org/10.1177/0954407019875356 (abstract only)
14. Amnuayphan P. et al. (2019), The Effect of Water Evaporation in Automotive Windshield Defrosting, Engineering Journal 23(4):107 — https://doi.org/10.4186/ej.2019.23.4.107 (full text)
15. Aroussi A. et al. (2000), Improving Vehicle Windshield Defrosting and Demisting, SAE 2000-01-1278 — https://doi.org/10.4271/2000-01-1278 (abstract only)
16. Aroussi A. et al. (2001), Comparison of Performance between Several Vehicle Windshield Defrosting and Demisting Mechanisms, SAE 2001-01-0582 — https://doi.org/10.4271/2001-01-0582 (abstract only)

**Pre-heating products and guidance**

17. DEFA SmartStart User and Installation Guide — https://www.defa.com/content/uploads/Documentation/Downloads/Technical-information/Electrical-preheating/SmartStart-User-Installation-Guide-GB.pdf
18. DEFA Technical Handbook (2009) — https://www.defa.com/content/uploads/Documentation/Downloads/Technical-information/Electrical-preheating/Technical-Handbook-EN.pdf
19. Calix Bluetooth Timer user guide (Android) — https://products-cars.calix.se/files/3694bbd867352c4bf5a80eaf05d61f31.pdf
20. Calix Timer product page — https://calix.se/en/products/calix-timer/
21. GARO car-heating outlet housing (AEL) user guide, 2009 — https://joensuunelli.fi/wp-content/uploads/2020/07/Autolammitys_kayttoohje.pdf
22. Theben DCH 036 car-heating timer, Finnish manual — https://sahkonumerot.fi/3504502/doc/operatinginstructions/
23. Motiva, Sähköauton lataus ja auton lämmitys – energiatehokas autoilu (updated 13.1.2026) — https://www.motiva.fi/tietopankki/sahkoauton-lataus-ja-auton-lammitys-energiatehokas-autoilu/ ; secondary summary attributing cabin-heater limits to Motiva: https://www.ksoy.fi/energiaa/lammitatko-autosi-oikein/

**Automotive sensors and patents**

24. ScioSense WSS2 windshield anti-fogging sensor — https://www.sciosense.com/wss2-windshield-anti-fogging-sensor/
25. Sensirion, antifogging application — https://sensirion.com/products/applications/mobility/antifogging
26. HELLA rain/light/climate sensor — https://www.hella.com/microsite-electronics/en/Rain-light-climate-sensor-143.html ; HELLA TechWorld, functions of rain and light sensors — https://www.hella.com/techworld/ae/lounge/functions-of-rain-sensors-light-sensors/
27. Patents (proposals, not proof of production use): GM US 2012/0047929 A1 — https://patents.google.com/patent/US20120047929 ; Nissan US 2007/0227718 A1 — https://patents.google.com/patent/US20070227718 ; Hyundai/Kia US 9,682,686 B2 — https://patents.google.com/patent/US9682686B2/en ; Fuji Heavy Industries (Subaru) US 4,902,874 A — https://patents.google.com/patent/US4902874A/en

**Learning and control**

28. LBNL Modelica Buildings Library, `Buildings.Controls.OBC.Utilities.OptimalStart` — https://simulationresearch.lbl.gov/modelica/releases/latest/help/Buildings_Controls_OBC_Utilities.html
29. Angelopoulos A. N., Candès E. J., Tibshirani R. J. (2023), Conformal PID Control for Time Series Prediction (quantile tracking) — https://arxiv.org/abs/2307.16895
30. Gibbs I., Candès E. J. (2021), Adaptive Conformal Inference Under Distribution Shift — https://arxiv.org/abs/2106.00170
31. Angelopoulos A. N., Bates S. (2021), A Gentle Introduction to Conformal Prediction and Distribution-Free Uncertainty Quantification — https://arxiv.org/abs/2107.07511
32. Kaernbach C. (1991), Simple adaptive testing with the weighted up-down method, Perception & Psychophysics 49(3):227–229 — https://doi.org/10.3758/BF03214307 (abstract only); Levitt H. (1971), Transformed Up-Down Methods in Psychoacoustics, JASA 49(2):467–477 — https://pubmed.ncbi.nlm.nih.gov/5541744/
33. Robbins H., Monro S. (1951), A Stochastic Approximation Method — https://doi.org/10.1214/aoms/1177729586 (abstract only); Joseph V. R. (2004), Efficient Robbins–Monro procedure for binary data, Biometrika 91(2):461–470 — https://www2.isye.gatech.edu/~brani/isyestat/03-09.pdf

**Data and repository**

34. [Car heater physics and control audit](car-heater-physics-and-control-audit.md), this repository, revision `b95b30a`.
35. Shelly Gen2+ Switch component (`output`, `apower`, `aenergy`) — https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/Switch/
36. FMI open data WFS: stored-query catalogue (https://opendata.fmi.fi/wfs?service=WFS&version=2.0.0&request=describeStoredQueries) and live `fmi::observations::weather::simple` (fmisid 101486 and 137188) and `fmi::forecast::harmonie::surface::point::simple` (62.9 N, 22.5 E) responses, 2026-09-29.

**Sensing and frost detection**

37. Optris, Non-contact temperature measurement — glass industry (brochure Glass-BR-EN2023-05-B), emissivity and low-E sections — https://optris.com/us/wp-content/uploads/sites/2/2024/09/Glass-BR-EN2023-05-B_web.pdf
38. A&D Company, Measuring temperature of water and glass through infrared thermometers (AD-5635) — https://blog.aandd.co.jp/en/gpdme/ad-5635/mizu-garasu-ondosokutei
39. Melexis, MLX90614 datasheet, application considerations — https://www.melexis.com/-/media/files/documents/datasheets/mlx90614-datasheet-melexis.pdf
40. Texas Instruments, SNOA986A, Precise Temperature Measurements With the TMP116 and TMP117, §4 and §10 — https://www.ti.com/lit/an/snoa986a/snoa986a.pdf
41. Sensirion, Design Guide for Humidity and Temperature Sensors, v2 (2024) — https://sensirion.com/media/documents/FC5BED84/662B494D/Sensirion_Humidity_Temperature_Design_Guide.pdf
42. Sensirion Automotive, Improved driver safety by ensuring a clear view: SAAF sensor — https://sensirion-automotive.com/company/news/press-releases-and-news/article/improved-driver-safety-by-ensuring-a-clear-view-saaf-sensor-as-part-of-the-solution
43. Eastman, Saflex Crystal Clear (RB4N) PVB technical data sheet — https://saflex-vanceva.eastman.com/content/dam/saflex/pdf-documents/arch/saflex/technical-data-sheet/product_technical_sheet_-_saflex_crystal_clear_rb4n_092820.pdf
44. Coating patents (proposals): US 10,556,821 B2 (low-E on the interior surface) — https://patents.google.com/patent/US10556821B2/en ; US 12,202,765 B2 (low-e on surfaces 2/3) — https://patents.google.com/patent/US12202765B2/en ; US 4,368,945 A (IR-reflecting film between PVB layers) — https://patents.google.com/patent/US4368945A/en
45. WMO-No. 8, Guide to Instruments and Methods of Observation, Vol. I (2023), Annex 4.A §17, §4.2.2–4.2.5, §6.6.1–6.6.2 — https://library.wmo.int/records/item/68695-guide-to-instruments-and-methods-of-observation
46. Camera patents (proposals): Mobileye US 8,553,088 B2 and US 9,185,360 B2 — https://patents.google.com/patent/US8553088B2/en , https://patents.google.com/patent/US9185360B2/en ; Ambarella US 11,001,231 B1 — https://patents.google.com/patent/US11001231B1/en
47. Aguiar, Gaspar, Dinho da Silva (2022), Image recognition method for frost sensing applications, Energy Reports 8:234–240 — https://doi.org/10.1016/j.egyr.2022.01.049
48. Espressif, `esp32-camera` driver, supported SoCs — https://github.com/espressif/esp32-camera
49. Karsisto V. E. (2024), RoadSurf 1.1: open-source road weather model library, Geosci. Model Dev. 17:4837–4853 — https://doi.org/10.5194/gmd-17-4837-2024 ; Kangas M., Heikinheimo M., Hippi M. (2015), RoadSurf: a modelling system for predicting road weather and road surface conditions, Meteorol. Appl. 22(3):544–553 — https://doi.org/10.1002/met.1486
50. FMI open data parameter metadata and changelog — https://opendata.fmi.fi/meta?observableProperty=observation&param=n_man&language=eng ; https://en.ilmatieteenlaitos.fi/open-data-changelog
51. Fintraffic Digitraffic road weather API (sensor list, e.g. road surface minus frost point) — https://tie.digitraffic.fi/api/weather/v1/sensors
52. Secondary: Hackaday, Tech In Plain Sight: Windshield Frit (2024) — https://hackaday.com/2024/01/18/tech-in-plain-sight-windshield-frit/

Repository evidence: the paths and line numbers above refer to revision
`b95b30a` and the local, git-ignored `ESP32C3-Car-Heater/` checkout. Line
numbers may move in later changes.
