"""A calendar from an iCalendar feed: Google Calendar's "secret address in iCal
format", or any .ics URL. A background thread fetches it every five minutes
and keeps a copy on disk, so the board shows today's meetings right after it
starts, even offline. Events are expanded for one day at a time; repeating
events (RRULE with the parts Google writes: FREQ, INTERVAL, COUNT, UNTIL,
BYDAY, BYMONTHDAY, BYMONTH), exceptions (EXDATE) and moved or edited
instances (RECURRENCE-ID) are handled here, with no library.

Nothing but the feed URL is sent anywhere, and nothing is written except the
cache file.

Run `python3 calfeed.py <url>` to print today's events from a feed.
"""
import hashlib
import logging
import os
import re
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from paths import CACHE_DIR

log = logging.getLogger("deck")

POLL_S = 5 * 60
RETRY_S = 60
MAX_STEPS = 20000          # periods a repeating event is followed before giving up (55 years of days)
WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
MEETING_HOSTS = ("meet.google.com", "zoom.us", "teams.microsoft.com", "teams.live.com", "webex.com", "whereby.com")


@dataclass
class Event:
    """One VEVENT. Times are aware datetimes; an all-day event starts at local midnight."""
    uid: str
    start: datetime
    end: datetime
    summary: str = ""
    location: str = ""
    url: str = ""
    all_day: bool = False
    rrule: dict | None = None
    exdates: set = field(default_factory=set)
    recurrence_id: datetime | None = None
    cancelled: bool = False

    @property
    def duration(self):
        return self.end - self.start


@dataclass
class Occurrence:
    """One instance of an event on a day."""
    start: datetime
    end: datetime
    summary: str = ""
    location: str = ""
    url: str = ""
    all_day: bool = False
    uid: str = ""

    def state(self, now, warn_s):
        """'past', 'now', 'soon' (starts within warn_s) or 'later'."""
        if self.end <= now:
            return "past"
        if self.start <= now:
            return "now"
        if (self.start - now).total_seconds() <= warn_s:
            return "soon"
        return "later"


def local_tz():
    return datetime.now().astimezone().tzinfo


# --- parsing -----------------------------------------------------------------

def unfold(text):
    """iCalendar folds long lines; a continuation starts with a space or tab."""
    out = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


_PARAM_RE = re.compile(r';([A-Za-z0-9-]+)=("[^"]*"|[^;:]*)')


def split_line(line):
    """'NAME;P=V;Q=W:value' -> (NAME, {P: V, Q: W}, value)."""
    i = 0
    name_end = None
    in_quotes = False
    while i < len(line):
        c = line[i]
        if c == '"':
            in_quotes = not in_quotes
        elif c == ":" and not in_quotes:
            name_end = i
            break
        i += 1
    if name_end is None:
        return line.upper(), {}, ""
    head, value = line[:name_end], line[name_end + 1:]
    name, _, params = head.partition(";")
    out = {}
    for m in _PARAM_RE.finditer(";" + params if params else ""):
        out[m.group(1).upper()] = m.group(2).strip('"')
    return name.upper(), out, value


def unescape(value):
    return (value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",")
            .replace("\\;", ";").replace("\\\\", "\\"))


