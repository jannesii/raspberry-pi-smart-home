# Keep reminder acknowledgements separate from health records

The first spoken reminders concern taking medicine and measuring blood
pressure, but an acknowledgement records only that the user handled a reminder
occurrence. Speaker playback and Discord delivery are separate delivery
evidence; neither acknowledges the reminder or creates a medicine-dose or
blood-pressure record. This boundary avoids interpreting notification delivery
as a completed health action or coupling reminders to the existing medicine
purchase/refill calculations.

The initial scope uses user-configured text, including the same reminder text
in Discord fallbacks. Dynamic messages are deferred; future content generation
must preserve the distinction between reminder acknowledgement, delivery
evidence, and health records.
