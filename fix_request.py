"""Fix request('key') -> request.query_params.get('key', '')"""
import re
import glob

files = glob.glob(r"C:\Web Projects\swm\app\views\admin\*.html")
files += glob.glob(r"C:\Web Projects\swm\app\views\hod\**\*.html", recursive=True)

for filepath in files:
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    original = content
    
    # Fix request('key') -> request.query_params.get('key', '')
    content = re.sub(r"request\('([^']+)'\)", r"request.query_params.get('\1', '')", content)
    
    if content != original:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"FIXED {filepath}")

print("Done")
