"""Fix all remaining Blade syntax in admin templates."""
import re
import glob

files = glob.glob(r"C:\Web Projects\swm\app\views\admin\*.html")
files += glob.glob(r"C:\Web Projects\swm\app\views\hod\**\*.html", recursive=True)

total_fixes = 0

for filepath in files:
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    
    original = content
    
    # 1. session('key' %}) -> session.get('key')
    # Pattern: session('...'{ %})
    content = re.sub(r"session\('([^']+)'\s*%\}", r"session.get('\1')", content)
    
    # 2. errors.any( %}) -> errors
    content = re.sub(r"errors\.any\(\s*%\}", "errors", content)
    
    # 3. old('key' else '' }}) -> request.form.get('key', '')
    content = re.sub(r"old\('([^']+)'\s*else\s*'([^']*)'\s*\}\}", r"request.form.get('\1', '\2')", content)
    
    # 4. old("key", []) -> request.form.get('key', '[]')
    content = re.sub(r'@json\(old\("([^"]+)",\s*\[\]\)\)', r"request.form.get('\1', '[]')", content)
    
    # 5. \Illuminate\Support\Carbon::parsevent.start_time|e.format(...) 
    # -> event.start_time.strftime('%I:%M %p') if event.start_time else ''
    content = re.sub(
        r"\{\\Illuminate\\Support\\Carbon::parsevent\.start_time\|e\.format\('([^']+)'\)\}",
        r"{{ event.start_time.strftime('\1') if event.start_time else '' }}",
        content
    )
    content = re.sub(
        r"\{\\Illuminate\\Support\\Carbon::parse\(([^)]+)\)\|e\.format\('([^']+)'\)",
        r"{{ \1.strftime('\2') if \1 else '' }}",
        content
    )
    
    # 6. number_format(x) -> "{:,}".format(x) 
    content = re.sub(r"number_format\(([^)]+)\)", r'"{:,}".format(\1)', content)
    
    # 7. request.args.get -> request.query_params.get
    content = content.replace("request.args.get", "request.query_params.get")
    
    # 8. Broken ternary with Blade-style class names like (bg-amber-50 text-amber-600) 
    # These are inline CSS class conditionals from Blade. Simplify them.
    # Pattern: {{ (bg-amber-50 text-amber-600) if condition else ((condition2 ? 'class1' : 'class2')) }}
    # These need manual attention per-file, but let's fix common patterns
    
    # 9. (1) if x else (0) -> 1 if x else 0
    content = content.replace("(1) if ", "1 if ")
    content = content.replace(" else (0)", " else 0")
    
    # 10. Fix 'selected' if old(...) === 'value' 
    # -> 'selected' if request.form.get('key', '') == 'value'
    content = re.sub(
        r"{{\s*'selected'\s*if\s+old\('([^']+)'\s*else\s*''\s*\}\}==\s*'([^']+)'",
        r"{{ 'selected' if request.form.get('\1', '') == '\2'",
        content
    )
    
    # 11. Fix isset(errors) && error_messages -> errors and error_messages  
    content = re.sub(r"isset\(errors\s*&&\s*error_messages\)", "(errors and error_messages)", content)
    
    # 12. Fix {{ \1|default([])|tojson }} (already good Jinja2, leave as-is)
    
    # 13. Fix Blade ternary style: condition ? 'a' : 'b' -> 'a' if condition else 'b'
    # This is risky to do globally, skip for now
    
    # 14. Fix {{ 'selected' if request.args.get(...) else '' }} 
    content = re.sub(
        r"\{\{\s*'selected'\s*if\s+request\.query_params\.get\('([^']+)'\)\|string\s*==\s*(\w+)\|string\s*else\s*''\s*\}\}",
        r"{{ 'selected' if request.query_params.get('\1', '')|string == \2|string else '' }}",
        content
    )
    
    if content != original:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        fixes = sum(1 for a, b in zip(original.split('\n'), content.split('\n')) if a != b)
        total_fixes += fixes
        print(f"FIXED {filepath}: {fixes} lines changed")

print(f"\nTotal lines changed: {total_fixes}")
