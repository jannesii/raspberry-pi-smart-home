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

## Owner checks still open

1. Transfer the public root `~/.config/jannenkoti-dev/jannenkoti-dev-ca.cer`
   to the phone and install it. On iPhone, enable full trust according to
   [Apple's certificate trust instructions](https://support.apple.com/en-us/102390).
2. On the home Wi-Fi, open `https://192.168.1.100:5557` and confirm it loads
   without a certificate warning. Sign in as `dev-tester` using the
   development password in the protected desktop configuration and confirm
   the **Kehitysympäristö** banner.
3. Open an observation in **Car heater → Sessions**, allow camera access
   and verify the live preview with the phone camera. Record the result
   here. Actual desktop camera capture can be repeated with a webcam.
