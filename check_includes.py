"""Fix truncated values in already-converted {% with %}{% include %}{% endwith %} lines."""
import re
import glob

# Map of (filename, correct_full_line) 
# We know the original Blade values from the earlier grep output
FIXES = {
    "admin\\dashboard.html": (
        '{% with title="Dashboard", subtitle="Live", headerIcon="ri-dashboard-3-line" %}{% include "partials/admin-topbar.html" %}{% endwith %}',
        '{% with title="Dashboard Overview", subtitle="Live system metrics and operational activity", headerIcon="ri-dashboard-3-line" %}{% include "partials/admin-topbar.html" %}{% endwith %}'
    ),
    "admin\\unit-reports.html": (
        '{% with title="Unit Reports", subtitle="Search, filter, and review departmental submissions" %}{% include "partials/admin-topbar.html" %}{% endwith %}',
        None  # Already correct
    ),
    "admin\\departments.html": (
        '{% with title="Departments", subtitle="Create and maintain ministry departments" %}{% include "partials/admin-topbar.html" %}{% endwith %}',
        None  # Already correct - no space issue
    ),
    "admin\\inquiries.html": (
        None, None  # Check separately
    ),
    "admin\\hods.html": (
        '{% with title="Department HODs", subtitle="Invite and manage HOD accounts" %}{% include "partials/admin-topbar.html" %}{% endwith %}',
        None  # Already correct
    ),
    "admin\\harvesttrack.html": (
        None, None  # Check separately
    ),
    "admin\\events-management.html": (
        '{% with title="Calendar", subtitle="Plan and manage church programs" %}{% include "partials/admin-topbar.html" %}{% endwith %}',
        None  # Already correct
    ),
    "admin\\branches.html": (
        None, None  # Check separately
    ),
    "admin\\events-create.html": (
        '{% with title="Add Program", subtitle="Schedule a church program" %}{% include "partials/admin-topbar.html" %}{% endwith %}',
        None  # Already correct
    ),
    "admin\\branch.html": (
        None, None  # Check separately - title=branch was truncated
    ),
    "admin\\profile.html": (
        '{% with title="My Profile", subtitle="Update your account details and security" %}{% include "partials/admin-topbar.html" %}{% endwith %}',
        None  # Already correct
    ),
    "admin\\admins.html": (
        None, None  # Check separately
    ),
    "admin\\messages.html": (
        '{% with title="Audio Messages", subtitle="Upload, distribute, and publish sermon audio metadata" %}{% include "partials/admin-topbar.html" %}{% endwith %}',
        None  # Already correct
    ),
    "admin\\members.html": (
        '{% with title="Members", subtitle="Create and manage branch members" %}{% include "partials/admin-topbar.html" %}{% endwith %}',
        None  # Already correct
    ),
    "admin\\settings.html": (
        None, None  # Check separately
    ),
    "admin\\sliders.html": (
        None, None  # Check separately
    ),
    "admin\\security-logs.html": (
        '{% with title="Security Logs", subtitle="Audit trail for authentication and privileged actions" %}{% include "partials/admin-topbar.html" %}{% endwith %}',
        None  # Already correct
    ),
    "admin\\unit-report-show.html": (
        '{% with title="Unit Report Details" %}{% include "partials/admin-topbar.html" %}{% endwith %}',
        None  # Already correct
    ),
}

# Just check and report current state for ALL files
files = glob.glob(r"C:\Web Projects\swm\app\views\admin\*.html") + glob.glob(r"C:\Web Projects\swm\app\views\hod\**\*.html", recursive=True)
for f in files:
    with open(f, "r", encoding="utf-8") as fh:
        content = fh.read()
    for i, line in enumerate(content.split('\n'), 1):
        if '{% with' in line and 'include' in line and 'endwith' in line:
            print(f"{f}:{i}: {line.strip()}")
