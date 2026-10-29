-- Verifies rehearsal.sql state after migrating to the candidate head.
-- Every invariant raises, so a failed check fails the rehearsal.

DO $$
BEGIN
    -- Baseline rows survive the announcement migrations unchanged.
    IF (SELECT count(*) FROM users WHERE username LIKE 'rehearsal-%') <> 2
       OR NOT EXISTS (SELECT 1 FROM users WHERE username = 'rehearsal-root'
                      AND is_admin AND is_root_admin) THEN
        RAISE EXCEPTION 'rehearsal users lost or changed';
    END IF;
    IF (SELECT count(*) FROM esp32_temphum WHERE location = 'rehearsal-room') <> 2
       OR (SELECT round(sum(temperature)::numeric, 1) FROM esp32_temphum
           WHERE location = 'rehearsal-room') <> 43.2 THEN
        RAISE EXCEPTION 'rehearsal sensor rows lost or changed';
    END IF;
    IF (SELECT count(*) FROM car_heater_installation WHERE saved_by = 'rehearsal-root') <> 1 THEN
        RAISE EXCEPTION 'rehearsal heater installation lost';
    END IF;
    IF (SELECT count(*) FROM thermostat_conf WHERE target_temp = 21.0 AND min_on_s = 240) <> 1 THEN
        RAISE EXCEPTION 'rehearsal thermostat configuration lost or changed';
    END IF;
    IF (SELECT count(*) FROM medicine_purchases WHERE medicine_key = 'rehearsal-tablet'
        AND pieces_bought = 30) <> 1 THEN
        RAISE EXCEPTION 'rehearsal medicine purchase lost or changed';
    END IF;

    -- The new announcement tables start empty.
    IF (SELECT count(*) FROM announcement_schedule) + (SELECT count(*) FROM announcement_occurrence)
       + (SELECT count(*) FROM announcement_attempt) + (SELECT count(*) FROM announcement_speaker_test)
       + (SELECT count(*) FROM announcement_spotify) <> 0 THEN
        RAISE EXCEPTION 'announcement tables are not empty after migration';
    END IF;

    -- One reminder round trip through the related tables, then removed again so
    -- both rehearsal copies end in the same state.
    INSERT INTO announcement_schedule (id, kind, status, revision, text, language, target_key,
        volume_percent, chime_percent, urgent, quiet_hours_override, expiry_minutes,
        max_announcements, due_at, created_at, updated_at)
    VALUES ('rehearsal-schedule', 'one_time', 'active', 1, 'Synthetic', 'fi', 'rehearsal',
        15, 40, false, false, 15, 1, '2026-10-29T12:00:00+00:00',
        '2026-10-29T11:00:00+00:00', '2026-10-29T11:00:00+00:00');
    INSERT INTO announcement_occurrence (id, schedule_id, instance_due_at, schedule_revision,
        created_at, text, language, target_key, target_name, speaker_set, volume_percent,
        chime_percent, urgent, max_announcements, due_at, expires_at, status, waiting_history,
        announcements_started, delivery, updated_at)
    VALUES ('rehearsal-occurrence', 'rehearsal-schedule', '2026-10-29T12:00:00+00:00', 1,
        '2026-10-29T12:00:00+00:00', 'Synthetic', 'fi', 'rehearsal', 'Rehearsal', '["rehearsal"]',
        15, 40, false, 1, '2026-10-29T12:00:00+00:00', '2026-10-29T12:15:00+00:00', 'closed',
        '[]', 1, 'delivered', '2026-10-29T12:00:05+00:00');
    INSERT INTO announcement_attempt (id, occurrence_id, target_key, target_name, speaker_set,
        evidence_scope, priority, text, language, volume_percent, chime_percent, status,
        requested_at, dispatch_deadline, updated_at, purpose)
    VALUES ('rehearsal-attempt', 'rehearsal-occurrence', 'rehearsal', 'Rehearsal', '["rehearsal"]',
        'device', 0, 'Synthetic', 'fi', 15, 40, 'finished', '2026-10-29T12:00:00+00:00',
        '2026-10-29T12:15:00+00:00', '2026-10-29T12:00:05+00:00', 'announcement');
    INSERT INTO announcement_acknowledgement (id, occurrence_id, action, acknowledged_at,
        prior_status, prior_delivery, announcements_started)
    VALUES ('rehearsal-ack', 'rehearsal-occurrence', 'done', '2026-10-29T12:01:00+00:00',
        'closed', 'delivered', 1);
    IF (SELECT retries_started FROM announcement_occurrence WHERE id = 'rehearsal-occurrence') <> 0 THEN
        RAISE EXCEPTION 'occurrence retries_started default missing';
    END IF;
    DELETE FROM announcement_acknowledgement WHERE id = 'rehearsal-ack';
    DELETE FROM announcement_attempt WHERE id = 'rehearsal-attempt';
    DELETE FROM announcement_occurrence WHERE id = 'rehearsal-occurrence';
    DELETE FROM announcement_schedule WHERE id = 'rehearsal-schedule';
END $$;
