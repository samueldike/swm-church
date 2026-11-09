"""Blog (WordPress-style posts): statuses, categories/tags, safe HTML, and the admin screens render."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.core import blog

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def test_status_follows_wordpress_rules():
    later, earlier = NOW + timedelta(days=1), NOW - timedelta(days=1)
    assert blog.decide_status("draft", "published", later, NOW) == "draft"
    assert blog.decide_status("pending", "published", None, NOW) == "pending"
    assert blog.decide_status("publish", "published", None, NOW) == "published"
    assert blog.decide_status("publish", "published", later, NOW) == "future"      # Scheduled
    assert blog.decide_status("publish", "published", earlier, NOW) == "published"  # back-dated
    assert blog.decide_status("update", "private", later, NOW) == "private"


def test_category_and_tag_names_are_tidied():
    assert blog.names('["Faith", " faith ", "Hope  Love", ""]') == ["Faith", "Hope Love"]
    assert blog.names("a, b ,A,,c") == ["a", "b", "c"]
    assert blog.names(None) == [] and blog.dump([]) is None
    post = SimpleNamespace(categories=None, category=None)
    blog.set_categories(post, ["Testimony", "Youth"])
    assert post.category == "Testimony" and blog.post_categories(post) == ["Testimony", "Youth"]
    assert blog.post_categories(SimpleNamespace(categories=None, category="Old")) == ["Old"]   # posts from before Blog


def test_content_is_sanitised_but_keeps_images_and_code():
    out = blog.sanitize('<h2>Hi</h2><script>alert(1)</script><p onclick="x">t</p>'
                        '<img src="/static/a.png" alt="A" onerror="x"><img src="javascript:alert(1)"><pre><code>x</code></pre>')
    assert "<script" not in out and "onclick" not in out and "onerror" not in out and "javascript:" not in out
    assert '<img src="/static/a.png" alt="A" loading="lazy">' in out
    assert "<pre><code>x</code></pre>" in out and "<h2>Hi</h2>" in out
    assert blog.words("<p>one two</p><p>three</p>") == 3


def test_every_blog_url_needs_the_media_permission():
    import asyncio

    from app.routes import admin

    # Every blog URL is covered by the media permission.
    from app.core import access
    for method, path in (("GET", "/admin/blog"), ("GET", "/admin/blog/new"), ("GET", "/admin/blog/5/edit"), ("GET", "/admin/blog/5/preview"),
                         ("GET", "/admin/blog/categories"), ("POST", "/admin/blog/save"), ("POST", "/admin/blog/bulk"), ("GET", "/admin/stories")):
        assert access.required_permission(method, path) == "media", path
    assert asyncio.iscoroutinefunction(admin.admin_blog)
