#!/usr/bin/env python3
"""Copy finished Polytrack laps from the public drop into this repo.

Players upload the run and post its URL. This keeps the good data.
Run it from the repo root. TOPIC is the drop name, matching the game.
"""

import datetime
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import zlib

MAX_JSON = 32_000_000
MAX_DOWNLOAD = 45_000_000
MAX_PAGE = 300_000
MAX_NOTICE = 2_000
MAX_NEW = 50
GAME = "polytrack-0.6.3"
SCHEMA = 1
TICK_LEN = 15
PAGE = re.compile(r"^https://tmpfiles\.org/([A-Za-z0-9]{4,40})/([A-Za-z0-9._-]{1,80})$")
DL = re.compile(
    r'href="(https://tmpfiles\.org/dl/[A-Za-z0-9._-]+/([A-Za-z0-9]{4,40})/([A-Za-z0-9._-]{1,80}))"'
)
NAMES = {"lap.json", "lap.json.gz", "lap.webm", "lap.mp4"}


class Temporary(Exception):
    pass


def clean_id(value):
    if not isinstance(value, str) or not value or len(value) > 300:
        return False
    return all(ord(ch) >= 32 for ch in value)


def is_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(value) != float("inf")


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def validate_run(run):
    if not isinstance(run, dict):
        return False
    if run.get("kind") != "run" or run.get("version") != SCHEMA or run.get("game") != GAME or run.get("determinism") != "ok":
        return False
    if not clean_id(run.get("id")):
        return False
    frames = run.get("frames")
    if not is_int(frames) or frames < 10 or frames > 5999999:
        return False
    if not is_num(run.get("seconds")) or abs(float(run["seconds"]) - frames * 0.001) > 0.001:
        return False
    rows = run.get("rows")
    if not isinstance(rows, list) or len(rows) < 10 or len(rows) > 20000:
        return False
    for row in rows:
        if not isinstance(row, dict) or not is_num(row.get("t")):
            return False
        action = row.get("action")
        if not is_int(action) or action < -1 or action > 8:
            return False
        nums = row.get("n")
        if not isinstance(nums, list) or len(nums) != 19 or any(not is_num(n) for n in nums):
            return False
        if "car" in row and not isinstance(row["car"], dict):
            return False
    ticks = run.get("ticks")
    if not isinstance(ticks, list) or len(ticks) < 10 or len(ticks) > 300000:
        return False
    for tick in ticks:
        if not isinstance(tick, list) or len(tick) != TICK_LEN or any(not is_num(n) for n in tick):
            return False
    track = run.get("track")
    if not isinstance(track, dict):
        return False
    save = track.get("save")
    parts = track.get("parts")
    if not isinstance(save, str) or not save or len(save) > 1_500_000:
        return False
    if not isinstance(parts, list) or len(parts) > 8000:
        return False
    return True


def sample_run():
    rows = []
    ticks = []
    i = 0
    while i < 10:
        nums = [0] * 19
        nums[0] = i
        rows.append({
            "t": i * 0.05,
            "dt": 0 if i == 0 else 0.05,
            "wall": i * 0.05,
            "action": 0,
            "n": nums,
            "velocity": {"x": 0, "y": 0, "z": 0},
            "acceleration": {"x": 0, "y": 0, "z": 0},
            "car": {"frames": 500},
        })
        ticks.append([i] + [0] * (TICK_LEN - 1))
        i += 1
    return {
        "kind": "run",
        "id": "pipeline-test|500|10|0|0|0|0",
        "game": GAME,
        "version": SCHEMA,
        "determinism": "ok",
        "track": {"name": "Pipeline test", "author": "", "save": "ABC", "parts": []},
        "mountain": {"offset": 0, "float_count": 0},
        "seconds": 0.5,
        "frames": 500,
        "ticks": ticks,
        "rows": rows,
    }


def slug_for(run_id):
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]


