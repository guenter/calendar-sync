# calendar-sync

One-way sync from external Google calendars (school, sports teams, ...) into a shared family Google Calendar.

External feeds (ICS/webcal links from the school or TeamSnap etc.) first need to be added to Google Calendar
via *Other calendars > From URL*; they then show up as readable calendars and can be used as sources.

## Setup

1. Google Cloud Console: create a project, enable the **Google Calendar API**, configure the OAuth consent screen
   (add yourself as a test user), create an **OAuth client ID of type Desktop app**, and save it as `credentials.json` here.
2. Install [uv](https://docs.astral.sh/uv/); dependencies are installed automatically on first run.
3. `uv run calsync.py calendars` (first run opens a browser for consent) to find calendar IDs.
4. `cp config.example.toml config.toml` and fill in the target and sources.
5. `uv run calsync.py sync --dry-run`, then `uv run calsync.py sync`.

Run it on a schedule, e.g. cron every 30 min: `*/30 * * * * cd /path/to/calendar-sync && uv run calsync.py sync`

## Behaviour

- Creates, updates and deletes events in the target so it mirrors each source within the window (`days_past`/`days_future`).
- Only touches events it created (tagged with private extended properties); manual events in the family calendar are safe.
- Recurring events are expanded into individual instances. Attendees/reminders are not copied, and no invitations are sent.
- Edits made by hand to a synced event are overwritten on the next run.
- A failing source doesn't stop the others; exit code is 1 if any failed.
- Keep `credentials.json` and `token.json` private.
