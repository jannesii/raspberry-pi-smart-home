;===================== date: 20240606 =====================
{if !spiral_mode && print_sequence != "by object"}
  ; don't support timelapse gcode in spiral_mode and by object sequence for I3 structure printer

  ; SKIPPABLE_START
  ; SKIPTYPE: timelapse

  M622.1 S1                        ; legacy enable (as in original)
  M1002 judge_flag timelapse_record_flag
  M622 J1                          ; begin judge block

  G90
  M83

  {if layer_z <= (initial_layer_print_height + 0.001)}
    M204 S[default_acceleration]

    G92 E0
    G17
    G2 Z{layer_z + 0.4} I0.86 J0.86 P1 F20000 ; spiral lift a little
    G1 Z{max_layer_z + 0.4}

    G1 X-48 Y128 F42000 ; click the button
    G1 X-47 F18000
    M400 P1000
    G1 X-28 F18000 ; Clean up the nozzle
    G1 X-47 F18000
    G1 X-28 F18000
    G1 X-47 F18000
    ;G1 X-28 F18000
    ;G1 X-47 F18000

    ;G1 X128 F42000 ; move print head back to the middle

    M204 S[initial_layer_acceleration]
  {else}
    G92 E0
    G17
    G2 Z{layer_z + 0.4} I0.86 J0.86 P1 F20000 ; spiral lift a little
    G1 Z{max_layer_z + 0.4}

    G1 X-48 Y128 F42000 ; click the button
    G1 X-47 F18000
    M400 P1000
    G1 X-28 F18000 ; Clean up the nozzle
    G1 X-47 F18000
    G1 X-28 F18000
    G1 X-47 F18000
    ;G1 X-28 F18000
    ;G1 X-47 F18000

    ;G1 X128 F42000 ; move print head back to the middle
  {endif}
  M623
  ; SKIPPABLE_END
{endif}