def parse_dt(value, params, tz):
    """An aware datetime from a DATE or DATE-TIME value, plus whether it was a date.
    Z means UTC, TZID picks the zone (unknown zones fall back to the local one),
    a bare time is local."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or len(value) == 8:
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=tz), True
    utc = value.endswith("Z")
    naive = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    if utc:
        return naive.replace(tzinfo=timezone.utc), False
    tzid = params.get("TZID")
    zone = tz
    if tzid:
        try:
            zone = ZoneInfo(tzid)
        except (ZoneInfoNotFoundError, ValueError):
            zone = tz
    return naive.replace(tzinfo=zone), False


_DURATION_RE = re.compile(r"^([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def parse_duration(value):
    m = _DURATION_RE.match(value.strip())
    if not m:
        return timedelta()
    sign, w, d, h, mi, s = m.groups()
    td = timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0), minutes=int(mi or 0), seconds=int(s or 0))
    return -td if sign == "-" else td


def parse_rrule(value, tz):
    rule = {}
    for part in value.split(";"):
        k, _, v = part.partition("=")
        k = k.upper()
        if k in ("INTERVAL", "COUNT"):
            rule[k] = int(v)
        elif k == "UNTIL":
            until, is_date = parse_dt(v, {}, tz)
            rule[k] = until + timedelta(days=1) - timedelta(seconds=1) if is_date else until
        elif k == "BYDAY":
            days = []
            for item in v.split(","):
                m = re.match(r"^([+-]?\d+)?([A-Z]{2})$", item.strip().upper())
                if m and m.group(2) in WEEKDAYS:
                    days.append((int(m.group(1)) if m.group(1) else None, WEEKDAYS.index(m.group(2))))
            rule[k] = days
        elif k in ("BYMONTHDAY", "BYMONTH"):
            rule[k] = [int(x) for x in v.split(",") if x.strip()]
        elif k == "FREQ":
            rule[k] = v.upper()
        elif k == "WKST":
            rule[k] = WEEKDAYS.index(v.upper()) if v.upper() in WEEKDAYS else 0
    return rule


def meeting_link(props):
    """The conference link: Google's own property, then a known meeting host in
    the location or description, then the event's URL."""
    if props.get("X-GOOGLE-CONFERENCE"):
        return props["X-GOOGLE-CONFERENCE"].strip()
    for text in (props.get("LOCATION", ""), props.get("DESCRIPTION", "")):
        for m in re.finditer(r"https?://[^\s<>\"']+", text):
            link = m.group(0).rstrip(".,)")
            if any(host in link for host in MEETING_HOSTS):
                return link
    return props.get("URL", "").strip()


def parse_ics(text, tz=None):
    """Every VEVENT in the feed as Event objects."""
    tz = tz or local_tz()
    events = []
    props = None
    for line in unfold(text):
        name, params, value = split_line(line)
        if name == "BEGIN" and value.upper() == "VEVENT":
            props, exdates, rrule, rec_id = {}, set(), None, None
            start, end, duration, all_day = None, None, None, False
        elif name == "END" and value.upper() == "VEVENT" and props is not None:
            if start is not None:
                if end is None:
                    end = start + (duration if duration is not None else timedelta(days=1) if all_day else timedelta())
                events.append(Event(
                    uid=props.get("UID", ""), start=start, end=end, all_day=all_day,
                    summary=unescape(props.get("SUMMARY", "")).strip(),
                    location=unescape(props.get("LOCATION", "")).strip(),
                    url=meeting_link({k: unescape(v) for k, v in props.items()}),
                    rrule=rrule, exdates=exdates, recurrence_id=rec_id,
                    cancelled=props.get("STATUS", "").upper() == "CANCELLED"))
            props = None
        elif props is None:
            continue
        elif name == "DTSTART":
            start, all_day = parse_dt(value, params, tz)
        elif name == "DTEND":
            end, _ = parse_dt(value, params, tz)
        elif name == "DURATION":
            duration = parse_duration(value)
        elif name == "RRULE":
            rrule = parse_rrule(value, tz)
        elif name == "EXDATE":
            for v in value.split(","):
                if v.strip():
                    exdates.add(parse_dt(v, params, tz)[0])
        elif name == "RECURRENCE-ID":
            rec_id, _ = parse_dt(value, params, tz)
        else:
            props[name] = value
    return events


# --- recurrence --------------------------------------------------------------

