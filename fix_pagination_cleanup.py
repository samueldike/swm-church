"""Remove leftover pagination fragments from templates that don't have pagination data."""
import re
import glob

# Templates that DON'T receive pagination from their route
no_pagination_files = [
    r"C:\Web Projects\swm\app\views\admin\admins.html",
    r"C:\Web Projects\swm\app\views\admin\branches.html",
    r"C:\Web Projects\swm\app\views\admin\harvesttrack.html",
    r"C:\Web Projects\swm\app\views\admin\hods.html",
    r"C:\Web Projects\swm\app\views\admin\inquiries.html",
    r"C:\Web Projects\swm\app\views\admin\messages.html",
    r"C:\Web Projects\swm\app\views\admin\unit-reports.html",
]

for filepath in no_pagination_files:
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    original = content
    
    # Remove leftover pagination span and if block
    # Pattern: <span class="text-xs text-gray-400">Page {{ pagination.page }} of {{ pagination.pages }}</span>
    content = re.sub(
        r'\s*<span class="text-xs text-gray-400">Page \{\{ pagination\.page \}\} of \{\{ pagination\.pages \}\}</span>\s*',
        "",
        content
    )
    
    # Remove {% if pagination.has_next %}...{% endif %} lines
    content = re.sub(
        r'\s*\{%\s*if\s+pagination\.has_next\s*%\}.*?\{%\s*endif\s*%\}\s*',
        "",
        content,
        flags=re.DOTALL
    )
    
    # Remove {% if pagination.has_prev %}...{% endif %} lines
    content = re.sub(
        r'\s*\{%\s*if\s+pagination\.has_prev\s*%\}.*?\{%\s*endif\s*%\}\s*',
        "",
        content,
        flags=re.DOTALL
    )
    
    # Remove empty div wrappers left behind
    content = re.sub(r'<div class="mt-4">\s*</div>', "", content)
    
    # Remove orphan {% endif %} lines
    content = re.sub(r'^\s*\{%\s*endif\s*%\}\s*$', "", content, flags=re.MULTILINE)
    
    # Clean up extra blank lines
    content = re.sub(r'\n{3,}', '\n\n', content)
    
    if content != original:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"FIXED {filepath}")

print("Done")
