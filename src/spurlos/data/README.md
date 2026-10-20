`registry.json.gz` is a packed copy of [datenanfragen/data](https://github.com/datenanfragen/data)
(CC0 1.0): company privacy contacts, supervisory authorities and the German and English
request templates. Rebuild it from a checkout with

    spurlos registry build path/to/data --out src/spurlos/data/registry.json.gz --commit <sha>
