import pymysql, json

conn = pymysql.connect(host='127.0.0.1', port=3306, user='root', password='', database='swm')
cur = conn.cursor()

# Site settings
cur.execute("SELECT `key`, value FROM site_settings")
print("=== SITE_SETTINGS ===")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1][:200] if r[1] else ''}")

# Admins
cur.execute("SELECT id, name, email, role, branch_id FROM admins")
print("\n=== ADMINS ===")
for r in cur.fetchall():
    print(f"  id={r[0]} name={r[1]} email={r[2]} role={r[3]} branch_id={r[4]}")

# Branches
cur.execute("SELECT id, name, slug, address, phone, email FROM branches")
print("\n=== BRANCHES ===")
for r in cur.fetchall():
    print(f"  id={r[0]} name={r[1]} slug={r[2]} address={r[3]} phone={r[4]} email={r[5]}")

# Departments
cur.execute("SELECT id, name, slug, description, branch_id, created_by_admin_id FROM departments")
print("\n=== DEPARTMENTS ===")
for r in cur.fetchall():
    print(f"  id={r[0]} name={r[1]} slug={r[2]} desc={r[3][:60] if r[3] else ''} branch={r[4]} created_by={r[5]}")

# Admin department mapping
cur.execute("SELECT id, name, email, department_id FROM admins WHERE department_id IS NOT NULL")
print("\n=== ADMINS WITH DEPARTMENTS ===")
for r in cur.fetchall():
    print(f"  id={r[0]} name={r[1]} email={r[2]} dept_id={r[3]}")

# Sermons
cur.execute("SELECT id, title, slug, preacher, status, published_at, is_featured, telegram_file_id, branch_id FROM sermons")
print("\n=== SERMONS ===")
for r in cur.fetchall():
    print(f"  id={r[0]} title={r[1]} slug={r[2]} preacher={r[3]} status={r[4]} pub={r[5]} feat={r[6]} tg={r[7][:30] if r[7] else None} branch={r[8]}")

# Events
cur.execute("SELECT id, title, slug, description, starts_at, ends_at, is_published, is_major, branch_id, created_by FROM events")
print("\n=== EVENTS ===")
for r in cur.fetchall():
    print(f"  id={r[0]} title={r[1]} slug={r[2]} start={r[4]} end={r[5]} published={r[6]} major={r[7]} branch={r[8]}")

# Counts
for t in ['members', 'contact_inquiries', 'prayer_requests', 'newsletter_subscribers', 'harvest_contacts', 'harvest_follow_ups', 'harvest_reminders']:
    cur.execute(f"SELECT COUNT(*) FROM {t}")
    cnt = cur.fetchone()[0]
    print(f"\n{t}: {cnt} rows")

# Harvest workers
cur.execute("SELECT id, name, phone, role FROM harvest_workers")
print("\n=== HARVEST_WORKERS ===")
for r in cur.fetchall():
    print(f"  id={r[0]} name={r[1]} phone={r[2]} role={r[3]}")

conn.close()
