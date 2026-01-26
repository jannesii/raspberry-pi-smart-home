G91
G0 Z5 F600 ; mniejsze uniesienie głowicy przed czyszczeniem
G90
M106 P2 S0
M106 P1 S0
M140 S[bed_temperature]
M104 S[nozzle_temperature]
M104 S170
G28 ; homing X, Y, Z
M190 S[bed_temperature]
M109 S[nozzle_temperature]
G92 E0
G1 Y-2 F1000 ; mniejsze przesunięcie w osi Y
G1 Z0.2 F300
G1 X120 E14 F360
G1 X160 F500
M1002 gcode_claim_action : 0
G92 E0
G5
