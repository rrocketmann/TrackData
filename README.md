# TrackData

TrackData is the Polytrack 0.6.3 laps from schoolagy. A run is stored only when that browser's determinism check passed. Profile names are not included. A Polytrack 0.5.2 lap is rejected here.

`laps/<id>.json.gz` is one lap, the same object the game puts in its zip. `laps/index.jsonl` lists them by campaign level (Summer 1, then Winter 5, and so on), fastest first within a level. A lap is stored only when its layout is an official campaign track. Lap time does not matter. The automatic collect writes a canvas video to `videos/<id>.webm` or `videos/<id>.mp4`. A collect started in this repo uploads that video to the `inbox` release instead.

```bash
gh repo clone rrocketmann/polytrack-laps-063
gh release download inbox -D videos
```

The game uploads each run, then leaves a short note. The schoolagy action copies new notes in about every ten minutes, and this repo checks once a day in case that action missed one. Notes that are not good data are ignored. The same run is not stored twice.

The drop name in `.github/workflows/collect.yml` has to match `INBOX_TOPIC` in `schoolagy/polytrack-0.6.3/dataset.js`. Players start sending after that file is on GitHub Pages.