def gunzip_limit(data, limit):
    if not data.startswith(b"\x1f\x8b"):
        raise ValueError("gzip")
    dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = bytearray()
    view = memoryview(data)
    while len(view):
        piece = dec.decompress(view, max(0, limit - len(out) + 1))
        consumed = len(view) - len(dec.unconsumed_tail)
        view = view[consumed:]
        out += piece
        if len(out) > limit:
            raise ValueError("large")
        if dec.eof:
            break
        if consumed == 0 and not piece:
            raise ValueError("stall")
    if not dec.eof:
        out += dec.flush()
    if len(out) > limit or not dec.eof:
        raise ValueError("large")
    return bytes(out)


def extract_dl(page_url, html):
    match = PAGE.match(page_url)
    if not match:
        return None
    file_id, name = match.group(1), match.group(2)
    if name not in NAMES:
        return None
    for found in DL.finditer(html):
        if found.group(2) == file_id and found.group(3) == name:
            return found.group(1)
    return None


def parse_notice(message):
    if not isinstance(message, str) or not message or len(message) > MAX_NOTICE:
        return None
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("v") != 1 or not clean_id(data.get("id")):
        return None
    json_url = data.get("json")
    if not isinstance(json_url, str) or not PAGE.match(json_url):
        return None
    if not (json_url.endswith("/lap.json.gz") or json_url.endswith("/lap.json")):
        return None
    video = data.get("video")
    if video is not None:
        if not isinstance(video, str) or not PAGE.match(video):
            return None
        if not (video.endswith("/lap.webm") or video.endswith("/lap.mp4")):
            return None
    return {"id": data["id"], "json": json_url, "video": video}


