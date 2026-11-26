# Development environment

Browse one selected desktop checkout or worktree at
`https://dev.jannenkoti.com` ([ADR 0005](../adr/0005-operate-pi-production-from-desktop-development.md)).
The desktop runs native PostgreSQL, an on-demand private Redis and one
Eventlet Gunicorn worker. Dependencies, writable files and synthetic data
belong to development. The launcher and its local readiness checks work with
the Pi offline. Public browser access requires the Pi and its tunnel.

## Desktop setup

Install `uv`, Git, native PostgreSQL, `redis-server` and a tool for issuing a
local TLS certificate. The commands below use fish and Python 3.13 for new
desktop environments. Set `repo` to the absolute path of this checkout if it
differs.

```fish
set repo /home/jannesi/Nextcloud/Personal/Coding/raspberry-pi-smart-home
if not test -e "$repo/.venv"
    uv venv --python 3.13 --seed "$repo/.venv"
end
uv pip install --python "$repo/.venv/bin/python" -r "$repo/requirements-dev.txt"
install -d -m 700 ~/.config/jannenkoti-dev ~/.local/state/jannenkoti-dev
```

Rerunning these commands reuses an existing `.venv`. If its Python fails to
import `encodings` or you need to change Python versions, move `.venv` aside
before rerunning setup. Creating a virtual environment over an existing one
with another Python can leave its executable and standard library out of sync.

1. **PostgreSQL.** Initialize the desktop cluster if needed. On the Arch desktop,
   the first command below applies to an empty `/var/lib/postgres/data` only.
   Keep PostgreSQL listening on loopback. Run the provisioning SQL once and use
   the password prompt to set a fresh password for `jannenkoti_dev`.

   ```fish
   sudo -u postgres initdb -D /var/lib/postgres/data --auth-local=peer --auth-host=scram-sha-256
   sudo systemctl start postgresql
   sudo -u postgres psql -X -d postgres -f - < "$repo/docs/operations/development-host/postgresql.sql"
   sudo -u postgres psql -X -d postgres -c '\password jannenkoti_dev'
   ```

2. **TLS.** Use a local CA and a server certificate with `192.168.1.100` in its
   IP subject alternative name. For example, with `mkcert` installed, use a CA
   dedicated to development. The commissioned desktop already has its own CA
   and server certificate in `~/.config/jannenkoti-dev`.

   ```fish
   set -l certdir "$HOME/.config/jannenkoti-dev"
   env CAROOT="$certdir/mkcert" mkcert -cert-file "$certdir/server.pem" -key-file "$certdir/server.key" 192.168.1.100
   chmod 600 "$certdir/server.key"
   ```

   For this example use `$certdir/mkcert/rootCA.pem` as the CA file. The Pi
   needs only this public certificate. Keep the CA private key and server key
   on the desktop. Browsers use Cloudflare's public TLS certificate.

3. **Configuration.** Copy the example once and set its absolute paths, dedicated
   database password, new session secret and dedicated development login
   password. Use a URL-safe database password or percent-encode it in
   `DATABASE_URL`. Use the local CA public certificate for `DEV_TLS_CA`.

   ```fish
   install -m 600 "$repo/docs/operations/development-host/dev.env.example" ~/.config/jannenkoti-dev/dev.env
   ```

4. **Tunnel ingress.** The Pi must be able to reach TCP 5557 on
   `192.168.1.100`. Keep PostgreSQL and Redis on loopback. Install the public
   development CA at `/etc/cloudflared/jannenkoti-dev-ca.pem` on the Pi and set
   the existing development hostname rule in the Cloudflared configuration:

   ```yaml
   - hostname: dev.jannenkoti.com
     service: https://192.168.1.100:5557
     originRequest:
       caPool: /etc/cloudflared/jannenkoti-dev-ca.pem
   ```

   Validate ingress before restarting the shared tunnel, then check both the
   development and production login pages. The development app must be running
   to serve the public hostname. Use a dedicated development password.

## After moving the checkout

