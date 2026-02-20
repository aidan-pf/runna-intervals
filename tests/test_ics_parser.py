"""Tests for the Runna ICS calendar parser."""

from runna_intervals.runna.ics_parser import (
    _EASY_PACE_SEC_KM,
    _fmt_km,
    _fmt_rest,
    _parse_description,
    _parse_step_line,
    parse_ics_to_events,
)


class TestUnitConversions:
    def test_fmt_km_whole(self) -> None:
        assert _fmt_km(2.0) == "2km"

    def test_fmt_km_fractional(self) -> None:
        assert _fmt_km(1.207) == "1.2km"

    def test_fmt_rest_sub_minute(self) -> None:
        assert _fmt_rest(40) == "40s"

    def test_fmt_rest_under_two_minutes(self) -> None:
        assert _fmt_rest(90) == "90s"

    def test_fmt_rest_whole_minutes(self) -> None:
        assert _fmt_rest(120) == "2m"

    def test_fmt_rest_mixed(self) -> None:
        assert _fmt_rest(150) == "2m30s"


class TestParseStepLine:
    def test_easy_warmup(self) -> None:
        lines = _parse_step_line(
            "1.25mi warm up at a conversational pace (no faster than 8:40/mi), 90s walking rest"
        )
        assert len(lines) == 2
        assert "2km" in lines[0]
        assert "5:23/km Pace" in lines[0]  # 8:40/mi → 5:23/km
        assert "90s" in lines[1]  # kept as seconds (< 2 min)
        assert "9:19/km Pace" in lines[1]  # walking pace for rest

    def test_easy_cooldown(self) -> None:
        # No explicit pace — no fallback in step-line context — shows "easy"
        lines = _parse_step_line(
            "1.1mi cool down at a conversational pace (or slower!)"
        )
        assert len(lines) == 1
        assert "1.8km" in lines[0]
        assert "easy" in lines[0]

    def test_easy_run_no_rest(self) -> None:
        lines = _parse_step_line(
            "6mi easy run at a conversational pace (no faster than 8:40/mi). "
            "This is a limit, not a target - run at whatever pace feels truly easy!"
        )
        assert len(lines) == 1
        assert "9.7km" in lines[0]
        assert (
            "5:23/km Pace" in lines[0]
        )  # pace extracted from "(no faster than 8:40/mi)"

    def test_paced_step_no_rest(self) -> None:
        lines = _parse_step_line("0.5mi at 6:45/mi")
        assert len(lines) == 1
        assert "0.8km" in lines[0]
        assert "4:12/km" in lines[0]

    def test_paced_step_with_rest(self) -> None:
        lines = _parse_step_line("0.25mi at 6:25/mi, 90s walking rest")
        assert len(lines) == 2
        assert "0.4km" in lines[0]
        assert "3:59/km" in lines[0]
        assert "9:19/km Pace" in lines[1]  # walking pace for rest

    def test_paced_step_with_range_note(self) -> None:
        lines = _parse_step_line("0.5mi at 6:45/mi (6:30-7:00/mi), 90s walking rest")
        assert len(lines) == 2
        assert "4:12/km" in lines[0]

    def test_paced_step_with_race_pace_note(self) -> None:
        lines = _parse_step_line("6mi at 7:15/mi (your target Half Marathon pace)")
        assert len(lines) == 1
        assert "9.7km" in lines[0]
        assert "4:30/km" in lines[0]

    def test_standalone_rest_under_minute(self) -> None:
        lines = _parse_step_line("40s walking rest")
        assert lines == ["- 40s 9:19/km Pace"]

    def test_standalone_rest_over_minute(self) -> None:
        lines = _parse_step_line("60s walking rest")
        assert lines == ["- 60s 9:19/km Pace"]

    def test_bullet_prefix_stripped(self) -> None:
        lines = _parse_step_line("• 0.5mi at 6:45/mi, 90s walking rest")
        assert len(lines) == 2
        assert "4:12/km" in lines[0]

    def test_empty_line(self) -> None:
        assert _parse_step_line("") == []


