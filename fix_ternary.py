"""Fix all ?: ternary shorthand and ?? null coalescing in admin templates."""
import re
import glob

files = glob.glob(r"C:\Web Projects\swm\app\views\admin\*.html")
files += glob.glob(r"C:\Web Projects\swm\app\views\hod\**\*.html", recursive=True)

count = 0
for filepath in files:
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    original = content
    
    # Fix: (expr if cond else '') ?: 'fallback' -> expr if cond else 'fallback'
    # Pattern: {{ (X.strftime(...) if X else '') ?: 'N/A' }}
    # -> {{ X.strftime(...) if X else 'N/A' }}
    content = re.sub(
        r"\{\{\s*\(([^)]+)\s+if\s+([^)]+)\s+else\s*''\)\s*\?:\s*'([^']+)'\s*\}\}",
        r"{{ \1 if \2 else '\3' }}",
        content
    )
    
    # Fix: (expr) ?: 'fallback' -> expr or 'fallback'
    # For simpler patterns without if/else
    content = re.sub(
        r"\{\{\s*\(([^)]+)\)\s*\?:\s*'([^']+)'\s*\}\}",
        r"{{ \1 or '\2' }}",
        content
    )
    
    # Fix: expr ?: fallback (without parentheses, in set statements)
    content = re.sub(
        r"(\w+\.\w+)\s*\?:\s*(\[\])",
        r"\1 or \2",
        content
    )
    
    if content != original:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        lines_changed = sum(1 for a, b in zip(original.split('\n'), content.split('\n')) if a != b)
        count += lines_changed
        print(f"FIXED {filepath}: {lines_changed} lines")

print(f"\nTotal lines changed: {count}")
