# Desktop development commissioning

## Verified on 17 November 2026

- Ran setup, start, status, stop and a confirmed reset from outside the
  selected checkout. Installed the selected checkout's real dependencies in
  the development state directory. The reset restored the dedicated
  `dev-tester` login and synthetic sample data.
- Provisioned native PostgreSQL and the dedicated development database and
  role on the desktop. PostgreSQL and the launcher's private Redis listened
  on loopback. The desktop firewall permits TCP 5557 to `192.168.1.100`
  from `192.168.1.0/24`. Ordinary commissioning used the local configuration
  with announcement acceptance off.
- Desktop Chromium trusted the local CA at
  `https://192.168.1.100:5557` and signed in as `dev-tester`. Verified the
  development banner, an authenticated Root-Admin page, and the secure,
  HttpOnly session and remember cookies. Chromium completed a secure
  WebSocket handshake. Socket.IO accepted the LAN HTTPS origin and rejected
  the former tunnel and production origins.
- The car-heater capture page displayed a live preview with Chromium's
  synthetic camera at the desktop LAN address. Chromium returned
  `NotFoundError` when tested with real desktop camera input.

## Cloudflare Tunnel follow-up on 17 November 2026

- The Pi's development ingress now routes `https://dev.jannenkoti.com` to
  `https://192.168.1.100:5557`. The Pi verified the desktop certificate with
  the public development CA.
- Verified public HTTPS login, the development banner, anonymous redirect,
  authenticated settings, and the secure WebSocket connection. Development
  Socket.IO rejected the production origin. The production login page
  responded successfully after the shared tunnel restarted.

## Phone commissioning

The owner confirmed that the phone camera worked at
`https://dev.jannenkoti.com`. They signed in, manually created a heating
session, and captured and saved a picture to that session. This completes
the phone commissioning check through the public HTTPS hostname.