def _month_days(year, month, rule, start):
    """Days of one month a MONTHLY (or YEARLY with BYMONTH) rule picks."""
    first = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    days_in = (nxt - first).days
    picks = set()
    if rule.get("BYMONTHDAY"):
        for n in rule["BYMONTHDAY"]:
            d = n if n > 0 else days_in + 1 + n
            if 1 <= d <= days_in:
                picks.add(d)
    elif rule.get("BYDAY"):
        for ordinal, wd in rule["BYDAY"]:
            matching = [d for d in range(1, days_in + 1) if date(year, month, d).weekday() == wd]
            if ordinal is None:
                picks.update(matching)
            elif -len(matching) <= ordinal <= len(matching) and ordinal != 0:
                picks.add(matching[ordinal - 1] if ordinal > 0 else matching[ordinal])
    elif start.day <= days_in:
        picks.add(start.day)
    return sorted(picks)


def _candidates(start, rule):
    """Dates a rule names, in order, from the event's first day on (unbounded)."""
    freq = rule.get("FREQ", "DAILY")
    interval = max(1, rule.get("INTERVAL", 1))
    first = start.date()
    by_month = rule.get("BYMONTH")
    if freq == "DAILY":
        by_day = {wd for _, wd in rule.get("BYDAY", [])}
        for k in range(MAX_STEPS):
            d = first + timedelta(days=k * interval)
            if (not by_day or d.weekday() in by_day) and (not by_month or d.month in by_month):
                yield d
    elif freq == "WEEKLY":
        wkst = rule.get("WKST", 0)
        by_day = {wd for _, wd in rule.get("BYDAY", [])} or {first.weekday()}
        week0 = first - timedelta(days=(first.weekday() - wkst) % 7)
        for k in range(MAX_STEPS):
            week = week0 + timedelta(days=7 * k * interval)
            for off in range(7):
                d = week + timedelta(days=off)
                if d >= first and d.weekday() in by_day and (not by_month or d.month in by_month):
                    yield d
    elif freq == "MONTHLY":
        for k in range(MAX_STEPS):
            m = first.month - 1 + k * interval
            year, month = first.year + m // 12, m % 12 + 1
            if by_month and month not in by_month:
                continue
            for d in _month_days(year, month, rule, first):
                dd = date(year, month, d)
                if dd >= first:
                    yield dd
    elif freq == "YEARLY":
        months = by_month or [first.month]
        for k in range(MAX_STEPS):
            year = first.year + k * interval
            for month in months:
                if rule.get("BYDAY") or rule.get("BYMONTHDAY"):
                    days = _month_days(year, month, rule, first)
                else:
                    nxt = date(year + (month == 12), month % 12 + 1, 1)
                    days = [first.day] if first.day <= (nxt - date(year, month, 1)).days else []
                for d in days:
                    dd = date(year, month, d)
                    if dd >= first:
                        yield dd


def starts(event, until):
    """Start times of a repeating event up to `until` (aware), DTSTART included."""
    rule = event.rrule or {}
    count = rule.get("COUNT")
    stop = rule.get("UNTIL")
    if stop is not None and stop < until:
        until = stop
    tz = event.start.tzinfo
    t = event.start.timetz()

    def all_starts():
        first = True
        for d in _candidates(event.start, rule):
            s = datetime.combine(d, t)
            if s.tzinfo is None:
                s = s.replace(tzinfo=tz)
            if first:
                first = False
                if s != event.start:
                    yield event.start    # DTSTART is always the first instance, whether or not the rule names it
            yield s

    seen = 0
    for s in all_starts():
        if s > until:
            return
        yield s
        seen += 1
        if count and seen >= count:
            return


def occurrences(events, day, tz=None):
    """Every instance of every event that touches `day` (a date), by start time,
    all-day ones first. Cancelled events and excluded instances are left out;
    an edited instance replaces the one the rule would have made."""
    tz = tz or local_tz()
    day_start = datetime.combine(day, datetime.min.time()).replace(tzinfo=tz)
    day_end = day_start + timedelta(days=1)
    overrides = {(e.uid, e.recurrence_id) for e in events if e.recurrence_id is not None}
    out = []

    def add(e, start, end):
        if start < day_end and (end > day_start or (end == start and start >= day_start)):
            out.append(Occurrence(start, end, e.summary, e.location, e.url, e.all_day, e.uid))

    for e in events:
        if e.cancelled:
            continue
        if e.recurrence_id is not None or not e.rrule:
            add(e, e.start, e.end)
            continue
        for s in starts(e, day_end):
            if s in e.exdates or (e.uid, s) in overrides:
                continue
            add(e, s, s + e.duration)
    out.sort(key=lambda o: (not o.all_day, o.start, o.end))
    return out


