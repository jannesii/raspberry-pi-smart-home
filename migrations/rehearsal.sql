-- Synthetic release-rehearsal state compatible with the production baseline.
-- Trusted candidate code, never production data. Rehearsal seeds this into an
-- empty disposable database, dumps/restores it, migrates both copies to the
-- candidate head and runs rehearsal-verify.sql against each.
-- The additive announcement and heater-notification migrations use these rows to check
-- that existing data and identity sequences survive the upgrade untouched.

INSERT INTO users (username, password_hash, is_admin, is_root_admin)
VALUES ('rehearsal-root', 'synthetic-not-a-hash', true, true),
       ('rehearsal-guest', 'synthetic-not-a-hash', false, false);

INSERT INTO esp32_temphum (location, timestamp, temperature, humidity, ac_on)
VALUES ('rehearsal-room', '2026-10-29T12:00:00+00:00', 21.5, 40.0, false),
       ('rehearsal-room', '2026-10-29T12:05:00+00:00', 21.7, 41.0, true);

INSERT INTO car_heater_installation (saved_at, saved_by, heater, power_topology)
VALUES ('2026-10-29T12:00:00+00:00', 'rehearsal-root', 'synthetic', 'synthetic');

INSERT INTO thermostat_conf (sleep_active, target_temp, pos_hysteresis, neg_hysteresis)
VALUES (false, 21.0, 0.3, 0.3);

INSERT INTO medicine_purchases (
    medicine_name, medicine_key, purchase_date, pieces_bought,
    dose_per_dosing_day, dosing_weekdays_json, created_at, updated_at
)
VALUES ('Rehearsal tablet', 'rehearsal-tablet', '2026-10-29', 30, 1, '[1,2,3,4,5,6,7]',
        '2026-10-29T12:00:00+00:00', '2026-10-29T12:00:00+00:00');
