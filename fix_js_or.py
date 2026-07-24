"""Fix broken JavaScript where || was incorrectly replaced with 'or'."""
import re
import glob

files = glob.glob(r"C:\Web Projects\swm\app\views\admin\*.html")
files += glob.glob(r"C:\Web Projects\swm\app\views\hod\**\*.html", recursive=True)

for filepath in files:
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    original = content
    
    # Fix dataset.xxxor'' -> dataset.xxx||''
    content = re.sub(r"(dataset\.\w+)or(')", r"\1||\2", content)
    
    # Fix Object.entries(stats or {}) -> Object.entries(stats || {})
    content = content.replace("Object.entries(stats or {})", "Object.entries(stats || {})")
    
    # Fix (rows or []).map -> (rows || []).map
    content = content.replace("(rows or []).map", "(rows || []).map")
    
    # Fix g.tithe or 0 -> g.tithe || 0
    content = re.sub(r"(\w+\.\w+) or (\d+)", r"\1 || \2", content)
    
    # Fix === 0 or i === -> === 0 || i ===
    content = re.sub(r"(=== \d+) or (i ===)", r"\1 || \2", content)
    
    # Fix route('...', ...) -> url_for('...')
    content = re.sub(r"\broute\('([^']+)'(?:,\s*([^)]+))?\)", lambda m: f"url_for('{m.group(1)}')" if not m.group(2) else f"url_for('{m.group(1)}', {m.group(2)})", content)
    
    # Fix request().query() -> request.query_params
    content = content.replace("request().query()", "request.query_params")
    
    # Fix array_merge(['unitReport' => report.id], request.query_params) -> just report.id
    content = re.sub(r"array_merge\(\['unitReport'\s*=>\s*(\w+\.id)\],\s*request\.query_params\)", r"\1", content)
    
    # Fix Laravel's route('name', id) pattern for URL generation
    content = re.sub(r"url_for\('admin\.unit-reports\.show', unitReport=report\.id\)", r"url_for('admin_unit_report_show', report_id=report.id)", content)
    
    if content != original:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"FIXED {filepath}")

print("Done")
