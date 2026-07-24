import pymysql, json

conn = pymysql.connect(host='127.0.0.1', port=3306, user='root', password='', database='swm')
cur = conn.cursor()

# Show column schemas for all key tables
tables = ['departments', 'events', 'members', 'contact_inquiries', 'prayer_requests', 'newsletter_subscribers', 'unit_reports', 'sermons', 'admin_audit_logs', 'harvest_contacts', 'harvest_follow_ups', 'harvest_reminders', 'harvest_worker_tokens', 'harvest_workers']
for t in tables:
    try:
        cur.execute(f"SHOW COLUMNS FROM {t}")
        cols = [r[0] for r in cur.fetchall()]
        print(f"\n=== {t} ({', '.join(cols)}) ===")
    except Exception as e:
        print(f"\n=== {t}: ERROR {e} ===")

# Get departments
cur.execute("SELECT * FROM departments")
cols = [d[0] for d in cur.description]
print("\n=== DEPARTMENTS ===")
for r in cur.fetchall():
    print(dict(zip(cols, r)))

# Get events
cur.execute("SELECT * FROM events")
cols = [d[0] for d in cur.description]
print("\n=== EVENTS ===")
for r in cur.fetchall():
    print(dict(zip(cols, r)))

# Get sermons
cur.execute("SELECT * FROM sermons")
cols = [d[0] for d in cur.description]
print("\n=== SERMONS ===")
for r in cur.fetchall():
    d = dict(zip(cols, r))
    for k in ['telegram_file_id', 'telegram_message_id', 'platform_targets', 'platform_links', 'distribution_status']:
        if k in d:
            d[k] = str(d[k])[:80] if d[k] else None
    print(d)

# Admins - get password hashes too
cur.execute("SELECT id, name, email, password, role, branch_id, department_id FROM admins LIMIT 5")
cols = [d[0] for d in cur.description]
print("\n=== ADMINS (first 5 with password) ===")
for r in cur.fetchall():
    d = dict(zip(cols, r))
    d['password'] = d['password'][:30] + '...'
    print(d)

# Members (sample)
cur.execute("SELECT id, first_name, last_name, email, phone, branch_id, gender, is_active FROM members LIMIT 5")
cols = [d[0] for d in cur.description]
print("\n=== MEMBERS (first 5) ===")
for r in cur.fetchall():
    print(dict(zip(cols, r)))

cur.execute("SELECT COUNT(*) as cnt FROM members")
print(f"Total members: {cur.fetchone()[0]}")

# Contact inquiries (sample)
cur.execute("SELECT * FROM contact_inquiries LIMIT 3")
cols = [d[0] for d in cur.description]
print("\n=== CONTACT_INQUIRIES (first 3) ===")
for r in cur.fetchall():
    print(dict(zip(cols, r)))

# Prayer requests (sample)
cur.execute("SELECT * FROM prayer_requests LIMIT 3")
cols = [d[0] for d in cur.description]
print("\n=== PRAYER_REQUESTS (first 3) ===")
for r in cur.fetchall():
    print(dict(zip(cols, r)))

# Newsletter subscribers (sample)
cur.execute("SELECT * FROM newsletter_subscribers LIMIT 3")
cols = [d[0] for d in cur.description]
print("\n=== NEWSLETTER_SUBSCRIBERS (first 3) ===")
for r in cur.fetchall():
    print(dict(zip(cols, r)))

# Unit reports
cur.execute("SELECT id, branch_id, department_id, hod_admin_id, report_month, status FROM unit_reports LIMIT 5")
cols = [d[0] for d in cur.description]
print("\n=== UNIT_REPORTS (first 5) ===")
for r in cur.fetchall():
    print(dict(zip(cols, r)))

cur.execute("SELECT COUNT(*) as cnt FROM unit_reports")
print(f"Total unit_reports: {cur.fetchone()[0]}")

# Harvest workers
cur.execute("SELECT * FROM harvest_workers")
cols = [d[0] for d in cur.description]
print("\n=== HARVEST_WORKERS ===")
for r in cur.fetchall():
    print(dict(zip(cols, r)))

# Admin audit logs
cur.execute("SELECT * FROM admin_audit_logs LIMIT 3")
cols = [d[0] for d in cur.description]
print("\n=== ADMIN_AUDIT_LOGS (first 3) ===")
for r in cur.fetchall():
    print(dict(zip(cols, r)))

cur.execute("SELECT COUNT(*) as cnt FROM admin_audit_logs")
print(f"Total audit_logs: {cur.fetchone()[0]}")

conn.close()
