Favicon and app icons
======================

Source file
-----------
- Put your source PNG at `app/static/icon-source.png` (square, 512–1024 px),
  or pass a custom path to the generator script.

Generate assets
---------------
The former `tools/make_favicon.py` generator is outside this repository.
Use external image tooling to create the following assets from the source PNG:
- `app/static/favicon.ico` (sizes: 16,32,48,64,128,256)
- `app/static/favicon-16x16.png`
- `app/static/favicon-32x32.png`
- `app/static/apple-touch-icon.png` (180x180)

Template links
--------------
`app/templates/base.html` includes link tags for these assets.

Routing note
------------
The Flask app serves `/favicon.ico` directly from the static folder for user agents
that request it implicitly.
