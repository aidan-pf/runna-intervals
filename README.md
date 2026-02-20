# runna-intervals

Sync your [Runna](https://runna.com) training plan to [Intervals.icu](https://intervals.icu) planned workouts — automatically, from the command line.

Runna workouts are fetched from your private ICS calendar feed and uploaded to Intervals.icu as structured planned events, complete with step-by-step descriptions with section headers, actual paces from the Runna description, and walking pace for rest intervals. Descriptions can be formatted in **miles** or **km** (default).

---

## How it works

1. Runna exposes your training plan as a private ICS calendar URL.
2. This tool fetches that calendar, parses each workout description, and converts it to Intervals.icu's workout format.
3. Workouts are uploaded via the Intervals.icu API. Re-running is safe — events are upserted by their Runna UID, so no duplicates are created.

---

## Installation

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/aidan-pf/runna-intervals
cd runna-intervals
uv sync
```

The `runna-intervals` command is then available via `uv run runna-intervals`.

---

## Setup

Run the interactive setup wizard once:

```bash
uv run runna-intervals config
```

You'll be prompted for:

| Setting | Where to find it |
|---|---|
| **Intervals.icu API key** | [intervals.icu](https://intervals.icu) → Settings → Developer Settings → Generate API Key |
| **Athlete ID** | Your profile URL: `intervals.icu/i12345` → athlete ID is `i12345` |
| **Runna ICS URL** | Runna app → Profile → Connected Apps & Devices → Connect Calendar → Other Calendar |

Credentials are saved to `~/runna-intervals/.env`.

To review your current config:

```bash
uv run runna-intervals config --show
```

---

## Usage

### Sync upcoming workouts

```bash
uv run runna-intervals sync
```

By default, syncs all workouts from **today onwards** in **km**. Re-running is safe.

### Preview before uploading

```bash
uv run runna-intervals sync --dry-run
```

### Test with a small batch first

```bash
uv run runna-intervals sync --dry-run --limit 2      # preview next 2 workouts
uv run runna-intervals sync --limit 2                 # upload just 2 to verify
```

### See the full workout descriptions

```bash
uv run runna-intervals sync --dry-run --show-desc
```

### Use miles instead of km

By default descriptions are formatted with distances in km and paces in min/km. Pass `--miles` to keep everything in miles and min/mile — matching the units Runna uses:

```bash
uv run runna-intervals sync --miles
uv run runna-intervals sync --miles --dry-run --show-desc   # preview
```

### Sync a specific date range

```bash
uv run runna-intervals sync --start 2026-02-22 --end 2026-02-24
```

### Sync the entire plan (including past workouts)

```bash
uv run runna-intervals sync --all
```

### Set your easy pace

Runna sometimes describes steps as "conversational pace" without giving an explicit pace. `runna-intervals` fills these in with a configurable fallback. The default is **520 sec/mi (8:40/mi)** — change it to match your actual easy pace.

**One-off override** (useful for previewing the effect):

```bash
uv run runna-intervals sync --easy-pace 540 --dry-run --show-desc   # 9:00/mi
uv run runna-intervals sync --easy-pace 480                          # 8:00/mi
```

**Persist it in `.env`** so you never have to pass the flag:

```bash
RUNNA_INTERVALS_EASY_PACE_SEC_MI=510   # 8:30/mi
```

Or re-run the setup wizard — it will prompt you for this value:

```bash
uv run runna-intervals config
```

## All sync options

| Option | Default | Description |
|---|---|---|
| `--start YYYY-MM-DD` | today | Only sync workouts from this date |
| `--end YYYY-MM-DD` | — | Only sync workouts up to this date |
| `--all` | off | Include past workouts (overrides the today default) |
| `--limit N` / `-l N` | — | Cap at N workouts |
| `--dry-run` / `-n` | off | Preview without uploading |
| `--show-desc` | off | Print the converted step-by-step description for each workout |
| `--miles` / `--km` | km | Format descriptions in miles/min-per-mile or km/min-per-km |
| `--easy-pace N` | 520 (8:40/mi) | Fallback pace in sec/mi for steps with no explicit pace |
| `--ics-url URL` | from config | Override the Runna ICS feed URL for this run |

The easy-pace fallback is used for steps Runna describes as "conversational pace" without giving a specific pace. You can set it persistently in `.env`:

```
RUNNA_INTERVALS_EASY_PACE_SEC_MI=480   # 8:00/mi
```

---

## Example output

With `--miles --dry-run --show-desc`:

```
Fetching Runna calendar…
          Runna → Intervals.icu (5 workout(s))
┏━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┓
┃ Date       ┃ Name              ┃ Duration ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━┩
│ 2026-02-20 │ Pyramid Intervals │  60m 00s │
│ 2026-02-21 │ 6mi Easy Run      │  55m 00s │
│ ...        │ ...               │ ...      │
└────────────┴───────────────────┴──────────┘

╭──────────────── 2026-02-20 — Pyramid Intervals ─────────────────╮
│ Warmup                                                          │
│ - 1.25mi 8:40/mi Pace                                           │
│ - 90s 15:00/mi Pace                                             │
│                                                                 │
│ Main Set                                                        │
│ - 0.12mi 6:00/mi Pace                                           │
│ - 60s 15:00/mi Pace                                             │
│ - 0.25mi 6:10/mi Pace                                           │
│ - 90s 15:00/mi Pace                                             │
│ ...                                                             │
│ Cooldown                                                        │
│ - 1.1mi 8:40/mi Pace                                            │
╰─────────────────────────────────────────────────────────────────╯

Dry run — not uploading.
```

### Description format

Each workout description uses Intervals.icu's structured format:

- **Section headers** (`Warmup`, `Main Set`, `Cooldown`) are added automatically based on workout structure.
- **Actual paces** are taken directly from the Runna description (e.g. `8:40/mi Pace`). The warmup pace is used as a fallback for the cooldown when no explicit pace is given.
- **Rest intervals** use a walking pace (`15:00/mi Pace` or `9:19/km Pace`) rather than an effort label.
- **Repeat blocks** are formatted with the `Nx` prefix (e.g. `4x`) when the ICS describes a fixed number of repetitions.

---

## Deleting Runna events

Only events uploaded by `runna-intervals` are ever deleted. Manual workouts, race entries, and anything else on your Intervals.icu calendar are not affected.

### Delete a date range

```bash
uv run runna-intervals delete --start 2026-04-01 --end 2026-04-30
```

### Delete from a start date onwards

```bash
uv run runna-intervals delete --start 2026-06-01
```

### Delete all future Runna workouts

```bash
uv run runna-intervals delete --future
```

This deletes all Runna events from today through a 2-year window.

### Preview before deleting

```bash
uv run runna-intervals delete --future --dry-run
```

### Skip the confirmation prompt

```bash
uv run runna-intervals delete --future --yes
```

### All delete options

| Option | Default | Description |
|---|---|---|
| `--start YYYY-MM-DD` | `2000-01-01` (or today with `--future`) | Delete events from this date |
| `--end YYYY-MM-DD` | ~2 years from today | Delete events up to this date |
| `--future` | off | Shorthand for today → 2 years out |
| `--dry-run` / `-n` | off | Preview without deleting |
| `--yes` / `-y` | off | Skip confirmation prompt |

---

## Other commands

```bash
# List existing planned events on Intervals.icu
uv run runna-intervals list-events --start 2026-04-01 --end 2026-04-30
```
