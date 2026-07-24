"""Fix all broken 'selected' patterns in admin templates."""
import re
import glob

files = glob.glob(r"C:\Web Projects\swm\app\views\admin\*.html")
files += glob.glob(r"C:\Web Projects\swm\app\views\hod\**\*.html", recursive=True)

for filepath in files:
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    original = content
    
    # Pattern 1: {{ 'selected' if (string else '' }}request.form.get('key', '')===str(x)->id)>
    # -> {{ 'selected' if request.form.get('key', '') == x.id|string else '' }}>
    content = re.sub(
        r"\{\{\s*'selected'\s*if\s*\(string\s+else\s*''\s*\}\}\s*request\.form\.get\('([^']+)',\s*'([^']*)'\)\s*===\s*str\((\w+)\)->id\)\s*>",
        r"{{ 'selected' if request.form.get('\1', '\2') == \3.id|string else '' }}>",
        content
    )
    
    # Pattern 2: {{ 'selected' if request.form.get('key', default else '' }} === value)>
    # -> {{ 'selected' if request.form.get('key', default) == value else '' }}>
    content = re.sub(
        r"\{\{\s*'selected'\s*if\s+request\.form\.get\('([^']+)',\s*([^)]*?)\s+else\s*''\s*\}\}\s*===\s*([^)]+)\)\s*>",
        r"{{ 'selected' if request.form.get('\1', \2) == \3 else '' }}>",
        content
    )
    
    # Pattern 3: {{ 'selected' if request.form.get('key', 'default' else '' }}==='value')>
    # -> {{ 'selected' if request.form.get('key', 'default') == 'value' else '' }}>
    content = re.sub(
        r"\{\{\s*'selected'\s*if\s+request\.form\.get\('([^']+)',\s*'([^']*)'\s+else\s*''\s*\}\}\s*===\s*'([^']+)'\)\s*>",
        r"{{ 'selected' if request.form.get('\1', '\2') == '\3' else '' }}>",
        content
    )
    
    # Pattern 4: {{ 'selected' if request('key' else '' }}==='value')>
    # -> {{ 'selected' if request.query_params.get('key', '') == 'value' else '' }}>
    content = re.sub(
        r"\{\{\s*'selected'\s*if\s+request\('([^']+)'\s+else\s*''\s*\}\}\s*===\s*'([^']+)'\)\s*>",
        r"{{ 'selected' if request.query_params.get('\1', '') == '\2' else '' }}>",
        content
    )
    
    # Pattern 5: {{ 'selected' if (string else '' }}request.query_params.get('key') === str(x)->id)>
    # -> {{ 'selected' if request.query_params.get('key', '') == x.id|string else '' }}>
    content = re.sub(
        r"\{\{\s*'selected'\s*if\s*\(string\s+else\s*''\s*\}\}\s*request\.query_params\.get\('([^']+)'\)\s*===\s*str\((\w+)\)->id\)\s*>",
        r"{{ 'selected' if request.query_params.get('\1', '') == \2.id|string else '' }}>",
        content
    )
    
    # Pattern 6: {{ 'selected' if request.form.get('key', admin?->branch_id)===str(b)->id)>
    # -> {{ 'selected' if request.form.get('key', admin.branch_id|string) == b.id|string else '' }}>
    content = re.sub(
        r"\{\{\s*'selected'\s*if\s*\(string\s+else\s*''\s*\}\}\s*request\.form\.get\('([^']+)',\s*admin\?->branch_id\)\s*===\s*str\((\w+)\)->id\)\s*>",
        r"{{ 'selected' if request.form.get('\1', admin.branch_id|string) == \2.id|string else '' }}>",
        content
    )
    
    if content != original:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"FIXED {filepath}")

print("Done")