Move any copied `.venv` aside and run the setup commands above to create it
at the new path.
If you use the Git hook, [install it again](../../readme.md#development) from
the relocated checkout so it uses the new interpreter. Update any saved
`dev` function, stop the instance if it is running, and run
`dev setup --worktree` with the new absolute path. Setup keeps the existing
development database, TLS files and state directory.

## Daily use

Run the launcher from any directory. A fish function can wrap the absolute
launcher path. Use the same launcher to select a different checkout or worktree
after stopping the running instance.

```fish
function dev
    set -l launcher /home/jannesi/Nextcloud/Personal/Coding/raspberry-pi-smart-home
    "$launcher/.venv/bin/python" "$launcher/scripts/dev.py" $argv
end

dev setup --worktree /home/jannesi/Nextcloud/Personal/Coding/raspberry-pi-smart-home
dev start
dev status
dev stop
dev reset --yes
```

- **setup** selects a Git checkout or worktree, installs its requirements into
  `$DEV_STATE_DIR/venv`, applies migrations and seeds the sample data. Setup
  retains existing development data and reuses dependencies while requirements
  are unchanged. It binds the database to that state directory, which must be
  outside the selected checkout. Use a separate development database for a
  different state directory.
- **start** runs the selected checkout, including uncommitted edits, and waits
  for a certificate-verified HTTPS response. It starts its own loopback Redis
  with persistence off. Stop and start to load Python edits. Run setup again
  after changing requirements or migrations. Logs are in
  `$DEV_STATE_DIR/instance/` as `app.log`, `redis.log` and `setup.log`.
- **status** reports the selection, revision, local listener health and browser
  address. Check the public URL separately to verify the tunnel. The browser
  banner shows the revision captured at startup.
- **stop** signals only the app and Redis processes recorded by the launcher.
- **reset --yes** requires a stopped instance and a marked local database
  owned by an unprivileged development role. It recreates the development
  `public` schema for the registered state directory and deletes its writable files while keeping
  `venv/`, `instance/` and the development state marker. It then migrates
  and reseeds. Redis starts empty.

All commands default to `~/.config/jannenkoti-dev/dev.env`. Use `--env-file`
for a different development configuration. An explicitly supplied
`--production-env` adds overlap checks using a local reference file. Desktop
operation needs only the development file. Keep both configuration and TLS
files outside the checkout and the reset directory. `run --env-file FILE`
is the foreground app-only entry point used for isolated checks with
pre-existing dependencies and Redis. Use `start` for everyday development.

| Exit | Meaning |
| --- | --- |
| `0` | Done. For status, the development app and Redis answer. |
| `1` | Operation failed or the running instance is unhealthy. |
| `2` | The command refused the configuration or target. |
| `3` | Status found no running instance. |
| `4` | A lifecycle operation holds the lock. |

## Browser access and sample data

Sign in at `https://dev.jannenkoti.com` with the dedicated development login.
Setup and reset seed that login as Root-Admin and three synthetic sensor
locations named `Kehitys …`. The sticky **Kehitysympäristö** banner shows the
branch and commit, and tab titles start with `[DEV]`.

The distinct development session and remember cookies use `Secure` and
`HttpOnly`. Root-Admin, authentication, CSRF and API-key checks still apply.
Application rate limits are disabled in development. The tunnel presents the
Pi's address to the app, so its visitors would otherwise share one login limit.
Socket.IO accepts the configured development HTTPS origin. The authenticated
car-heater capture page allows same-origin camera access over HTTPS. The
browser still controls camera permission.

| Setting | Requirement |
| --- | --- |
| `DATABASE_URL` | A local `postgresql+psycopg` URL using a loopback IP or absolute Unix socket directory. The database must have the development marker and a dedicated unprivileged owner. |
| `REDIS_URL` | A free loopback port for the private Redis, such as `redis://127.0.0.1:6380/0`. |
| `RATE_LIMIT_STORAGE_URI` | Optional. Defaults to the private `REDIS_URL`. `memory://` is also supported. |
| `DEV_STATE_DIR` | An existing absolute directory, empty on first setup. |
| `DB_PATH`, `VILLENKOTI_DB_PATH` | Absolute file paths under `DEV_STATE_DIR`. KFactor test-mode output also goes there. |
| `SECRET_KEY`, `WEB_USERNAME`, `WEB_PASSWORD` | Dedicated development values. |
| `DEV_BIND_HOST`, `PORT` | Defaults are `192.168.1.100` and `5557`. Use an assigned private IPv4 address with a matching certificate. |
| `DEV_BROWSER_ORIGIN` | Set to `https://dev.jannenkoti.com` for the tunnel. When omitted, browsers use the local HTTPS listener origin. |
| `DEV_TLS_CERT`, `DEV_TLS_KEY`, `DEV_TLS_CA` | Absolute PEM paths for the server certificate, private key and public CA certificate. Use mode `600` for the key. |
| `USE_SQLA_READS` | Forced to `1` by setup, reset and start. |

## Effects in development

Credential-like values in the env file are not consent to use them. Startup
logs `<integration> disabled in development` for each one it keeps off:

- **Household control and automations:** AC thermostat and the Hue routine are
  not started. AC status reports `503`.
- **Notifications:** the alert webhook, alert batcher, Sodexo posts and medicine
  reminders are off. Sending or recording an alert returns not delivered; it is
  never reported as successful.
- **Scheduled external work and finance:** the weather observation and forecast
  archive is off. YNAB is off and its API reports `503`.
- **Host configuration:** network-bypass (NoVPN) changes are refused. The API
  returns `409 unavailable_in_development` and the settings page shows an
  error. The shared `devices.conf` is never rewritten and `novpn-master` is
  never restarted. Reading the device list still works.
- **Car heater:** commands and automations publish only to the development
  Redis. No gateway runs there, so commands stay queued and no device acts.
  On-demand weather forecast reads still reach FMI.

Browser Socket.IO, the ESP32 telemetry bridge and heater command publishing use
`REDIS_URL`.

## Verification

The [lifecycle tests](../../tests/test_development_lifecycle.py) run the
operator commands from outside the selected checkout against disposable
PostgreSQL and Redis. The Chromium flow uses a disposable trusted CA and
checks login, secure cookies, the development indicator, Socket.IO origins
and the car-heater capture preview with a synthetic camera. See the
[desktop commissioning record](development-host/commissioning.md) for tunnel
verification and outstanding device checks.

## Selected announcement acceptance

Ordinary development forces the announcement candidate's production builder off,
even when `ANNOUNCEMENTS_ENABLED=true` or real credentials appear in the development
file. It never inherits the calling shell's integration credentials. Speaker
acceptance uses a separate restricted adapter; it requires the announcement
candidate code in the selected worktree. The original feature branches can stay
separate: create a temporary combined worktree and merge both feature revisions
there, preserving the original checkouts and their pending commissioning notes.
Resolve the service-registration merge by retaining both the production
announcement registration and the development acceptance initialization. The
production registration remains disabled in development by the configuration
loader. No announcement implementation is copied into the environment feature.

Only enable acceptance after the owner explicitly authorizes the named real
speakers/channel. Stop development before editing the protected development
configuration. For example (replace the illustrative LAN addresses):

```dotenv
DEV_ACCEPTANCE='{"speakers":[{"key":"acceptance-right","name":"Acceptance right","host":"192.168.1.200","port":8009}]}'
DEV_ACCEPTANCE_MEDIA_ORIGIN=http://192.168.1.100:5558
```

Start development normally. `scripts/dev.py status` and the development banner
show configured speaker addresses and the separately selected Discord channel
label. The running banner reflects the process's startup configuration; changes
to the file require stop/start. Status reads the configuration file, so confirm
the running banner after a restart. No credential, webhook URL or audio capability
appears in either summary. The media listener binds only the specified LAN IP and
separate port. It exposes only `/announcement-media/<capability>.wav`, sharing
the candidate's existing capability store; capabilities expire after at most two
minutes and are revoked when the candidate ends the attempt. Invalid/expired
capabilities and all management paths return 404. It does not use browser cookies
and suppresses URL access logging because capabilities are bearer credentials.
Keep its port off router forwarding rules.

Browser login stays at `https://dev.jannenkoti.com`. A successful browser preview
or HTTPS request does not establish that a speaker can fetch LAN audio, play it,
use an appropriate volume, or restore music. Those remain explicitly authorized
owner checks in the announcement feature's commissioning guide.

Discord fallback is disabled even if `ALERT_WEBHOOK_URL` is present. To explicitly
select a channel, add `"discord":{"label":"Acceptance channel","webhook":"https://discord.com/api/webhooks/ID/TOKEN"}`
to `DEV_ACCEPTANCE` alongside selected speakers. Store this only in the protected
environment file; the label must identify the intended channel and contain no
secret. The webhook is scoped to that channel by Discord. This does not enable
the ordinary alert batcher, heater control, automations, host operations or any
other notification destination.

Spotify acceptance and synchronized Cast groups are refused in this initial
adapter: account scoping and group membership need their own explicit boundaries.
Inherited Spotify credentials cannot enable account access or restoration. Do
not interpret speaker-only acceptance as Spotify/group feature acceptance.

Cloud speech also needs explicit selection. Add `"speech":"azure"` alongside
the selected speakers in `DEV_ACCEPTANCE`, then add `AZURE_SPEECH_KEY`,
`AZURE_SPEECH_REGION` and optionally `AZURE_SPEECH_VOICE_FI` to the protected
development environment. Use a free F0 Speech resource; default Finnish voice
is Noora, with Selma available. The acceptance summary includes `Azure Speech
(cloud)`. Without that selection, development uses local Piper even if Azure
credentials or `ANNOUNCEMENT_SPEECH_ENGINE=azure` are present. Cloud synthesis
sends preview/reminder text to Microsoft; reuse of cached audio makes no speech
request. This selection does not enable additional speakers, accounts or webhooks.

To return to safe development, stop the instance, remove `DEV_ACCEPTANCE` (or set
it to `{}`), then start again. Confirm `Acceptance: off (safe development)` in the
running UI. Stopping also closes the media listener and invalidates its in-memory
capabilities. Do not change a running instance's file and assume effects stopped.
