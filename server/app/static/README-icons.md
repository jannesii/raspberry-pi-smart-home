Favicon and app icons
======================

Source file
-----------
- Put your source PNG at `server/app/static/icon-source.png` (square, 512–1024 px),
  or pass a custom path to the generator script.

Generate assets
---------------
- Requires Python Pillow: `pip install pillow`
- Run: `python tools/make_favicon.py` (or `python tools/make_favicon.py path/to/source.png`)

This writes:
- `server/app/static/favicon.ico` (sizes: 16,32,48,64,128,256)
- `server/app/static/favicon-16x16.png`
- `server/app/static/favicon-32x32.png`
- `server/app/static/apple-touch-icon.png` (180x180)

Template links
--------------
`server/app/templates/base.html` includes link tags for these assets.

Routing note
------------
The Flask app serves `/favicon.ico` directly from the static folder for user agents
that request it implicitly.