class TestParseDescription:
    def test_easy_run(self) -> None:
        desc = (
            "Easy Run • 6mi • 50m - 55m\n\n"
            "6mi easy run at a conversational pace (no faster than 8:40/mi). "
            "This is a limit, not a target - run at whatever pace feels truly easy!\n\n"
            "📲 View in the Runna app: https://example.com"
        )
        result = _parse_description(desc)
        assert "5:23/km" in result  # pace from "(no faster than 8:40/mi)"
        assert "9.7km" in result
        # Header and app link stripped
        assert "Easy Run •" not in result
        assert "📲" not in result

    def test_intervals_with_reps(self) -> None:
        desc = (
            "Intervals • 5mi • 45m - 50m\n\n"
            "1mi warm up at a conversational pace (no faster than 8:40/mi), 90s walking rest\n\n"
            "3 reps of:\n"
            "• 0.75mi at 6:50/mi, 120s walking rest\n"
            "• 0.25mi at 6:20/mi, 60s walking rest\n\n"
            "1mi cool down at a conversational pace (or slower!)\n\n"
            "📲 View in the Runna app: https://example.com"
        )
        result = _parse_description(desc)
        assert "3x" in result
        assert "1.2km" in result  # 0.75mi
        assert "0.4km" in result  # 0.25mi
        assert "Warmup" in result
        assert "Cooldown" in result

    def test_repeat_following_format(self) -> None:
        desc = (
            "Tempo • 6.5mi • 50m - 55m\n\n"
            "1.25mi warm up at a conversational pace (no faster than 8:40/mi)\n\n"
            "Repeat the following 4x:\n"
            "----------\n"
            "0.5mi at 6:55/mi\n"
            "0.5mi at 8:05/mi\n"
            "----------\n\n"
            "90s walking rest\n\n"
            "1.25mi cool down at a conversational pace (or slower!)\n\n"
            "📲 View in the Runna app: https://example.com"
        )
        result = _parse_description(desc)
        assert "4x" in result
        assert "0.8km" in result
        assert "4:18/km" in result  # 6:55/mi
        assert "5:01/km" in result  # 8:05/mi
        assert "9:19/km" in result  # walking pace for standalone rest

    def test_pyramid_intervals(self) -> None:
        """Multi-line paragraph of paced steps (no repeat block)."""
        desc = (
            "Intervals • 5.5mi • 50m - 1h0m\n\n"
            "1.25mi warm up at a conversational pace (no faster than 8:40/mi), 90s walking rest\n\n"
            "0.12mi at 6:00/mi, 60s walking rest\n"
            "0.25mi at 6:10/mi, 90s walking rest\n\n"
            "1.1mi cool down at a conversational pace (or slower!)\n\n"
            "📲 View in the Runna app: https://example.com"
        )
        result = _parse_description(desc)
        assert "3:44/km" in result  # 6:00/mi
        assert "3:50/km" in result  # 6:10/mi
        assert "Warmup" in result
        assert "Cooldown" in result

    def test_standalone_rest_paragraph(self) -> None:
        desc = (
            "Intervals • 3.5mi • 35m - 40m\n\n"
            "0.75mi warm up at a conversational pace, 90s walking rest\n\n"
            "4 reps of:\n"
            "• 0.25mi at 6:25/mi, 90s walking rest\n\n"
            "60s walking rest\n\n"
            "4 reps of:\n"
            "• 0.12mi at 6:10/mi, 40s walking rest\n\n"
            "0.75mi cool down at a conversational pace (or slower!)\n\n"
            "📲 View in the Runna app: https://example.com"
        )
        result = _parse_description(desc)
        assert result.count("4x") == 2
        assert "9:19/km" in result  # walking pace for standalone 60s rest

    def test_no_triple_blank_lines(self) -> None:
        desc = (
            "Easy Run • 5mi • 40m - 45m\n\n"
            "5mi easy run at a conversational pace (no faster than 8:40/mi).\n\n"
            "📲 View in the Runna app: https://example.com"
        )
        result = _parse_description(desc)
        assert "\n\n\n" not in result


class TestParseICSToEvents:
    _MINIMAL_ICS = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Runna//EN

BEGIN:VEVENT
UID:UPCOMING_PLAN_WORKOUT-abc123
DTSTAMP:20260219
DTSTART:20260401
DTEND:20260402
SUMMARY:🏃 Threshold Intervals • 5mi
DESCRIPTION:Intervals • 5mi • 45m - 50m\\n\\n1mi warm up\\, 90s walking rest\\n\\n4 reps
 of:\\n• 0.5mi at 6:45/mi\\, 90s walking rest\\n\\n1mi cool down\\n\\n📲 View in the Runna
 app: https://example.com
