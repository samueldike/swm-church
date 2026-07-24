"""Fix all .total() and .links() calls in admin templates - replace with pagination dict usage."""
import re
import glob

files = glob.glob(r"C:\Web Projects\swm\app\views\admin\*.html")
files += glob.glob(r"C:\Web Projects\swm\app\views\hod\**\*.html", recursive=True)

count = 0
for filepath in files:
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    original = content
    
    # Fix: variable.total() -> pagination.total  
    # Match patterns like: {{ "{:,}".format(admins.total()) }} or {{ admins.total() }}
    content = re.sub(
        r"\{\{\s*\"{:,\}\"\.format\((\w+)\.total\(\)\)\s*\}\}",
        r'{{ "{:,}".format(pagination.total) }}',
        content
    )
    content = re.sub(
        r"\{\{\s*(\w+)\.total\(\)\s*\}\}",
        r"{{ pagination.total }}",
        content
    )
    
    # Fix: variable.links() -> pagination links HTML
    links_replacement = """{% if pagination.has_prev or pagination.has_next %}
  <div class="flex items-center gap-2">
    {% if pagination.has_prev %}<a href="{{ pagination.prev_url }}" class="text-xs text-primary font-medium hover:underline">&larr; Previous</a>{% endif %}
    <span class="text-xs text-gray-400">Page {{ pagination.page }} of {{ pagination.pages }}</span>
    {% if pagination.has_next %}<a href="{{ pagination.next_url }}" class="text-xs text-primary font-medium hover:underline">Next &rarr;</a>{% endif %}
  </div>
  {% endif %}"""
    
    content = re.sub(
        r"\{\{\s*(\w+)\.links\(\)\s*\}\}",
        links_replacement,
        content
    )
    
    if content != original:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        lines_changed = sum(1 for a, b in zip(original.split('\n'), content.split('\n')) if a != b)
        count += lines_changed
        print(f"FIXED {filepath}: {lines_changed} lines")

print(f"\nTotal lines changed: {count}")