class Stay(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not (newurl.startswith("https://tmpfiles.org/") or newurl.startswith("https://ntfy.sh/")):
            raise urllib.error.URLError("offsite")
        return urllib.request.HTTPRedirectHandler.redirect_request(self, req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(Stay)


def http_get(url, limit):
    if not (url.startswith("https://tmpfiles.org/") or url.startswith("https://ntfy.sh/")):
        return None
    req = urllib.request.Request(url, headers={"User-Agent": "schoolagy-lap-collector"})
    try:
        with OPENER.open(req, timeout=60) as res:
            chunks = []
            total = 0
            while True:
                piece = res.read(65536)
                if not piece:
                    break
                total += len(piece)
                if total > limit:
                    return b""
                chunks.append(piece)
            return b"".join(chunks)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return b""
        return None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def download_named(page_url, limit):
    if not PAGE.match(page_url):
        return b""
    html = http_get(page_url, MAX_PAGE)
    if html is None:
        return None
    if html == b"":
        return b""
    link = extract_dl(page_url, html.decode("utf-8", "replace"))
    if not link:
        return b""
    return http_get(link, limit)


def load_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def append_line(path, line):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def index_path():
    return os.path.join("laps", "index.jsonl")


def load_index():
    rows = []
    for line in load_lines(index_path()):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


# Campaign order. A lap on any other layout is not stored.
CAMPAIGN = [
    "Summer 1", "Summer 2", "Summer 3", "Summer 4", "Summer 5", "Summer 6", "Summer 7",
    "Winter 1", "Winter 2", "Winter 3", "Winter 4", "Winter 5",
    "Desert 1", "Desert 2", "Desert 3", "Desert 4", "Desert 5",
]
CAMPAIGN_ORDER = {name: index for index, name in enumerate(CAMPAIGN)}

# sha256 prefix of the sorted (x, y, z, type, rotation, axis) tuples.
# Taken from the Polytrack 0.6.3 official .track files.
OFFICIAL = {
    "8f94794c132dcd1b": "Summer 1",
    "eba5b2814a0b60e6": "Summer 2",
    "08d3a76150bf2493": "Summer 3",
    "598d2f153b1dfcb9": "Summer 4",
    "b451518d1b0dc8e0": "Summer 5",
    "11b898afd000c9f4": "Summer 6",
    "0830c1efcb61821e": "Summer 7",
    "bc39a89124e18f0b": "Winter 1",
    "5d3bdb8249d2fcbe": "Winter 2",
    "c431c56ee8fd7ded": "Winter 3",
    "aa851494e5d3d404": "Winter 4",
    "387b28e30c2a2566": "Winter 5",
    "d824a995ca386b1e": "Desert 1",
    "84a542770fc9271d": "Desert 2",
    "23f882dd20816a97": "Desert 3",
    "cf41a955af1ef0c1": "Desert 4",
    "9c53502b022070bd": "Desert 5",
}


def layout_sig(parts):
    if not isinstance(parts, list) or not parts:
        return None
    rows = []
    for part in parts:
        if not isinstance(part, dict):
            return None
        try:
            rows.append((
                int(part["x"]), int(part["y"]), int(part["z"]),
                int(part["type"]), int(part["rotation"]), int(part["axis"]),
            ))
        except (KeyError, TypeError, ValueError):
            return None
    rows.sort()
    return hashlib.sha256(repr(rows).encode()).hexdigest()[:16]


def official_name(parts):
    return OFFICIAL.get(layout_sig(parts) or "")


def track_times(name):
    times = []
    for row in load_index():
        if row.get("track") == name and is_num(row.get("seconds")):
            times.append(float(row["seconds"]))
    return times


def half_cut(times):
    # Slower edge of the faster half. Fewer than four laps is not a split yet.
    if len(times) < 4:
        return None
    ordered = sorted(times)
    return ordered[(len(ordered) - 1) // 2]


def fast_enough(seconds, times):
    # One second past the slower edge of the faster half.
    cut = half_cut(times)
    return cut is None or seconds <= cut + 1


def level_key(name):
    # "Summer 2" stays before "Summer 10" when the name is not a campaign level.
    parts = re.findall(r"\d+|\D+", name or "")
    key = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part.casefold()))
    return tuple(key)


def index_sort_key(row):
    seconds = row.get("seconds")
    if not is_num(seconds):
        seconds = float("inf")
    name = row.get("track") if isinstance(row.get("track"), str) else ""
    level = (0, CAMPAIGN_ORDER[name]) if name in CAMPAIGN_ORDER else (1, level_key(name))
    return (level, seconds, row.get("id") or "")


def write_index(rows):
    rows = sorted(rows, key=index_sort_key)
    os.makedirs("laps", exist_ok=True)
    text = "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows)
    with open(index_path(), "w", encoding="utf-8") as handle:
        handle.write(text)


def index_has(run_id):
    for row in load_index():
        if row.get("id") == run_id:
            return True
    return False


def append_index(run, slug, video_name, size):
    track = run.get("track") if isinstance(run.get("track"), dict) else {}
    name = official_name(track.get("parts")) or ""
    row = {
        "id": run["id"],
        "file": "laps/" + slug + ".json.gz",
        "video": video_name,
        "game": run.get("game"),
        "frames": run["frames"],
        "seconds": run["seconds"],
        "track": name,
        "bytes": size,
        "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    rows = [old for old in load_index() if old.get("id") != run["id"]]
    rows.append(row)
    write_index(rows)


def release_has(name):
    proc = subprocess.run(
        ["gh", "release", "view", "inbox", "--json", "assets"],
        capture_output=True, text=True
    )
    if proc.returncode != 0:
        return False
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return False
    for asset in data.get("assets") or []:
        if asset.get("name") == name:
            return True
    return False


def store_video_file(name, page_url, ext, slug):
    rel = "videos/" + name
    if os.path.isfile(rel) and os.path.getsize(rel) > 0:
        return rel
    data = download_named(page_url, MAX_DOWNLOAD)
    if data is None:
        raise Temporary()
    if not data:
        print("video missing", slug)
        return None
    if ext == "webm" and not data.startswith(b"\x1a\x45\xdf\xa3"):
        print("video skipped", slug)
        return None
    if ext == "mp4" and not (len(data) > 8 and data[4:8] == b"ftyp"):
        print("video skipped", slug)
        return None
    os.makedirs("videos", exist_ok=True)
    with open(rel, "wb") as handle:
        handle.write(data)
    return rel


def ensure_release():
    proc = subprocess.run(["gh", "release", "view", "inbox"], capture_output=True, text=True)
    if proc.returncode == 0:
        return True
    made = subprocess.run(
        ["gh", "release", "create", "inbox", "--title", "Lap videos", "--notes", "Canvas recordings for collected laps."],
        capture_output=True, text=True
    )
    if made.returncode != 0:
        print("release create failed")
        return False
    return True


def store_video(slug, page_url):
    ext = "mp4" if page_url.endswith(".mp4") else "webm"
    name = slug + "." + ext
    if os.environ.get("VIDEO_STORE") == "git":
        return store_video_file(name, page_url, ext, slug)
    if release_has(name):
        return name
    data = download_named(page_url, MAX_DOWNLOAD)
    if data is None:
        raise Temporary()
    if not data:
        print("video missing", slug)
        return None
    if ext == "webm" and not data.startswith(b"\x1a\x45\xdf\xa3"):
        print("video skipped", slug)
        return None
    if ext == "mp4" and not (len(data) > 8 and data[4:8] == b"ftyp"):
        print("video skipped", slug)
        return None
    if not ensure_release():
        raise Temporary()
    folder = os.path.join("inbox", "tmp")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "wb") as handle:
        handle.write(data)
    uploaded = subprocess.run(
        ["gh", "release", "upload", "inbox", path, "--clobber"],
        capture_output=True, text=True
    )
    os.remove(path)
    if uploaded.returncode != 0:
        print("video upload failed", slug)
        raise Temporary()
    return name


def ingest(notice):
    raw = download_named(notice["json"], MAX_DOWNLOAD)
    if raw is None:
        raise Temporary()
    if not raw:
        return "invalid", None
    try:
        if raw.startswith(b"\x1f\x8b") or notice["json"].endswith(".gz"):
            text = gunzip_limit(raw, MAX_JSON)
            stored = raw if raw.startswith(b"\x1f\x8b") else gzip.compress(text)
        else:
            if len(raw) > MAX_JSON:
                return "invalid", None
            text = raw.decode("utf-8")
            stored = gzip.compress(raw)
        run = json.loads(text)
    except (ValueError, UnicodeError, OSError, zlib.error, json.JSONDecodeError):
        return "invalid", None
    if not validate_run(run) or run["id"] != notice["id"]:
        return "invalid", None
    track_name = official_name((run.get("track") or {}).get("parts"))
    if not track_name:
        print("custom", run["id"][:80])
        return "custom", None
    if not fast_enough(float(run["seconds"]), track_times(track_name)):
        print("slow", run["id"][:80])
        return "slow", None
    slug = slug_for(run["id"])
    path = os.path.join("laps", slug + ".json.gz")
    new = not os.path.exists(path)
    if new:
        os.makedirs("laps", exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(stored)
    video_name = None
    if notice["video"]:
        video_name = store_video(slug, notice["video"])
    if new or not index_has(run["id"]):
        append_index(run, slug, video_name, len(stored))
    print(("stored " if new else "duplicate ") + slug)
    return "ok", slug


def fetch_messages(topic):
    url = "https://ntfy.sh/" + topic + "/json?poll=1&since=12h"
    raw = http_get(url, 2_000_000)
    if raw is None:
        raise Temporary()
    messages = []
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("event") == "message":
            messages.append(item)
    messages.sort(key=lambda item: item.get("time") or 0)
    return messages


def main():
    topic = os.environ.get("TOPIC", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", topic):
        raise SystemExit("TOPIC must be the drop name")
    seen_path = os.path.join("inbox", "seen.txt")
    seen = set(load_lines(seen_path))
    try:
        messages = fetch_messages(topic)
    except Temporary:
        print("drop unread")
        return 1
    handled = 0
    for item in messages:
        mid = item.get("id")
        if not isinstance(mid, str) or not mid or mid in seen:
            continue
        if handled >= MAX_NEW:
            break
        notice = parse_notice(item.get("message") or "")
        if notice is None:
            append_line(seen_path, mid)
            seen.add(mid)
            handled += 1
            continue
        try:
            status, _slug = ingest(notice)
        except Temporary:
            print("retry later", notice["id"][:80])
            break
        if status == "invalid":
            print("rejected", notice["id"][:80])
        append_line(seen_path, mid)
        seen.add(mid)
        handled += 1
    print("done", handled)
    return 0


def self_test():
    good = sample_run()
    assert validate_run(good)
    bad = dict(good)
    bad["determinism"] = "no"
    assert not validate_run(bad)
    short = dict(good)
    short["rows"] = good["rows"][:3]
    assert not validate_run(short)
    other = dict(good)
    other["game"] = "polytrack-0.5.2" if GAME == "polytrack-0.6.3" else "polytrack-0.6.3"
    assert not validate_run(other)
    wide = dict(good)
    wide["ticks"] = [tick + [0] for tick in good["ticks"]]
    assert not validate_run(wide)
    assert not parse_notice('{"v":1,"id":"x","json":"https://evil.example/a/lap.json.gz"}')
    notice = {
        "v": 1,
        "id": good["id"],
        "json": "https://tmpfiles.org/AbCd1234/lap.json.gz",
        "video": None,
    }
    parsed = parse_notice(json.dumps(notice))
    assert parsed and parsed["json"].endswith("/lap.json.gz")
    page = '<a href="https://tmpfiles.org/dl/179.abc/AbCd1234/lap.json.gz">Download</a>'
    assert extract_dl("https://tmpfiles.org/AbCd1234/lap.json.gz", page).endswith("/lap.json.gz")
    assert extract_dl("https://evil.example/AbCd1234/lap.json.gz", page) is None
    packed = gzip.compress(json.dumps(good).encode())
    assert json.loads(gunzip_limit(packed, 100000))["id"] == good["id"]
    try:
        gunzip_limit(gzip.compress(b"x" * 1000), 10)
    except ValueError:
        pass
    else:
        raise SystemExit("gzip limit failed")
    assert slug_for("pipeline-test|500|10|0|0|0|0") == hashlib.sha256(
        b"pipeline-test|500|10|0|0|0|0"
    ).hexdigest()[:16]
    assert level_key("Summer 2") < level_key("Summer 10")
    assert index_sort_key({"track": "Winter 5", "seconds": 19.0, "id": "a"}) > index_sort_key(
        {"track": "Summer 1", "seconds": 40.0, "id": "b"}
    )
    assert index_sort_key({"track": "Summer 1", "seconds": 20.0, "id": "a"}) < index_sort_key(
        {"track": "Summer 1", "seconds": 25.0, "id": "b"}
    )
    assert index_sort_key({"track": "Desert 1", "seconds": 1.0, "id": "a"}) > index_sort_key(
        {"track": "Winter 5", "seconds": 90.0, "id": "b"}
    )
    one = [{"x": 1, "y": 2, "z": 3, "type": 0, "rotation": 1, "axis": 0}]
    assert layout_sig(one) == hashlib.sha256(repr([(1, 2, 3, 0, 1, 0)]).encode()).hexdigest()[:16]
    assert official_name(one) is None
    assert official_name(good["track"]["parts"]) is None
    assert half_cut([1, 2, 3]) is None
    assert half_cut([1, 2, 3, 4]) == 2
    assert fast_enough(3, [1, 2, 3, 4])
    assert not fast_enough(3.1, [1, 2, 3, 4])
    assert fast_enough(9, [1, 2, 3])
    assert OFFICIAL["8f94794c132dcd1b"] == "Summer 1"
    assert OFFICIAL["387b28e30c2a2566"] == "Winter 5"
    print("self-test ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        self_test()
    else:
        sys.exit(main())
