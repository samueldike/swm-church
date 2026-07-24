"""Fix pagination references in admin templates that don't have pagination data."""
import re
import glob

# Templates that DON'T receive pagination from their route
no_pagination_files = [
    r"C:\Web Projects\swm\app\views\admin\admins.html",
    r"C:\Web Projects\swm\app\views\admin\branches.html",
    r"C:\Web Projects\swm\app\views\admin\departments.html",
    r"C:\Web Projects\swm\app\views\admin\harvesttrack.html",
    r"C:\Web Projects\swm\app\views\admin\hods.html",
    r"C:\Web Projects\swm\app\views\admin\inquiries.html",
    r"C:\Web Projects\swm\app\views\admin\messages.html",
    r"C:\Web Projects\swm\app\views\admin\unit-reports.html",
]

# Map of template name to collection variable name
collection_map = {
    "admins.html": "admins",
    "branches.html": "branches",
    "departments.html": "departments",
    "harvesttrack.html": "workers",  # and "contacts"
    "hods.html": "hods",
    "inquiries.html": "inquiries",
    "messages.html": "sermons",
    "unit-reports.html": "reports",
}

for filepath in no_pagination_files:
    fname = filepath.split("\\")[-1]
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    original = content
    
    coll = collection_map.get(fname, "")
    
    # Fix pagination.total -> collection|length
    if coll:
        content = content.replace("pagination.total", f"{coll}|length")
    
    # Remove pagination links blocks for templates without pagination
    # Pattern: {% if pagination.has_prev or pagination.has_next %}...{% endif %}
    content = re.sub(
        r"\{%\s*if\s+pagination\.has_prev\s+or\s+pagination\.has_next\s*%\}.*?\{%\s*endif\s*%\}",
        "",
        content,
        flags=re.DOTALL
    )
    
    if content != original:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"FIXED {filepath}")

print("Done")
