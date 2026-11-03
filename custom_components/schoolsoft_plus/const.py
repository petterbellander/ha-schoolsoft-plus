"""Constants for SchoolSoft Plus."""

from __future__ import annotations

import re
from datetime import timedelta

DOMAIN = "schoolsoft_plus"

CONF_SCHOOL = "school"
CONF_AI_TASK = "ai_task_entity"
DEFAULT_SCHOOL = ""
BASE_URL = "https://sms.schoolsoft.se"
USERTYPE_PARENT = 2

# Schedule, assignments, exams and open work.
UPDATE_INTERVAL = timedelta(minutes=30)
# Lunch, assessments, absence, news and fritids change slowly.
SLOW_INTERVAL = timedelta(hours=3)

# Assignments whose dates fall in this window get their full text fetched.
BODY_WINDOW_PAST = timedelta(days=7)
BODY_WINDOW_FUTURE = timedelta(days=60)
MAX_BODY_CHARS = 2000

# What counts as a test ("prov") among assignment kinds and titles.
EXAM_RE = re.compile(
    r"prov|diagnos|test|förhör|forhor|kunskapskontroll|examination|tentamen|läxförhör",
    re.IGNORECASE,
)

LUNCH_START = (11, 0)
LUNCH_END = (12, 0)
