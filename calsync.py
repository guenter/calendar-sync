#!/usr/bin/env python3
"""One-way sync of events from external Google calendars into a shared target calendar.

Synced events are tagged with private extended properties, so repeated runs are
idempotent: new source events are created, changed ones updated, and ones removed
from the source are deleted from the target. Events not created by this tool are
never touched.
"""
import argparse
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]
HERE = Path(__file__).parent
TAG_SOURCE = "calsyncSource"
TAG_EVENT = "calsyncEventId"
RETRIES = 5


def get_service():
    creds = None
    token = HERE / "token.json"
    if token.exists():
        creds = Credentials.from_authorized_user_file(token, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            secrets = HERE / "credentials.json"
            if not secrets.exists():
                sys.exit("Missing credentials.json - see README.md for setup.")
            creds = InstalledAppFlow.from_client_secrets_file(secrets, SCOPES).run_local_server(port=0)
        token.write_text(creds.to_json())
        token.chmod(0o600)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def list_events(service, calendar_id, time_min, time_max, **extra):
    events, page = [], None
    while True:
        resp = service.events().list(
            calendarId=calendar_id, timeMin=time_min, timeMax=time_max,
            singleEvents=True, showDeleted=False, maxResults=250, pageToken=page, **extra,
        ).execute(num_retries=RETRIES)
        events += resp.get("items", [])
        page = resp.get("nextPageToken")
        if not page:
            return events


def _time(t):
    """Normalize a start/end dict so equivalent values compare equal."""
    if "dateTime" in t:
        return datetime.fromisoformat(t["dateTime"]).astimezone(timezone.utc).isoformat()
    return t["date"]


def _clean_time(t):
    return {k: t[k] for k in ("date", "dateTime", "timeZone") if k in t}


def desired_body(src_event, source):
    body = {
        "summary": source.get("prefix", "") + src_event.get("summary", "(no title)"),
        "description": src_event.get("description", ""),
        "location": src_event.get("location", ""),
        "start": _clean_time(src_event["start"]),
        "end": _clean_time(src_event["end"]),
        "transparency": src_event.get("transparency", "opaque"),
        "extendedProperties": {"private": {TAG_SOURCE: source["id"], TAG_EVENT: src_event["id"]}},
    }
    if "color" in source:
        body["colorId"] = str(source["color"])
    return body


def differs(existing, body):
    for field in ("summary", "description", "location", "colorId"):
        if existing.get(field, "") != body.get(field, existing.get(field, "")):
            return True
    if existing.get("transparency", "opaque") != body["transparency"]:
        return True
    return _time(existing["start"]) != _time(body["start"]) or _time(existing["end"]) != _time(body["end"])


def sync_source(service, target, source, time_min, time_max, dry_run):
    counts = {"created": 0, "updated": 0, "deleted": 0, "unchanged": 0}
    src_events = {
        e["id"]: e for e in list_events(service, source["id"], time_min, time_max)
        if e.get("status") != "cancelled" and "start" in e
    }
    existing = {
        e["extendedProperties"]["private"][TAG_EVENT]: e
        for e in list_events(
            service, target, time_min, time_max,
            privateExtendedProperty=f"{TAG_SOURCE}={source['id']}",
        )
    }

    def act(verb, desc, call):
        counts[verb] += 1
        print(f"  {'[dry-run] ' if dry_run else ''}{verb}: {desc}")
        if not dry_run:
            call().execute(num_retries=RETRIES)

    for eid, ev in src_events.items():
        body = desired_body(ev, source)
        desc = f"{body['summary']} ({_time(body['start'])[:16]})"
        if eid not in existing:
            act("created", desc, lambda b=body: service.events().insert(calendarId=target, body=b, sendUpdates="none"))
        elif differs(existing[eid], body):
            act("updated", desc, lambda b=body, t=existing[eid]["id"]: service.events().patch(
                calendarId=target, eventId=t, body=b, sendUpdates="none"))
        else:
            counts["unchanged"] += 1
    for eid, old in existing.items():
        if eid not in src_events:
            act("deleted", old.get("summary", ""), lambda t=old["id"]: service.events().delete(
                calendarId=target, eventId=t, sendUpdates="none"))
    return counts


def cmd_sync(args):
    config = tomllib.loads(Path(args.config).read_text())
    service = get_service()
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=config.get("days_past", 7))).isoformat()
    time_max = (now + timedelta(days=config.get("days_future", 365))).isoformat()
    failed = False
    for source in config["sources"]:
        print(f"Source {source['id']}")
        if source["id"] == config["target"]:
            print("  skipped: source is the target calendar")
            continue
        try:
            counts = sync_source(service, config["target"], source, time_min, time_max, args.dry_run)
            print("  " + ", ".join(f"{v} {k}" for k, v in counts.items()))
        except Exception as e:  # keep going so one broken source doesn't block the rest
            failed = True
            print(f"  FAILED: {e}", file=sys.stderr)
    return 1 if failed else 0


def cmd_calendars(_args):
    for c in get_service().calendarList().list().execute(num_retries=RETRIES)["items"]:
        print(f"{c['id']}\t{c.get('summaryOverride') or c['summary']}\t({c['accessRole']})")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync", help="run the sync")
    s.add_argument("-c", "--config", default=str(HERE / "config.toml"))
    s.add_argument("-n", "--dry-run", action="store_true", help="show changes without applying them")
    s.set_defaults(fn=cmd_sync)
    c = sub.add_parser("calendars", help="list calendar IDs visible to your account")
    c.set_defaults(fn=cmd_calendars)
    args = p.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
