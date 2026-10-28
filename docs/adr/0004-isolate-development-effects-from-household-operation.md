# Isolate development effects from household operation

The household application starts integrations and background work, so a second
checkout and database alone do not provide a safe development environment.
Everyday development must default to isolated data and simulated or disabled
external effects, including device control, notifications, automations and host
configuration changes. Acceptance testing with real devices or accounts requires
explicitly enabling the selected integrations; it is separate from ordinary
development testing.

The initial workflow serves one owner and prioritizes a small operational scope.
Production releases are manually initiated for a specific tested revision, and
brief production downtime is acceptable. Automatic deployment and zero-downtime
release infrastructure are outside the initial scope.

One on-demand development instance runs on the existing Pi, using one selected
feature worktree at a time. It has separate dependencies, configuration,
PostgreSQL credentials and database, Redis instance and writable files. Sharing
the host accepts resource contention in exchange for avoiding another machine
or hosting service; household and host-management effects remain isolated.

Development uses a dedicated test login and minimal synthetic data with an
explicit reset, adding fixtures only when needed rather than cloning production
data. Disposable PostgreSQL is required for migration validation. A stable,
clearly marked development address must support home-network browser access
from both a computer and a phone, and explicitly enabled speaker acceptance
testing.

Development browser access uses `dev.jannenkoti.com` through the existing
Cloudflare Tunnel, routing to `http://localhost:5557`, with browser HTTPS and
separate development login credentials.
Internet reachability is accepted; protected data and actions must require
authentication. Explicitly enabled speaker acceptance uses a separate LAN media
endpoint, exposing only the intended short-lived audio capabilities rather than
the browser management interface.

Production runs an exact committed and tested revision from a dedicated release
location with its own dependencies, separate from working checkouts. Retain the
previous release for recovery. The owner explicitly initiates deployment after
development acceptance, including real-device checks where required.

Before applying production migrations, create a database backup and require a
successful migration rehearsal on disposable PostgreSQL. Automatic code recovery
is permitted only when the previous release is compatible with the database.
Restoring a database backup requires a separate explicit owner decision because
it can discard subsequent writes; failed migrations or uncertain database state
must produce a clear failure report and recovery instructions.

Release gates cover relevant automated checks (the full suite for shared
behavior changes), owner acceptance, configuration and dependency readiness,
and a bounded post-start check of the running revision and basic application
and database access. Verification evidence must identify the exact candidate;
lengthy tests need not be repeated during production downtime when that evidence
remains applicable. New CI infrastructure and deployment dashboards are outside
the initial scope.
