# Web assets

Sources live in `web/src` (HTML plus JS/CSS under `static/`).
Run `scripts/build_web.sh` to regenerate the gzipped files in `web/dist` that PlatformIO uploads to LittleFS (`data_dir` now points there).
Files are flattened into the filesystem root so paths like `/index.js` and `/styles.css` stay the same.
