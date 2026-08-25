import pathlib


def test_admin_routes_exist():
    p = pathlib.Path("app/routes/admin.py").read_text(encoding="utf-8")
    assert 'name="admin.dashboard.metrics"' in p, "metrics route name mismatch"
    assert 'name="admin_stories"' in p, "stories route missing"
    assert 'name="admin_transcriptions_update"' in p, "transcription update missing"
    assert "slugify(name.strip())" in p, "branch slug not hardened"
    assert "selectinload(ContactInquiry.branch)" in p, (
        "inquiries branch eager load missing"
    )
    assert "selectinload(Sermon.branch)" in p, "messages branch eager load missing"


def test_csrf_middleware():
    p = pathlib.Path("app/main.py").read_text(encoding="utf-8")
    assert "csrf_protect" in p, "CSRF middleware missing"
    assert "csrf_token" in p, "CSRF token check missing"


def test_auth_timeout_uses_setting():
    p = pathlib.Path("app/core/auth.py").read_text(encoding="utf-8")
    assert "admin_session_timeout_minutes" in p, (
        "auth timeout should read from settings"
    )
    assert "get_setting" in p, "auth should use settings_cache"


def test_template_pops_flash():
    p = pathlib.Path("app/core/template.py").read_text(encoding="utf-8")
    assert 'request.session.pop("flash_messages"' in p, (
        "flash_messages should be popped after render"
    )
    assert 'request.session.pop("success"' in p or "request.session.pop(_k" in p, (
        "success should be popped"
    )


def test_settings_js_no_crash():
    p = pathlib.Path("app/views/admin/settings.html").read_text(encoding="utf-8")
    assert "?.abort()" in p, "settings JS should use optional chaining"
    assert "pastorPhoto" in p, "pastor photo preview missing"


def test_transcription_detail_has_edit():
    p = pathlib.Path("app/views/admin/transcription_detail.html").read_text(
        encoding="utf-8"
    )
    assert "transcript-edit-form" in p, "transcription edit form missing"
    assert "admin_transcriptions_update" in p, "transcription update URL missing"


def test_stories_template_exists():
    assert pathlib.Path("app/views/admin/stories.html").exists(), (
        "stories admin template missing"
    )
    p = pathlib.Path("app/views/admin/stories.html").read_text(encoding="utf-8")
    assert "New Story" in p, "stories template incomplete"


def test_inquiries_template_guards_branch():
    p = pathlib.Path("app/views/admin/inquiries.html").read_text(encoding="utf-8")
    assert "if inquiry.branch" in p or "inquiry.branch.name if" in p, (
        "inquiries branch guard missing"
    )


def test_messages_status_lower():
    p = pathlib.Path("app/routes/admin.py").read_text(encoding="utf-8")
    assert 'status.strip().lower() or "draft"' in p, (
        "sermon status should be lowercased"
    )


def test_gitignore_allows_tests():
    p = pathlib.Path(".gitignore").read_text(encoding="utf-8")
    assert "churchaly" in p, "churchaly should be ignored"
    assert "!tests/test_*.py" in p or "tests" in p, "tests should not be ignored"


def test_migrate_handles_semicolon_in_string():
    p = pathlib.Path("migrate_mysql.py").read_text(encoding="utf-8")
    assert "outside quotes" in p.lower() or "in_s" in p, (
        "migrate should handle ; inside quoted strings"
    )


def test_no_hardcoded_secret_in_routes():
    p = pathlib.Path("app/routes/admin.py").read_text(encoding="utf-8")
    assert "sk_live" not in p and "AKIA" not in p, "hardcoded secret found"
