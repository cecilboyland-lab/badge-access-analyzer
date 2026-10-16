# Data Dictionary

## Source: `data/raw_access_log.csv`

One row per badge swipe at a card reader.

| Field | Type | Required | Description | Example |
|---|---|---|---|---|
| `event_id` | string | Yes | Unique identifier for the swipe event | `EVT0000123` |
| `timestamp` | datetime | Yes | Date and time of the swipe, local time, format `YYYY-MM-DD HH:MM:SS` | `2026-08-14 07:42:10` |
| `badge_id` | string | Yes | Identifier of the badge used | `B10042` |
| `department` | string | No | Department of the badge holder | `IT` |
| `reader_id` | string | Yes | Identifier of the card reader | `RDR-103` |
| `reader_location` | string | No | Plain-language name of the reader's door | `Server Room` |
| `building` | string | No | Building where the reader is installed | `Building A` |
| `result` | string | Yes | Outcome of the swipe: `GRANTED` or `DENIED` | `GRANTED` |

## Business definitions

| Term | Definition |
|---|---|
| **Swipe** | Any badge presentation at a reader, whether granted or denied |
| **Denial rate** | Denied swipes divided by total swipes for a reader or badge |
| **After-hours** | 22:00 to 04:59 local time |
| **Restricted reader** | A reader only certain departments are authorized to use (Server Room, Armory, Records Office, Executive Suite, Loading Dock) |
| **Dormant badge** | A badge with no recorded activity for 30 or more days |
| **Flag** | An event matched by one of the anomaly rules. A flag is a lead for review, not a confirmed incident. |

## Data-quality rules applied during cleaning

| Check | Action | Reported as |
|---|---|---|
| Leading/trailing spaces, inconsistent case in `result` | Normalized to uppercase, trimmed | Result values re-cased/trimmed |
| Exact duplicate rows | Removed | Exact duplicate rows removed |
| Blank required field | Row removed | Rows missing a required field removed |
| Timestamp not in expected format or impossible date | Row removed | Rows with malformed timestamps removed |
| `result` not `GRANTED` or `DENIED` after normalization | Row removed | Rows with invalid result values removed |

Every removal is counted in the Data Quality sheet of `access_summary.xlsx` so no record disappears silently.

## Lineage

```
generate_data.py  ->  data/raw_access_log.csv
                          |
                      analyze.py: clean()
                          |
                  output/clean_access_log.csv
                          |
                      analyze.py: run_rules()
                          |
                  output/flagged_events.csv
                          |
          output/access_summary.xlsx  +  output/charts/*.png
```
