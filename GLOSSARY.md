# Server Domain Glossary

Shared language for the services managed by this application.

## Language

### Spoken announcements

**Announcement**:
A spoken message directed to one or more selected speakers, preceded by an
attention chime.
_Avoid_: Notification (when specifically referring to spoken output)

**Attention chime**:
A soft sound played before an announcement to draw the listener's attention.
_Avoid_: Ping

**Announcement schedule**:
A rule specifying when an announcement is due, once or repeatedly.

**Announcement target**:
The speaker or set of speakers selected to play an announcement.

**Reminder occurrence**:
One scheduled instance of a reminder, including any repeats or snoozed
announcements associated with that instance.

**Reminder acknowledgement**:
The user's explicit indication that a reminder occurrence is done, distinct
from evidence that its announcement played.

**Snooze**:
The user's postponement of a reminder occurrence without marking it done.

**Superseded occurrence**:
A reminder occurrence whose pending announcements have been replaced by the
next occurrence of the same schedule, without marking the older one done.

**Occurrence expiry**:
The deadline after which a reminder occurrence must no longer produce spoken
announcements.

**Quiet hours**:
A recurring local-time interval during which spoken announcements are held
unless their schedule explicitly permits an override.

**Fallback notification**:
A reminder message sent through an alternative channel when spoken delivery
cannot satisfy the reminder's delivery policy.

### Car heater

**Departure time**:
The time the driver intends to leave in the car.

**Defrost readiness**:
The driver's viewing area is clear enough of exterior frost or ice to drive,
with wipers allowed and without scraping.
_Avoid_: Cabin-temperature readiness

**Heating session**:
One heating attempt associated with a trigger and, when applicable, a departure
time.

**Manual heating**:
Heating started directly by the driver for a selected duration.

**Comfort heating**:
Heating intended to maintain a selected cabin-air temperature.

**Heating limit**:
The maximum continuous heater-on duration permitted for a heating session,
including any heating after departure time.

**Departure grace period**:
Additional heating time allowed after the intended departure time if the driver
has not yet left, within the heating limit.

**Departure outcome**:
The driver's observation of window condition at departure, including whether
wipers or scraping were needed.

**Observation window**:
A period during a heating session when the driver records windshield condition
repeatedly to compare visible clearing with measured temperatures.

**Weather evidence**:
Preserved weather observations and forecasts describing the conditions around
heating attempts, including overnight conditions and periods without heating.

**Forecast version**:
A particular issue or revision of a forecast, distinct from the times its
predictions describe.

**Backfilled weather evidence**:
Weather observations retrieved after the intended collection time, distinguished
from evidence available when an earlier heating decision was made.