X-WORKOUT-ESTIMATED-DURATION:3000
END:VEVENT

END:VCALENDAR
"""

    def test_parses_single_event(self) -> None:
        events = parse_ics_to_events(self._MINIMAL_ICS)
        assert len(events) == 1

    def test_event_name_clean(self) -> None:
        events = parse_ics_to_events(self._MINIMAL_ICS)
        assert events[0].name == "Threshold Intervals"
        assert "🏃" not in events[0].name
        assert "• 5mi" not in events[0].name

    def test_event_date(self) -> None:
        events = parse_ics_to_events(self._MINIMAL_ICS)
        assert events[0].start_date_local == "2026-04-01T00:00:00"

    def test_event_moving_time(self) -> None:
        events = parse_ics_to_events(self._MINIMAL_ICS)
        assert events[0].moving_time == 3000

    def test_event_external_id(self) -> None:
        events = parse_ics_to_events(self._MINIMAL_ICS)
        assert events[0].external_id == "runna-UPCOMING_PLAN_WORKOUT-abc123"

    def test_date_filter_start(self) -> None:
        events = parse_ics_to_events(self._MINIMAL_ICS, start_date="2026-05-01")
        assert len(events) == 0

    def test_date_filter_end(self) -> None:
        events = parse_ics_to_events(self._MINIMAL_ICS, end_date="2026-03-01")
        assert len(events) == 0

    def test_date_filter_inclusive(self) -> None:
        events = parse_ics_to_events(
            self._MINIMAL_ICS, start_date="2026-04-01", end_date="2026-04-01"
        )
        assert len(events) == 1

    _LONG_RUN_ICS = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Runna//EN

BEGIN:VEVENT
UID:UPCOMING_PLAN_WORKOUT-longrun001
DTSTAMP:20260219
DTSTART:20260223
DTEND:20260224
SUMMARY:🏃 6.5mi Long Run • 6.5mi
DESCRIPTION:Long Run • 6.5mi • 55m - 1h0m\\n\\n6.5mi at a conversational pace\\n\\n📲 View in the Runna app: https://example.com
X-WORKOUT-ESTIMATED-DURATION:3600
END:VEVENT

END:VCALENDAR
"""

    def test_easy_run_pace_derived_from_estimated_duration(self) -> None:
        """A plain 'conversational pace' run should get a concrete sec/km pace
        derived from X-WORKOUT-ESTIMATED-DURATION ÷ distance, not a %pace zone."""
        events = parse_ics_to_events(self._LONG_RUN_ICS)
        assert len(events) == 1
        wdoc = events[0].workout_doc
        assert wdoc is not None
        assert len(wdoc.steps) == 1
        step = wdoc.steps[0]
        assert step.pace is not None
        assert step.pace.units == "sec/km"
        # Global _EASY_PACE_SEC_KM fallback = 340 sec/km (≈ 5:40/km)
        assert step.pace.start == _EASY_PACE_SEC_KM
        assert step.pace.end == _EASY_PACE_SEC_KM

    def test_miles_mode(self) -> None:
        """use_miles=True keeps distances in miles and paces in min/mi."""
        desc = (
            "Intervals • 5.5mi • 50m - 1h0m\n\n"
            "1.25mi warm up at a conversational pace (no faster than 8:40/mi), 90s walking rest\n\n"
            "0.12mi at 6:00/mi, 60s walking rest\n\n"
            "1.1mi cool down at a conversational pace (or slower!)\n\n"
            "📲 View in the Runna app: https://example.com"
        )
        km_result = _parse_description(desc, use_miles=False)
        mi_result = _parse_description(desc, use_miles=True)

        assert "km" in km_result
        assert "mi" in mi_result
        assert "8:40/mi Pace" in mi_result  # warmup pace in miles
        assert "15:00/mi Pace" in mi_result  # walking rest in miles
        assert "1.25mi" in mi_result
        assert "1.1mi" in mi_result  # cooldown uses fallback from warmup


