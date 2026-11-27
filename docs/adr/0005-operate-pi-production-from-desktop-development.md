# Operate Pi production from desktop development

The main development workspace has moved to the owner's desktop. Ordinary
development will also run the application, PostgreSQL and Redis on that desktop,
keeping production on the Pi and preserving the development isolation contract
from ADR 0004. This supersedes ADR 0004's placement of the development runtime on
the Pi. Reuse native PostgreSQL and Redis rather than introducing containers;
PostgreSQL runs locally as a service, while the development app and its private
Redis remain on demand. Browsers use `https://dev.jannenkoti.com` through
the Pi's existing Cloudflare Tunnel. The Pi connects to the desktop at
`https://192.168.1.100:5557` and validates the desktop's TLS certificate with
the public development CA. The Pi's fixed address is `192.168.1.50` and desktop
operations address it through `raspi`.

Production deployment remains an explicit owner action for an exact committed
revision. Desktop commands will use the `raspi` SSH alias and the existing Pi
release process, preserving its checks, acceptance, migration rehearsal,
pre-migration backup, readiness checks and compatible code recovery. Brief
downtime remains acceptable; automatic deployment, zero-downtime infrastructure
and a deployment dashboard remain outside the initial scope.
Transfer the exact committed candidate directly over SSH so deployment does not
depend on first pushing it to GitHub.
[ADR 0006](0006-run-the-release-test-suite-on-the-desktop.md) refines the
preserved checks: the full suite runs on the desktop rather than on the Pi.

Routine production database browsing and agent queries will use dedicated
read-only access through SSH, separate from the application's credentials.
Production writes are separate maintenance operations; development may permit
writes and explicit resets. Reuse the installed VS Code PostgreSQL extension if
suitable, and provide a small agent query command with bounded output and a
statement timeout rather than requiring a database MCP server.
Initially disable password saving in VS Code and prompt for the read-only
database password. The agent command may use a restricted local credential file
outside the repository.

The initial operations scope also includes production revision and health status,
recent application, gateway and deployment logs, verification of backup coverage
and a restore into a disposable database, plus current daily-use and recovery
documentation. A new monitoring stack and broader infrastructure automation are
outside this scope. Create daily production backups covering PostgreSQL and the
configuration, secrets and persistent files needed for recovery, retain 14 daily
copies and copy backups to the desktop when available. The recovery target is
approximately one day of database loss when a recent off-Pi copy exists, rather
than preservation of every write. Encrypt recovery backups on the Pi with a
public key; keep the private decryption key on the desktop, with the owner storing
a recovery copy in 1Password. Verify encryption, copying, decryption and isolated
restoration together.

Systemd timers create the Pi backup daily at 03:15 Europe/Helsinki and check for
new desktop copies hourly while the desktop is on, catching up after startup.
Operations status reports the latest Pi backup, latest desktop copy, failures
and backup or copy age above 36 hours. A separate notification service is outside
the initial scope.
