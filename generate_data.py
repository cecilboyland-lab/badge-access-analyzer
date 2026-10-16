"""
generate_data.py
Generates a synthetic badge access-control log for a fictional facility.

All data is fake. No real people, badges, or locations are used.
The generator deliberately injects realistic data-quality problems
(duplicates, missing fields, malformed timestamps) and suspicious
patterns (after-hours access, repeated denials, impossible travel,
dormant badge reactivation) so the analyzer has something to find.

Usage:
    python generate_data.py                 # writes data/raw_access_log.csv
    python generate_data.py --rows 20000 --seed 7
"""

import argparse
import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

# Fictional facility layout: reader ID -> (location name, building)
READERS = {
    "RDR-101": ("Main Lobby Entrance", "Building A"),
    "RDR-102": ("Loading Dock", "Building A"),
    "RDR-103": ("Server Room", "Building A"),
    "RDR-104": ("Records Office", "Building A"),
    "RDR-201": ("North Entrance", "Building B"),
    "RDR-202": ("Training Center", "Building B"),
    "RDR-203": ("Armory", "Building B"),
    "RDR-301": ("Parking Garage", "Building C"),
    "RDR-302": ("Admin Wing", "Building C"),
    "RDR-303": ("Executive Suite", "Building C"),
}

# Readers most staff can use; the rest are restricted
COMMON_READERS = ["RDR-101", "RDR-201", "RDR-301", "RDR-202", "RDR-302"]
RESTRICTED_READERS = ["RDR-102", "RDR-103", "RDR-104", "RDR-203", "RDR-303"]

DEPARTMENTS = ["Operations", "Admin", "IT", "Security", "Training", "Facilities"]


def make_badges(n=250):
    """Create a roster of fake badge holders."""
    badges = []
    for i in range(n):
        dept = random.choice(DEPARTMENTS)
        # IT can use the server room, Security can use the armory, etc.
        allowed = list(COMMON_READERS)
        if dept == "IT":
            allowed.append("RDR-103")
        if dept == "Security":
            allowed += ["RDR-203", "RDR-103"]
        if dept == "Admin":
            allowed += ["RDR-104", "RDR-303"]
        if dept == "Facilities":
            allowed.append("RDR-102")
        badges.append({
            "badge_id": f"B{10000 + i}",
            "department": dept,
            "allowed": allowed,
        })
    return badges


def normal_event(badge, day):
    """A typical weekday business-hours swipe."""
    hour = random.choices(
        range(6, 19),
        weights=[2, 6, 10, 8, 6, 5, 7, 6, 5, 5, 4, 3, 2],
    )[0]
    ts = day.replace(hour=hour, minute=random.randint(0, 59), second=random.randint(0, 59))
    reader = random.choice(badge["allowed"])
    # ~2% of normal swipes fail (bad read, expired PIN, etc.)
    result = "DENIED" if random.random() < 0.02 else "GRANTED"
    return ts, badge, reader, result


def build_events(rows, start, days):
    badges = make_badges()
    events = []

    # 1. Normal traffic (the bulk of the log), weekdays weighted heavier
    while len(events) < int(rows * 0.95):
        day = start + timedelta(days=random.randrange(days))
        if day.weekday() >= 5 and random.random() < 0.85:
            continue  # keep weekends light
        events.append(normal_event(random.choice(badges), day))

    # 2. After-hours access (late night / early morning)
    for _ in range(int(rows * 0.015)):
        b = random.choice(badges)
        day = start + timedelta(days=random.randrange(days))
        hour = random.choice([0, 1, 2, 3, 4, 22, 23])
        ts = day.replace(hour=hour, minute=random.randint(0, 59), second=random.randint(0, 59))
        events.append((ts, b, random.choice(b["allowed"]), "GRANTED"))

    # 3. Repeated denials: a badge hammering a restricted door
    for _ in range(6):
        b = random.choice(badges)
        reader = random.choice([r for r in RESTRICTED_READERS if r not in b["allowed"]] or RESTRICTED_READERS)
        base = (start + timedelta(days=random.randrange(days))).replace(
            hour=random.randint(7, 20), minute=random.randint(0, 50))
        for k in range(random.randint(4, 8)):
            events.append((base + timedelta(seconds=40 * k + random.randint(0, 20)), b, reader, "DENIED"))

    # 4. Impossible travel: same badge at two buildings within ~1 minute
    for _ in range(5):
        b = random.choice(badges)
        base = (start + timedelta(days=random.randrange(days))).replace(
            hour=random.randint(8, 17), minute=random.randint(0, 50))
        events.append((base, b, "RDR-101", "GRANTED"))
        events.append((base + timedelta(seconds=random.randint(20, 70)), b, "RDR-301", "GRANTED"))

    # 5. Dormant badge suddenly active: no use for weeks, then a burst late in the window
    dormant = random.sample(badges, 3)
    events = [e for e in events if e[1] not in dormant]
    for b in dormant:
        day = start + timedelta(days=days - random.randint(1, 5))
        for _ in range(random.randint(3, 6)):
            events.append(normal_event(b, day))

    random.shuffle(events)
    return events


def write_csv(events, path, dirty=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["event_id", "timestamp", "badge_id", "department",
              "reader_id", "reader_location", "building", "result"]
    rows = []
    for i, (ts, b, reader, result) in enumerate(sorted(events, key=lambda e: e[0])):
        loc, bldg = READERS[reader]
        rows.append({
            "event_id": f"EVT{i + 1:07d}",
            "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "badge_id": b["badge_id"],
            "department": b["department"],
            "reader_id": reader,
            "reader_location": loc,
            "building": bldg,
            "result": result,
        })

    if dirty:
        # Inject data-quality problems the cleaning step must catch
        n = len(rows)
        for r in random.sample(rows, int(n * 0.01)):          # exact duplicates
            rows.append(dict(r))
        for r in random.sample(rows, int(n * 0.005)):         # missing badge IDs
            r["badge_id"] = ""
        for r in random.sample(rows, int(n * 0.003)):         # malformed timestamps
            r["timestamp"] = random.choice(["N/A", "2026-13-45 25:61:00", ""])
        for r in random.sample(rows, int(n * 0.004)):         # inconsistent casing / spacing
            r["result"] = random.choice([" granted", "Denied ", "GRANTED "])
        random.shuffle(rows)

    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main():
    p = argparse.ArgumentParser(description="Generate a synthetic badge access log.")
    p.add_argument("--rows", type=int, default=10000, help="approximate number of events")
    p.add_argument("--days", type=int, default=60, help="length of the log window in days")
    p.add_argument("--seed", type=int, default=42, help="random seed for reproducibility")
    p.add_argument("--out", default="data/raw_access_log.csv")
    args = p.parse_args()

    random.seed(args.seed)
    start = datetime(2026, 8, 1)
    events = build_events(args.rows, start, args.days)
    n = write_csv(events, Path(args.out))
    print(f"Wrote {n:,} rows to {args.out}")


if __name__ == "__main__":
    main()
