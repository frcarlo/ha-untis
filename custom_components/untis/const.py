"""Constants for the Untis integration."""

from datetime import timedelta

DOMAIN = "untis"

CONF_STUDENTS = "students"

# Days fetched from today on — enough to find the next school day across a short break
FETCH_DAYS = 14
UPDATE_INTERVAL = timedelta(minutes=15)

# Changes are reported as events for today and tomorrow only (like the ntfy pushes)
EVENT_CHANGE = "untis_lesson_changed"
EVENT_DAYS = 2

STORAGE_VERSION = 1
