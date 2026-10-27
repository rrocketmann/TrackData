# Polytrack 0.6.3 laps

Finished OK laps from Polytrack 0.6.3 on schoolagy. A lap is stored only when that browser's determinism check passed. Profile names are not included. Polytrack 0.5.2 laps go to a separate archive and are rejected here.

`laps/<id>.json.gz` is one lap, the same object the game puts in its zip. `laps/index.jsonl` lists them. Canvas video, when the browser recorded one, is a release asset on the `inbox` release.

```bash
gh repo clone rrocketmann/polytrack-laps-063
gh release download inbox -D videos
```

The game uploads each lap, then leaves a short note. This repo's hourly action copies new notes in. Notes that are not a finished OK lap are ignored. The same lap is not stored twice.

The drop name in `.github/workflows/collect.yml` has to match `INBOX_TOPIC` in `schoolagy/polytrack-0.6.3/dataset.js`. Players start sending after that file is on GitHub Pages.