def next_meeting(occs, now, warn_s):
    """(occurrence, state) for the key: the meeting going on now, else the next
    one today; all-day events do not count as meetings. (None, '') without one."""
    timed = [o for o in occs if not o.all_day]
    ongoing = [o for o in timed if o.state(now, warn_s) == "now"]
    if ongoing:
        o = min(ongoing, key=lambda o: o.end)
        return o, "now"
    for o in timed:
        st = o.state(now, warn_s)
        if st in ("soon", "later"):
            return o, st
    return None, ""


# --- the feed ----------------------------------------------------------------

def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"Accept": "text/calendar, */*", "User-Agent": "deckmate"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


class Feed:
    """One feed URL: fetched in a background thread, cached on disk, expanded
    per day on demand. today(now) is what the keys read."""

    def __init__(self, url, start=True, tz=None):
        self.url = url
        self.tz = tz or local_tz()
        self.events = []
        self.fetched_at = None
        self.error = ""
        self.loaded = False
        self._lock = threading.Lock()
        self._day, self._today, self._version, self._today_version = None, [], 0, -1
        self.cache = os.path.join(CACHE_DIR, "calendar-" + hashlib.sha1(url.encode()).hexdigest()[:16] + ".ics")
        self._load_cache()
        if start:
            threading.Thread(target=self._loop, daemon=True).start()

    def _load_cache(self):
        try:
            with open(self.cache, encoding="utf-8") as f:
                self.set_text(f.read())
            self.fetched_at = os.path.getmtime(self.cache)
        except (OSError, ValueError):
            pass

    def set_text(self, text):
        events = parse_ics(text, self.tz)
        with self._lock:
            self.events = events
            self.loaded = True
            self._version += 1

    def _loop(self):
        while True:
            wait = POLL_S
            try:
                text = fetch(self.url)
                if "BEGIN:VCALENDAR" not in text[:2000]:
                    raise ValueError("not an iCalendar feed")
                self.set_text(text)
                self.fetched_at = time.time()
                self.error = ""
                try:
                    os.makedirs(CACHE_DIR, exist_ok=True)
                    tmp = self.cache + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        f.write(text)
                    os.replace(tmp, self.cache)
                except OSError as e:
                    log.info(f"calendar: cannot cache the feed: {e}")
            except Exception as e:       # network down, HTTP error, bad feed
                self.error = f"calendar: {e}"
                log.info(self.error)
                wait = RETRY_S
            time.sleep(wait)

    def today(self, now=None):
        """Today's occurrences, computed once per day and per fetched copy."""
        now = now or datetime.now(self.tz)
        day = now.date()
        with self._lock:
            if day != self._day or self._today_version != self._version:
                self._today = occurrences(self.events, day, self.tz)
                self._day, self._today_version = day, self._version
            return list(self._today)


_feeds = {}


def feed(url):
    """The one Feed per URL, shared by every key that shows it."""
    if url not in _feeds:
        _feeds[url] = Feed(url)
    return _feeds[url]


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: calfeed.py <ics url>")
    f = Feed(sys.argv[1], start=False)
    f.set_text(fetch(sys.argv[1]))
    print(f"{len(f.events)} events in the feed")
    for o in f.today():
        when = "all day" if o.all_day else f"{o.start.astimezone(f.tz):%H:%M}-{o.end.astimezone(f.tz):%H:%M}"
        print(f"{when:13} {o.summary}" + (f"  [{o.url}]" if o.url else ""))
