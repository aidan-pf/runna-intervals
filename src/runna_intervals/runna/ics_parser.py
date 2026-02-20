"""Parse a Runna ICS calendar feed into Intervals.icu planned events.
Runna has no public API, but exposes a private ICS calendar feed URL.
Each VEVENT contains a structured DESCRIPTION field with workout steps.
"""

import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date

import httpx
from icalendar import Calendar

from runna_intervals.models.intervals import (
    IntervalsEvent,
    WorkoutDoc,
    WorkoutStep,
    WorkoutStepPace,
)

# ---------------------------------------------------------------------------
# Unit conversion helpers
# ---------------------------------------------------------------------------

_MI_TO_KM = 1.609344

# Default fallback easy pace. Overridden at runtime via Settings.easy_pace_sec_mi
# or the --easy-pace CLI flag; edit this only to change the compiled-in default.
_EASY_PACE_SEC_MI = 520
_EASY_PACE_SEC_KM = int(_EASY_PACE_SEC_MI / _MI_TO_KM)

# Walking pace used for rest steps: 15:00/mi ≈ 9:19/km
_WALK_PACE_SEC_MI = 900
_WALK_PACE_SEC_KM = int(_WALK_PACE_SEC_MI / _MI_TO_KM)  # 559 sec/km


def _mi_to_km(miles: float) -> float:
    return miles * _MI_TO_KM


def _fmt_km(km: float) -> str:
    """Format a km distance as a compact string for Intervals.icu descriptions."""
    rounded = round(km, 1)
    if rounded == int(rounded):
        return f"{int(rounded)}km"
    return f"{rounded}km"


def _fmt_mi(mi: float) -> str:
    """Format a mile distance as a compact string for Intervals.icu descriptions."""
    if mi == int(mi):
        return f"{int(mi)}mi"
    s = f"{mi:.2f}".rstrip("0")
    return f"{s}mi"


