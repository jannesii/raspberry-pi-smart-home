# Preserve reminder delivery uncertainty across restarts

Reminder schedules, occurrences, acknowledgements, and delivery attempts must
survive server restarts. If a crash leaves an audio attempt's result uncertain,
record delivery as unknown, use the configured Discord fallback, and do not
automatically replay that uncertain attempt. This trades a possible missed
spoken announcement for avoiding an unintended duplicate medicine reminder;
delivery evidence must never imply that the user heard or acted on it.

Speaker delivery and Discord fallback outcomes remain distinct. Sending a
fallback does not acknowledge an occurrence, and expiry prevents stale speech
after recovery. Expired occurrences recovered after downtime may produce a
clearly labelled missed-reminder fallback instead.
