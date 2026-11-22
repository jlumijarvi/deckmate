#!/usr/bin/env python3
"""The project board's data: the issues, the commits, the days worked, the
releases, the key types and the checks, written as JSON files in
tools/board/data/ for the deckmate board (a Claude Artifact dashboard).
Run it from the repo's root: `python3 tools/board.py`. Then the files are
uploaded to the board.

Each file is {rows, meta}: the rows the board shows, and meta saying how
they were made, this script's own text included so readers can check it.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

REPO = "jlumijarvi/deckmate"
OUT = "tools/board/data"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def run(*cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=ROOT).stdout


made_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
with open(os.path.join(HERE, "board.py")) as f:
    SELF = f.read()


def meta(inp):
    return [{"label": "Made", "value": made_at}, {"label": "Input", "value": inp},
            {"label": "tools/board.py", "value": SELF, "type": "code", "lang": "python"}]


# --- issues --------------------------------------------------------------------------------
raw = json.loads(run("gh", "issue", "list", "--state", "all", "--limit", "500",
                     "--json", "number,title,state,createdAt,closedAt,url"))
issues = sorted(({
    "number": i["number"], "title": i["title"], "state": i["state"].lower(),
    "created": i["createdAt"][:10], "closed": i["closedAt"][:10] if i.get("closedAt") else None,
    "days_open": (datetime.fromisoformat((i.get("closedAt") or made_at).replace("Z", "+00:00"))
                  - datetime.fromisoformat(i["createdAt"].replace("Z", "+00:00"))).days,
    "url": i["url"],
} for i in raw), key=lambda i: i["number"])

# --- releases, from the changelog -----------------------------------------------------------
with open(os.path.join(ROOT, "CHANGELOG.md")) as f:
    changelog = f.read()
releases = []
for m in re.finditer(r"^## (\d+\.\d+\.\d+) \((\d{4}-\d{2}-\d{2})\)\n(.*?)(?=^## |\Z)", changelog, re.M | re.S):
    version, date, body = m.groups()
    bullets = [re.sub(r"\s+", " ", b).strip() for b in re.split(r"^- ", body, flags=re.M)[1:]]
    refs = sorted({int(n) for b in bullets for n in re.findall(r"#(\d+)", b)})
    first = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", bullets[0]).replace("`", "") if bullets else ""
    releases.append({
        "version": version, "date": date, "changes": len(bullets), "issues": " ".join(map(str, refs)),
        "highlight": re.split(r"(?<=\.)\s", first)[0][:200],
        "url": f"https://github.com/{REPO}/releases/tag/v{version}",
    })

# --- commits, and the days worked -----------------------------------------------------------
commits = []
for line in run("git", "log", "--date=short", "--pretty=format:%h\t%ad\t%s").splitlines():
    hash_, date, subject = line.split("\t", 2)
    refs = re.findall(r"#(\d+)", subject)
    commits.append({"hash": hash_, "date": date, "subject": subject, "issues": " ".join(refs)})
by_day = {}
for c in commits:                      # the log is newest first: the last seen is the day's first commit
    day = by_day.setdefault(c["date"], {"date": c["date"], "commits": 0, "issues": set(), "first": ""})
    day["commits"] += 1
    day["issues"].update(int(n) for n in c["issues"].split())
    day["first"] = c["subject"]
closed_on, released_on = {}, {}
for i in issues:
    if i["closed"]:
        closed_on[i["closed"]] = closed_on.get(i["closed"], 0) + 1
for r in releases:
    released_on[r["date"]] = released_on.get(r["date"], 0) + 1
days = sorted(({
    "date": d["date"], "commits": d["commits"], "issues_touched": len(d["issues"]),
    "issues_closed": closed_on.get(d["date"], 0), "releases": released_on.get(d["date"], 0),
    "first_commit": d["first"],
} for d in by_day.values()), key=lambda d: d["date"], reverse=True)

# --- key types, as the README states them -----------------------------------------------------
with open(os.path.join(ROOT, "README.md")) as f:
    readme = f.read()
table = readme[readme.index("| Type | Shows | Press |"):]
table = table[:table.index("\n\n")]
keys = []
for row in table.splitlines()[2:]:
    cells = [c.strip() for c in row.strip("|").split("|")]
    if len(cells) != 3:
        continue
    type_cell, shows, press = cells
    mt = re.match(r"^`([^`]+)`\s*(.*)$", type_cell)
    keys.append({
        "type": mt.group(1) if mt else "button",
        "fields": re.sub(r"[()`]", "", mt.group(2)).strip() if mt else "title, color, run",
        "shows": shows.replace("`", ""), "press": press.replace("`", ""),
    })

# --- checks: tests, coverage, size ------------------------------------------------------------
tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                       capture_output=True, text=True, cwd=ROOT)
ran = re.search(r"^Ran (\d+) tests", tests.stderr, re.M)
test_files = sorted(f for f in os.listdir(os.path.join(ROOT, "tests")) if f.startswith("test_") and f.endswith(".py"))
try:
    cov = json.loads(run("gh", "api", f"repos/{REPO}/contents/coverage.json?ref=badges", "-q", ".content | @base64d"))
    coverage = int(cov["message"].rstrip("%"))
except (subprocess.CalledProcessError, ValueError, KeyError):
    coverage = None
program = [f for f in os.listdir(ROOT) if f.endswith(".py")]
lines = lambda files, folder: sum(sum(1 for _ in open(os.path.join(folder, f))) for f in files)  # noqa: E731
with open(os.path.join(ROOT, "VERSION")) as f:
    version = f.read().strip()
checks = [
    {"metric": "version", "value": version, "detail": "VERSION, the installed package when up to date"},
    {"metric": "tests", "value": int(ran.group(1)) if ran else 0,
     "detail": f"{'pass' if tests.returncode == 0 else 'FAIL'}, {len(test_files)} test files, python3 -m unittest"},
    {"metric": "coverage", "value": coverage, "detail": "percent of lines the tests run, from the CI badge"},
    {"metric": "program lines", "value": lines(program, ROOT), "detail": f"{len(program)} Python modules"},
    {"metric": "test lines", "value": lines(test_files, os.path.join(ROOT, "tests")),
     "detail": f"{len(test_files)} files"},
]

# --- write ------------------------------------------------------------------------------------
os.makedirs(os.path.join(ROOT, OUT), exist_ok=True)


def write(name, rows, inp):
    with open(os.path.join(ROOT, OUT, name + ".json"), "w") as f:
        json.dump({"rows": rows, "meta": meta(inp)}, f)
    print(f"{name}: {len(rows)} rows")


write("issues", issues, "gh issue list --state all")
write("releases", releases, "CHANGELOG.md, one row per released section")
write("commits", commits, "git log on main")
write("days", days, "git log on main grouped by day; issues closed and releases made that day")
write("keys", keys, "README.md, the key types table")
write("checks", checks, "python3 -m unittest, the coverage badge on the badges branch, line counts")
