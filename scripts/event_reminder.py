#!/usr/bin/env python3
"""Format a generic event reminder without embedded schedules or locations."""

import sys


def main() -> None:
    if len(sys.argv) < 6:
        print(
            "usage: event_reminder.py "
            "<title> <mode:remote|onsite> <startHH:MM> <endHH:MM> <dayLabel>",
            file=sys.stderr,
        )
        raise SystemExit(2)

    title, mode, start_hm, end_hm, day_label = sys.argv[1:6]
    if mode not in {"remote", "onsite"}:
        print("mode must be remote or onsite", file=sys.stderr)
        raise SystemExit(2)

    label = "Remote event reminder" if mode == "remote" else "On-site event reminder"
    print(f"{label}: {title}")
    print(f"Time: {day_label} {start_hm}-{end_hm}")


if __name__ == "__main__":
    main()
