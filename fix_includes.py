"""Fix all {% include ... with ... %} -> {% with %}{% include %}{% endwith %} in admin templates."""
import re
import glob

def fix_include_with(content: str) -> str:
    """Convert {% include 'X' with key=val, key=val %} to proper Jinja2."""
    pattern = r'\{%\s*include\s+["\']([^"\']+)["\']\s+with\s+(.+?)\s*%\}'
    
    def replacer(m):
        template_path = m.group(1)
        params_str = m.group(2)
        
        # Parse key=value pairs - values can be unquoted (alphanumeric, hyphens, dots, spaces)
        # or quoted with double quotes
        params = []
        pos = 0
        while pos < len(params_str):
            # Skip whitespace and commas
            while pos < len(params_str) and params_str[pos] in ' ,\t':
                pos += 1
            if pos >= len(params_str):
                break
            
            # Parse key
            key_match = re.match(r'(\w+)', params_str[pos:])
            if not key_match:
                break
            key = key_match.group(1)
            pos += key_match.end()
            
            # Skip = sign
            while pos < len(params_str) and params_str[pos] in ' \t':
                pos += 1
            if pos >= len(params_str) or params_str[pos] != '=':
                break
            pos += 1
            
            # Parse value
            while pos < len(params_str) and params_str[pos] in ' \t':
                pos += 1
            
            if pos < len(params_str) and params_str[pos] == '"':
                # Quoted value
                pos += 1
                val_end = params_str.index('"', pos)
                val = params_str[pos:val_end]
                pos = val_end + 1
            else:
                # Unquoted value - read until comma or end
                val_end = pos
                while val_end < len(params_str) and params_str[val_end] not in ',}':
                    val_end += 1
                val = params_str[pos:val_end].rstrip()
                pos = val_end
            
            params.append(f'{key}="{val}"')
        
        with_args = ", ".join(params)
        return '{%% with %s %%}{%% include "%s" %%}{%% endwith %%}' % (with_args, template_path)
    
    return re.sub(pattern, replacer, content)

files = glob.glob(r"C:\Web Projects\swm\app\views\**\*.html", recursive=True)
count = 0
for f in files:
    with open(f, "r", encoding="utf-8") as fh:
        content = fh.read()
    if "include" in content and "with" in content:
        new_content = fix_include_with(content)
        if new_content != content:
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(new_content)
            print(f"FIXED: {f}")
            count += 1

print(f"\nTotal fixed: {count}")