class TestKmFormatParsing:
    """Tests for Runna ICS feeds generated in km/min-per-km units."""

    def test_paced_step_km_input(self) -> None:
        """0.8km at 4:12/km → same output as 0.5mi at 6:45/mi (≈ same distance/pace)."""
        lines = _parse_step_line("0.8km at 4:12/km")
        assert len(lines) == 1
        assert "0.8km" in lines[0]
        assert "4:12/km" in lines[0]

    def test_paced_step_km_with_rest(self) -> None:
        lines = _parse_step_line("0.4km at 3:59/km, 90s walking rest")
        assert len(lines) == 2
        assert "0.4km" in lines[0]
        assert "3:59/km" in lines[0]
        assert "9:19/km Pace" in lines[1]

    def test_easy_step_km_with_explicit_pace(self) -> None:
        """Easy km step with (no faster than M:SS/km) pace hint."""
        lines = _parse_step_line(
            "2km warm up at a conversational pace (no faster than 5:23/km), 90s walking rest"
        )
        assert len(lines) == 2
        assert "2km" in lines[0]
        assert "5:23/km" in lines[0]
        assert "90s" in lines[1]

    def test_easy_step_km_no_explicit_pace(self) -> None:
        lines = _parse_step_line("1.8km cool down at a conversational pace (or slower!)")
        assert len(lines) == 1
        assert "1.8km" in lines[0]
        assert "easy" in lines[0]

    def test_paced_step_km_miles_output(self) -> None:
        """km input with use_miles=True output should give miles/min-per-mile."""
        from runna_intervals.runna.ics_parser import _parse_description

        desc = (
            "Easy Run • 10km • 50m - 55m\n\n"
            "10km easy run at a conversational pace (no faster than 5:23/km).\n\n"
            "📲 View in the Runna app: https://example.com"
        )
        mi_result = _parse_description(desc, use_miles=True)
        km_result = _parse_description(desc, use_miles=False)

        assert "km" in km_result
        assert "mi" in mi_result
        # 5:23/km = 323 sec/km → * 1.609344 ≈ 519 sec/mi ≈ 8:39/mi
        assert "8:39/mi" in mi_result

    def test_parse_description_km_intervals(self) -> None:
        """Full km-format workout with repeat block."""
        from runna_intervals.runna.ics_parser import _parse_description

        desc = (
            "Intervals • 8km • 45m - 50m\n\n"
            "1.6km warm up at a conversational pace (no faster than 5:23/km), 90s walking rest\n\n"
            "4 reps of:\n"
            "• 0.8km at 4:12/km, 90s walking rest\n\n"
            "1.6km cool down at a conversational pace (or slower!)\n\n"
            "📲 View in the Runna app: https://example.com"
        )
        result = _parse_description(desc)
        assert "4x" in result
        assert "1.6km" in result
        assert "0.8km" in result
        assert "4:12/km" in result
        assert "5:23/km" in result
        assert "Warmup" in result
        assert "Cooldown" in result

    def test_ics_km_format_event_name_clean(self) -> None:
        """SUMMARY with km suffix is stripped correctly."""
        ics = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Runna//EN

BEGIN:VEVENT
UID:UPCOMING_PLAN_WORKOUT-km001
DTSTAMP:20260219
DTSTART:20260401
DTEND:20260402
SUMMARY:🏃 Threshold Intervals • 8km
DESCRIPTION:Intervals • 8km • 45m - 50m\\n\\n1.6km warm up at a conversational pace\\, 90s walking rest\\n\\n4 reps of:\\n• 0.8km at 4:12/km\\, 90s walking rest\\n\\n1.6km cool down\\n\\n📲 View in the Runna app: https://example.com
X-WORKOUT-ESTIMATED-DURATION:2700
END:VEVENT

END:VCALENDAR
"""
        events = parse_ics_to_events(ics)
        assert len(events) == 1
        assert events[0].name == "Threshold Intervals"
        assert "8km" not in events[0].name
        assert "🏃" not in events[0].name

    def test_ics_km_format_description(self) -> None:
        """Full km-format ICS produces valid description with correct paces."""
        ics = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Runna//EN

BEGIN:VEVENT
UID:UPCOMING_PLAN_WORKOUT-km002
DTSTAMP:20260219
DTSTART:20260402
DTEND:20260403
SUMMARY:🏃 Easy Run • 10km
DESCRIPTION:Easy Run • 10km • 55m - 1h0m\\n\\n10km easy run at a conversational pace (no faster than 5:23/km). This is a limit\\, not a target!\\n\\n📲 View in the Runna app: https://example.com
X-WORKOUT-ESTIMATED-DURATION:3300
END:VEVENT

END:VCALENDAR
"""
        events = parse_ics_to_events(ics)
        assert len(events) == 1
        assert "10km" in events[0].description
        assert "5:23/km" in events[0].description
