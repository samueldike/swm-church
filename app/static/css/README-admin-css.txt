admin.css is COMPILED from admin-src.css via the Tailwind v4 CLI (checked into
node_modules/.bin/tailwindcss — no npm install needed, it's already vendored).

Rebuild after changing any admin template's classes:

  ./node_modules/.bin/tailwindcss -i app/static/css/admin-src.css -o app/static/css/admin.css --minify --content "./app/views/admin/**/*.html" --content "./app/views/hod/**/*.html" --content "./app/views/partials/admin-*.html"

Do not hand-edit app/static/css/admin.css directly — edit admin-src.css and
rebuild. Public site pages are unaffected: they use a separate hand-written
CSS system (theme-styles.html etc.), not Tailwind.