def _pace_str_to_sec_km(pace_mi_str: str) -> int:
    """Convert 'M:SS' min/mile string → integer seconds per km."""
    mins, secs = (int(x) for x in pace_mi_str.split(":"))
    sec_mi = mins * 60 + secs
    km_f = sec_mi / _MI_TO_KM
    k_min = int(km_f // 60)
    k_sec = int(round(km_f % 60))
    if k_sec == 60:
        k_min += 1
        k_sec = 0
    return k_min * 60 + k_sec


def _pace_km_to_sec_mi(pace_km_str: str) -> int:
    """Convert 'M:SS' min/km string → integer seconds per mile."""
    mins, secs = (int(x) for x in pace_km_str.split(":"))
    sec_km = mins * 60 + secs
    return int(sec_km * _MI_TO_KM)


def _fmt_rest(seconds: int) -> str:
    """Format a rest duration for Intervals.icu description.

    Values under 2 minutes stay as seconds (e.g. 90s); 2+ minutes use m/s notation.
    """
    if seconds < 120:
        return f"{seconds}s"
    mins, secs = divmod(seconds, 60)
    return f"{mins}m" if secs == 0 else f"{mins}m{secs:02d}s"


# ---------------------------------------------------------------------------
# Regex patterns for step lines
# ---------------------------------------------------------------------------

# "{N.N}mi|km at {M:SS}/mi|km"  with optional "(note)" and optional ", {N}s walking rest"
# groups: (1) distance value, (2) distance unit, (3) pace M:SS, (4) pace unit, (5) rest secs
_PACED_RE = re.compile(
    r"^(?:•\s*)?(\d+(?:\.\d+)?)\s*(mi|km)\s+at\s+(\d+:\d+)/(mi|km)"
    r"(?:\s*\([^)]*\))?"
    r"(?:,\s*(\d+)s\s+walking\s+rest)?$",
    re.IGNORECASE,
)

# "{N.N}mi|km ... conversational pace ..." with optional ", {N}s walking rest"
# groups: (1) distance value, (2) distance unit, (3) rest secs
_EASY_RE = re.compile(
    r"^(?:•\s*)?(\d+(?:\.\d+)?)\s*(mi|km)"
    r".+?conversational\s+pace"
    r"[^,]*"
    r"(?:,\s*(\d+)s\s+walking\s+rest)?",
    re.IGNORECASE,
)

# "(no faster than M:SS/mi|km)" pace hint embedded in easy-step descriptions
# groups: (1) pace M:SS, (2) pace unit
_EASY_PACE_RE = re.compile(
    r"\(no\s+faster\s+than\s+(\d+:\d+)/(mi|km)\)", re.IGNORECASE
)

# Standalone "{N}s walking rest"
_REST_RE = re.compile(r"^(\d+)s\s+walking\s+rest$", re.IGNORECASE)

# Repeat block openers
_REPS_OF_RE = re.compile(r"^(\d+)\s+reps\s+of:$", re.IGNORECASE)
_REPEAT_NX_RE = re.compile(r"^Repeat the following (\d+)x:$", re.IGNORECASE)

# Header line: "Easy Run • 6mi • 50m - 55m" or "Easy Run • 10km • 50m - 55m"
_HEADER_RE = re.compile(r"^[A-Za-z\s]+•\s*[\d.]+(mi|km)\s*•", re.IGNORECASE)

# App deep-link footer
_APP_LINK_RE = re.compile(r"^📲")


# ---------------------------------------------------------------------------
# Internal step / section models
# ---------------------------------------------------------------------------


@dataclass
class _Step:
    """Parsed Runna workout step."""

    distance_m: float | None = None  # distance in metres (for km-mode output)
    distance_mi: float | None = None  # original ICS distance in miles
    pace_sec_km: int | None = None  # pace in sec/km; None = easy/conversational
    pace_sec_mi: int | None = None  # original ICS pace in sec/mi
    rest_s: int | None = None  # trailing or standalone rest duration
    is_rest: bool = False
    label: str = ""  # "Warm Up", "Cool Down", or ""


@dataclass
class _Block:
    """A repeated set of steps."""

    reps: int
    steps: list[_Step] = dc_field(default_factory=list)


@dataclass
class _Section:
    """A named section of workout steps (Warmup, Main Set, Cooldown, etc.)."""

    header: str  # "Warmup", "Main Set", "Cooldown", or ""
    items: list[_Step | _Block] = dc_field(default_factory=list)


# ---------------------------------------------------------------------------
# Step parsing (structured data)
# ---------------------------------------------------------------------------


def _parse_step_data(line: str) -> list[_Step]:
    """Parse one Runna step line → list of _Step objects.

    A paced step with a trailing rest produces two items:
        [_Step(distance_m=402, pace_sec_km=239), _Step(rest_s=90, is_rest=True)]
    """
    line = line.strip()
    if not line:
        return []

    # Standalone rest
    m = _REST_RE.match(line)
    if m:
        return [_Step(rest_s=int(m.group(1)), is_rest=True)]

    # Easy / conversational step (check before paced)
    m = _EASY_RE.match(line)
    if m:
        val = float(m.group(1))
        dist_unit = m.group(2).lower()
        if dist_unit == "km":
            km = val
            mi = km / _MI_TO_KM
        else:
            mi = val
            km = _mi_to_km(mi)
        lower = line.lower()
        if ("warm" in lower and "up" in lower) or "warmup" in lower:
            label = "Warm Up"
        elif ("cool" in lower and "down" in lower) or "cooldown" in lower:
            label = "Cool Down"
        else:
            label = ""

        # Extract explicit "(no faster than M:SS/mi|km)" pace if present
        pm = _EASY_PACE_RE.search(line)
        pace_sec_mi = None
        pace_sec_km = None
        if pm:
            pace_unit = pm.group(2).lower()
            p_mins, p_secs = (int(x) for x in pm.group(1).split(":"))
            if pace_unit == "km":
                pace_sec_km = p_mins * 60 + p_secs
                pace_sec_mi = _pace_km_to_sec_mi(pm.group(1))
            else:
                pace_sec_mi = p_mins * 60 + p_secs
                pace_sec_km = _pace_str_to_sec_km(pm.group(1))

        steps = [
            _Step(
                distance_m=km * 1000,
                distance_mi=mi,
                pace_sec_km=pace_sec_km,
                pace_sec_mi=pace_sec_mi,
                label=label,
            )
        ]
        if m.group(3):  # rest is group 3 (dist_unit is group 2)
            steps.append(_Step(rest_s=int(m.group(3)), is_rest=True))
        return steps

    # Specific-pace step
    m = _PACED_RE.match(line)
    if m:
        val = float(m.group(1))
        dist_unit = m.group(2).lower()
        pace_str = m.group(3)
        pace_unit = m.group(4).lower()
        if dist_unit == "km":
            km = val
            mi = km / _MI_TO_KM
        else:
            mi = val
            km = _mi_to_km(mi)
        p_mins, p_secs = (int(x) for x in pace_str.split(":"))
        if pace_unit == "km":
            pace_sec_km = p_mins * 60 + p_secs
            pace_sec_mi = _pace_km_to_sec_mi(pace_str)
        else:
            pace_sec_mi = p_mins * 60 + p_secs
            pace_sec_km = _pace_str_to_sec_km(pace_str)

        steps = [
            _Step(
                distance_m=km * 1000,
                distance_mi=mi,
                pace_sec_km=pace_sec_km,
                pace_sec_mi=pace_sec_mi,
            )
        ]
        if m.group(5):  # rest is group 5 (dist_unit=2, pace_str=3, pace_unit=4)
            steps.append(_Step(rest_s=int(m.group(5)), is_rest=True))
        return steps

    return []  # unrecognised line


# ---------------------------------------------------------------------------
# Step → description text / WorkoutStep
# ---------------------------------------------------------------------------


def _step_to_desc_line(
    step: _Step,
    use_miles: bool = False,
    fallback_pace_sec: int | None = None,
) -> str:
    """Convert a _Step to one Intervals.icu description line.

    Args:
        step: The parsed step.
        use_miles: If True, format in miles/min-per-mile; otherwise km/min-per-km.
        fallback_pace_sec: Fallback pace in sec/mi (use_miles=True) or sec/km
            (use_miles=False) for easy steps without an explicit pace target.
    """
    if step.is_rest:
        rest_dur = _fmt_rest(step.rest_s or 0)
        if use_miles:
            pace_m, pace_s = divmod(_WALK_PACE_SEC_MI, 60)
            return f"- {rest_dur} {pace_m}:{pace_s:02d}/mi Pace"
        else:
            pace_m, pace_s = divmod(_WALK_PACE_SEC_KM, 60)
            return f"- {rest_dur} {pace_m}:{pace_s:02d}/km Pace"

    if step.distance_mi is None and step.distance_m is None:
        return ""

    # Distance string
    if use_miles and step.distance_mi is not None:
        dist_str = _fmt_mi(step.distance_mi)
    else:
        km = (step.distance_m or 0) / 1000
        dist_str = _fmt_km(km)

    # Pace
    if use_miles:
        pace_sec = step.pace_sec_mi
        if pace_sec is None:
            pace_sec = fallback_pace_sec
    else:
        pace_sec = step.pace_sec_km
        if pace_sec is None:
            pace_sec = fallback_pace_sec

    if pace_sec is not None:
        pace_m, pace_s = divmod(pace_sec, 60)
        unit = "/mi" if use_miles else "/km"
        return f"- {dist_str} {pace_m}:{pace_s:02d}{unit} Pace"
    else:
        return f"- {dist_str} easy"


def _step_duration_s(step: _Step, easy_pace_sec_km: int = _EASY_PACE_SEC_KM) -> int:
    """Estimate step duration in seconds."""
    if step.is_rest:
        return step.rest_s or 0
    if step.distance_m is None:
        return 0
    pace = step.pace_sec_km or easy_pace_sec_km
    return int(step.distance_m / 1000 * pace)


def _step_to_workout_step(
    step: _Step, easy_pace_sec_km: int = _EASY_PACE_SEC_KM
) -> WorkoutStep:
    """Convert a _Step to a WorkoutStep model for workout_doc (always in km units)."""
    if step.is_rest:
        return WorkoutStep(
            duration=step.rest_s,
            pace=WorkoutStepPace(
                start=_WALK_PACE_SEC_KM, end=_WALK_PACE_SEC_KM, units="sec/km"
            ),
        )
    distance = int(step.distance_m or 0) or None
    duration = _step_duration_s(step, easy_pace_sec_km) or None
    if step.pace_sec_km:
        pace = WorkoutStepPace(
            start=step.pace_sec_km, end=step.pace_sec_km, units="sec/km"
        )
    elif easy_pace_sec_km:
        pace = WorkoutStepPace(
            start=easy_pace_sec_km, end=easy_pace_sec_km, units="sec/km"
        )
    else:
        pace = WorkoutStepPace(start=65, end=79, units="%pace")
    return WorkoutStep(
        distance=distance,
        duration=duration,
        pace=pace,
        text=step.label or None,
    )


# ---------------------------------------------------------------------------
# Paragraph / workout parsing
# ---------------------------------------------------------------------------


def _parse_paragraph(para: str) -> list[_Step | _Block]:
    """Parse one paragraph → list of steps/blocks."""
    lines = para.splitlines()
    first = lines[0].strip()

    # ── Repeat block: "N reps of:" ────────────────────────────────────────
    m = _REPS_OF_RE.match(first)
    if m:
        block = _Block(reps=int(m.group(1)))
        for sub in lines[1:]:
            block.steps.extend(_parse_step_data(sub.strip()))
        return [block]

    # ── Repeat block: "Repeat the following Nx:" ──────────────────────────
    m = _REPEAT_NX_RE.match(first)
    if m:
        block = _Block(reps=int(m.group(1)))
        in_block = False
        for sub in lines[1:]:
            sub = sub.strip()
            if sub.startswith("---"):
                in_block = not in_block
                continue
            if in_block and sub:
                block.steps.extend(_parse_step_data(sub))
        return [block]

    # ── One or more regular step lines ────────────────────────────────────
    items: list[_Step | _Block] = []
    for line in lines:
        items.extend(_parse_step_data(line.strip()))
    return items


def _parse_workout(raw: str) -> list[_Section]:
    """Parse a full Runna workout description → list of named sections."""
    sections: list[_Section] = []
    for para in [p.strip() for p in raw.split("\n\n") if p.strip()]:
        if _HEADER_RE.match(para) or _APP_LINK_RE.match(para):
            continue
        items = _parse_paragraph(para)
        if not items:
            continue

        # Determine section header from item labels
        header = "Main Set"
        for item in items:
            if isinstance(item, _Step):
                if item.label == "Warm Up":
                    header = "Warmup"
                    break
                elif item.label == "Cool Down":
                    header = "Cooldown"
                    break

        sections.append(_Section(header=header, items=items))
    return sections


# ---------------------------------------------------------------------------
# Sections → description text / WorkoutDoc
# ---------------------------------------------------------------------------


def _find_fallback_easy_pace(sections: list[_Section]) -> int | None:
    """Return the first easy pace (sec/mi) found in the Warmup section, or None."""
    for section in sections:
        if section.header == "Warmup":
            for item in section.items:
                if isinstance(item, _Step) and not item.is_rest and item.pace_sec_mi:
                    return item.pace_sec_mi
    return None


def _sections_to_description(
    sections: list[_Section],
    use_miles: bool = False,
    easy_pace_sec_mi: int = _EASY_PACE_SEC_MI,
) -> str:
    """Convert structured sections → Intervals.icu description text."""
    fallback_mi = _find_fallback_easy_pace(sections) or easy_pace_sec_mi

    # Pre-compute fallback in the output unit
    if fallback_mi is not None:
        fallback_km = int(fallback_mi / _MI_TO_KM)
    else:
        fallback_km = None

    fallback_sec = fallback_mi if use_miles else fallback_km
    show_headers = len(sections) > 1

    result: list[str] = []
    last_header: str | None = None

    for section in sections:
        header = section.header
        if result:
            result.append("")
        if show_headers and header != last_header:
            if header:
                result.append(header)
            last_header = header

        for item in section.items:
            if isinstance(item, _Block):
                result.append(f"{item.reps}x")
                for step in item.steps:
                    line = _step_to_desc_line(step, use_miles, fallback_sec)
                    if line:
                        result.append(line)
            else:
                line = _step_to_desc_line(item, use_miles, fallback_sec)
                if line:
                    result.append(line)

    return re.sub(r"\n{3,}", "\n\n", "\n".join(result).strip())


def _sections_to_workout_doc(
    sections: list[_Section], easy_pace_sec_km: int = _EASY_PACE_SEC_KM
) -> WorkoutDoc:
    """Convert structured sections → WorkoutDoc with pace targets (always km units)."""
    steps: list[WorkoutStep] = []
    for section in sections:
        for item in section.items:
            if isinstance(item, _Block):
                substeps = [
                    _step_to_workout_step(s, easy_pace_sec_km) for s in item.steps
                ]
                steps.append(WorkoutStep(reps=item.reps, steps=substeps))
            else:
                ws = _step_to_workout_step(item, easy_pace_sec_km)
                if ws.distance is not None or ws.duration is not None:
                    steps.append(ws)
    return WorkoutDoc(steps=steps)


# ---------------------------------------------------------------------------
# Public parsing API (backwards-compatible)
# ---------------------------------------------------------------------------


def _parse_step_line(line: str) -> list[str]:
    """Parse one Runna step line → list of Intervals.icu description lines."""
    result = []
    for step in _parse_step_data(line):
        desc = _step_to_desc_line(step)
        if desc:
            result.append(desc)
    return result


def _parse_description(
    raw: str, use_miles: bool = False, easy_pace_sec_mi: int = _EASY_PACE_SEC_MI
) -> str:
    """Convert a Runna workout description to Intervals.icu description format."""
    return _sections_to_description(
        _parse_workout(raw), use_miles=use_miles, easy_pace_sec_mi=easy_pace_sec_mi
    )


# ---------------------------------------------------------------------------
# ICS parsing
# ---------------------------------------------------------------------------


def _parse_date(component: object) -> str:
    """Extract DTSTART from a VEVENT component as a YYYY-MM-DD string."""
    dtstart = component.get("DTSTART")  # type: ignore[attr-defined]
    if dtstart is None:
        return "1970-01-01"
    d = dtstart.dt
    if isinstance(d, date):
        return d.strftime("%Y-%m-%d")
    return str(d)[:10]


def _clean_summary(raw: str) -> str:
    """Strip leading emoji and trailing '• Xmi' from SUMMARY."""
    # Remove emoji (anything in Unicode private/emoji ranges at start)
    text = re.sub(
        r"^[\U0001F000-\U0001FFFF\U00002700-\U000027BF\s🏃]+", "", raw
    ).strip()
    # Remove trailing "• X.Xmi" or "• X.Xkm"
    text = re.sub(r"\s*•\s*[\d.]+(mi|km)\s*$", "", text).strip()
    return text


def fetch_ics(url: str) -> str:
    """Fetch an ICS feed from a URL and return the raw text."""
    response = httpx.get(url, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    return response.text


def parse_ics_to_events(
    ics_text: str,
    start_date: str | None = None,
    end_date: str | None = None,
    use_miles: bool = False,
    easy_pace_sec_mi: int | None = None,
    skipped: list[tuple[str, str]] | None = None,
) -> list[IntervalsEvent]:
    """Parse a Runna ICS feed into a list of Intervals.icu events.

    Args:
        ics_text: Raw ICS calendar text.
        start_date: Optional filter — only include workouts on or after this date (YYYY-MM-DD).
        end_date: Optional filter — only include workouts on or before this date (YYYY-MM-DD).
        use_miles: If True, format descriptions in miles/min-per-mile instead of km/min-per-km.
        easy_pace_sec_mi: Override the fallback easy pace in sec/mi for steps with no
            explicit pace. When None, falls back to the module default (520 = 8:40/mi).
        skipped: Optional list to collect (date, name) tuples for workouts that could not
            be parsed. When None, unparseable workouts are silently dropped.

    Returns:
        List of IntervalsEvent objects ready to upload.
    """
    _easy_mi = easy_pace_sec_mi if easy_pace_sec_mi is not None else _EASY_PACE_SEC_MI
    _easy_km = int(_easy_mi / _MI_TO_KM)

    cal = Calendar.from_ical(ics_text)
    events: list[IntervalsEvent] = []

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        workout_date = _parse_date(component)

        if start_date and workout_date < start_date:
            continue
        if end_date and workout_date > end_date:
            continue

        uid = str(component.get("UID", ""))
        summary = _clean_summary(str(component.get("SUMMARY", "Workout")))

        # Unescape ICS newlines (\n → actual newline)
        desc_raw = str(component.get("DESCRIPTION", "")).replace("\\n", "\n")

        est_duration = component.get("X-WORKOUT-ESTIMATED-DURATION")
        moving_time = int(str(est_duration)) if est_duration else 3600

        sections = _parse_workout(desc_raw)
        description = _sections_to_description(
            sections, use_miles=use_miles, easy_pace_sec_mi=_easy_mi
        )
        if not description:
            if skipped is not None:
                skipped.append((workout_date, summary))
            continue

        workout_doc = _sections_to_workout_doc(sections, easy_pace_sec_km=_easy_km)

        events.append(
            IntervalsEvent(
                start_date_local=f"{workout_date}T00:00:00",
                name=summary,
                description=description,
                moving_time=moving_time,
                target="PACE",
                external_id=f"runna-{uid}" if uid else None,
                workout_doc=workout_doc if workout_doc.steps else None,
            )
        )

    return events
