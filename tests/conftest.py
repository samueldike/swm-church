"""Test-wide settings. The in-process job loop (reminders, campaigns, analytics flush) is switched off so tests never
send mail or race the database; jobs are exercised by calling them directly."""
import os

os.environ.setdefault("SWM_DISABLE_SCHEDULER", "1")
os.environ.setdefault("SWM_EMAIL_DRY_RUN", "1")
