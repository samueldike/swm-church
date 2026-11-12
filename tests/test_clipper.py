"""Video Clipper (app/core/clipper/): the pure logic. No AI, network or ffmpeg."""
import re

from app.core.clipper import captions, cut, editorial, options, reframe, speech


def _words(text: str, start: float = 0.0, per: float = 0.3, gap: float = 0.05, pauses: dict | None = None):
    """Evenly timed words; ``pauses`` maps a word index to an extra pause BEFORE that word."""
    out, t = [], start
    for k, w in enumerate(text.split()):
        t += (pauses or {}).get(k, 0.0)
        out.append([round(t, 3), round(t + per, 3), w])
        t += per + gap
    return out


# ─── access and options ───────────────────────────────────────────────

def test_who_may_use_the_clipper():
    assert options.allowed("super_admin", None)
    assert options.allowed("branch_admin", None)
    assert options.allowed("admin", None)                 # legacy name of a branch admin
    assert options.allowed("hq_media", None)
    assert options.allowed("hod", "Media")
    assert options.allowed("hod", "Media & Technical Unit")
    assert not options.allowed("hod", "Choir")
    assert not options.allowed("hod", "Multimedia")        # the word "media" on its own, as WhatsApp does
    assert not options.allowed("hq_finance", None)
    assert not options.allowed("resident_pastor", None)


def test_clock_parsing_and_settings():
    assert options.parse_clock("45:00") == 2700
    assert options.parse_clock("1:05:30") == 3930
    assert options.parse_clock("90") == 90
    assert options.parse_clock("") is None
    assert options.parse_clock("12:75") is None
    assert options.clock(3930) == "1:05:30"
    st, problems = options.clean_settings({"count": "99", "length": "x", "format": "vertical", "quality": "720",
                                           "range_start": "45:00", "range_end": "46:00"})
    assert st["count"] == 15 and st["length"] == "30-60" and st["quality"] == 720
    assert problems and "two minutes" in problems[0]
    st, problems = options.clean_settings({"range_start": "soon"})
    assert problems


def test_clipper_routes_are_covered_by_the_policy():
    from app.core.access import required_permission

    for method, path in (("GET", "/admin/clipper"), ("POST", "/admin/clipper/create"), ("POST", "/admin/clipper/3/upload"),
                         ("GET", "/admin/clipper/3/shorts/4/video.mp4"), ("POST", "/admin/clipper/3/retry")):
        assert required_permission(method, path) == "clipper"


# ─── words -> sentences ───────────────────────────────────────────────

def test_units_split_at_sentence_ends_and_measure_playing_time():
    words = _words("Faith is a seed. It grows when you water it. Do you believe?", pauses={4: 2.0})
    units = speech.build_units(words)
    assert [u.text for u in units] == ["Faith is a seed.", "It grows when you water it.", "Do you believe?"]
    assert units[1].w0 == 4 and units[1].w1 == 10
    # A pause inside a unit is not counted as playing time.
    long_pause = _words("We must keep going no matter what happens to us.", pauses={4: 1.5})
    u = speech.build_units(long_pause)[0]
    assert u.speech < (u.end - u.start) - 1.0


# ─── fitting to length ────────────────────────────────────────────────

def _units(n: int, secs: float = 8.0):
    return [speech.Unit(i, i * 10.0, i * 10.0 + secs, f"Sentence {i}.", i * 3, i * 3 + 3, secs) for i in range(n)]


def test_fit_drops_trim_ids_and_never_the_opening_or_ending():
    units = _units(20)
    short = {"edl": [{"i": i} for i in range(5, 14)], "trim": [5, 13, 8, 9], "extra": []}
    f = editorial.fit_short(units, short, floor=30, target=60)
    ids = [x["i"] for x in f["edl"]]
    assert ids[0] == 5 and ids[-1] == 13
    assert f["length"] <= 60 * 1.08


def test_fit_grows_a_short_passage_with_whole_sentences():
    units = _units(20)
    f = editorial.fit_short(units, {"edl": [{"i": 3}, {"i": 4}], "extra": [5, 6]}, floor=30, target=60)
    assert f["length"] >= 30
    assert [x["i"] for x in f["edl"]][:2] == [3, 4]


def test_passage_style_refuses_distant_jumps_and_no_sentence_is_used_twice():
    units = _units(40)
    f = editorial.fit_short(units, {"edl": [{"i": 2}, {"i": 3}, {"i": 30}, {"i": 31}, {"i": 32}]}, floor=10, target=60)
    assert [x["i"] for x in f["edl"]][:3] == [30, 31, 32]
    g = editorial.fit_short(units, {"edl": [{"i": 2}, {"i": 30}]}, floor=10, target=60, style="assembled", used={30})
    assert [x["i"] for x in g["edl"]][0] == 2 and all(x["i"] != 30 for x in g["edl"])


def test_cold_open_must_be_later_in_the_body_and_short():
    units = _units(10, secs=6.0)
    f = editorial.fit_short(units, {"edl": [{"i": 2}, {"i": 3}, {"i": 4}], "cold_open": 4}, floor=10, target=60)
    assert f["cold_open"] == 4
    assert editorial.fit_short(units, {"edl": [{"i": 2}, {"i": 3}], "cold_open": 2}, floor=5, target=60)["cold_open"] is None
    long = _units(10, secs=12.0)
    assert editorial.fit_short(long, {"edl": [{"i": 2}, {"i": 3}], "cold_open": 3}, floor=5, target=60)["cold_open"] is None


def test_plan_welds_neighbours_and_puts_the_teaser_first():
    units = _units(10)
    plan = editorial.plan_from_edl(units, [{"i": 2, "role": "setup"}, {"i": 3, "role": "point"}, {"i": 5, "role": "payoff"}], [6], 3)
    assert plan[0]["cold_open"] and plan[0]["w0"] == units[3].w0
    assert plan[1]["ids"] == [2, 3] and plan[1]["w1"] == units[3].w1
    assert plan[2]["ids"] == [5]
    assert plan[3]["spare"]


# ─── the cut ──────────────────────────────────────────────────────────

def test_cut_removes_pauses_and_fillers_and_lands_on_words():
    words = _words("So the Lord is good um and his mercy endures for ever", pauses={6: 1.2})
    plan = [{"w0": 0, "w1": len(words), "start": words[0][0], "end": words[-1][1]}]
    c = cut.build_cut(plan, words, [])
    texts = [w[2] for w in c["words"]]
    assert texts[0] == "the"                      # an opening "So" goes
    assert "um" not in texts                      # hesitations go everywhere
    assert len(c["segments"]) >= 2                # the pause (and the filler) are cut out
    total_raw = words[-1][1] - words[1][0]
    assert c["length"] < total_raw - 1.0
    for s, e in c["segments"]:                    # every cut starts just before a word and ends just after one
        assert any(abs(s - (w[0] - speech.PAD_IN)) < 0.07 or s >= w[0] - 0.1 for w in words)
        assert e > s


def test_cut_uses_measured_silence_inside_a_stretched_word():
    words = [[0.0, 0.4, "Grace"], [0.45, 2.6, "abounds"], [2.62, 3.0, "today."]]
    plan = [{"w0": 0, "w1": 3, "start": 0, "end": 3}]
    with_gap = cut.build_cut(plan, words, [[1.0, 2.5]])
    without = cut.build_cut(plan, words, [])
    assert with_gap["length"] < without["length"] - 1.0


def test_cold_open_plays_first_then_again_in_place_and_nudges_move_by_words():
    words = _words("One. Two three. Four five six. Seven.")
    plan = [{"w0": 3, "w1": 6, "start": 0, "end": 0, "cold_open": True},
            {"w0": 0, "w1": 7, "start": 0, "end": 0}]
    c = cut.build_cut(plan, words, [])
    texts = [w[2] for w in c["words"]]
    assert texts[:3] == ["Four", "five", "six."] and texts.count("five") == 2
    later = cut.build_cut([{"w0": 0, "w1": 7}], words, [], nudge_start=0.3)
    assert [w[2] for w in later["words"]][0] == "Two"
    shorter = cut.build_cut([{"w0": 0, "w1": 7}], words, [], nudge_end=-0.6)
    assert [w[2] for w in shorter["words"]][-1] != "Seven."


def test_spares_top_up_a_short_cut_but_never_after_the_ending():
    words = _words("A b c. D e f. G h i. J k l.")
    plan = [{"w0": 0, "w1": 3}, {"w0": 6, "w1": 9},
            {"w0": 3, "w1": 6, "spare": True, "after": 0}, {"w0": 9, "w1": 12, "spare": True, "after": 1}]
    c = cut.build_cut(plan, words, [], floor=100)
    texts = [w[2] for w in c["words"]]
    assert texts == ["A", "b", "c.", "D", "e", "f.", "G", "h", "i."]


# ─── the camera ───────────────────────────────────────────────────────

def test_camera_holds_inside_the_dead_zone_and_pans_smoothly():
    still = [(t / 6, 0.5 + (0.01 if t % 2 else -0.01), False) for t in range(60)]
    keys = reframe.camera_path(still, 0.32, duration=10)
    assert len(keys) <= 3 and all(abs(c - keys[0][1]) < 1e-6 for _, c in keys)
    walk = [(t / 6, 0.3 if t < 30 else 0.75, False) for t in range(90)]
    keys = reframe.camera_path(walk, 0.32, duration=15)
    xs = [c for _, c in keys]
    assert xs[0] < 0.4 and xs[-1] > 0.6
    for (t0, c0), (t1, c1) in zip(keys, keys[1:]):
        if t1 > t0:
            assert abs(c1 - c0) / (t1 - t0) <= reframe.MAX_SPEED + 1e-6


def test_camera_cuts_with_the_source():
    track = [(t / 6, 0.25 if t < 12 else 0.8, t == 12) for t in range(24)]
    keys = reframe.camera_path(track, 0.32, duration=4)
    jump = [k for k in zip(keys, keys[1:]) if k[1][0] - k[0][0] < 0.01 and abs(k[1][1] - k[0][1]) > 0.3]
    assert jump


def test_crop_expression_and_filters():
    keys = [(0.0, 0.5), (2.0, 0.5), (3.0, 0.7), (6.0, 0.7)]
    expr = reframe.crop_expr(keys, 1920, 608)
    assert expr.startswith("if(lt(t,") and "t-2.000" in expr
    vf = reframe.video_filter(fmt="vertical", quality=1080, framing="speaker", src_w=1920, src_h=1080, keys=keys)
    assert vf.startswith("crop=w=606:h=1080:x='") and "scale=1080:1920" in vf
    assert "overlay" in reframe.video_filter(fmt="vertical", quality=720, framing="fit", src_w=1920, src_h=1080)
    assert reframe.video_filter(fmt="landscape", quality=1080, framing="speaker", src_w=1920, src_h=1080).startswith("scale=1920:1080")
    assert reframe.needs_tracking("vertical", "speaker", 1920, 1080)
    assert not reframe.needs_tracking("vertical", "speaker", 1080, 1920)


# ─── captions ─────────────────────────────────────────────────────────

def test_bold_captions_highlight_each_word_in_turn_and_srt_is_valid():
    words = _words("Faith comes by hearing, and hearing by the word of God.")
    text = captions.ass(words, style="bold", width=1080, height=1920)
    lines = [l for l in text.splitlines() if l.startswith("Dialogue:")]
    assert len(lines) == len(words)
    assert all(l.count("\\c&H0037C8F0") == 1 for l in lines)
    assert "FAITH" in lines[0] and "{" not in re.sub(r"\{\\c&H[0-9A-F]+\}", "", lines[0])
    srt = captions.srt(words)
    assert srt.startswith("1\n00:00:00,000 --> ")
    clean = captions.ass(words, style="clean", width=1080, height=1920)
    assert "Faith comes by hearing," in clean


def test_caption_text_cannot_inject_override_tags():
    text = captions.ass([[0, 1, "{\\pos(0,0)}hi"]], style="clean", width=1080, height=1920)
    assert "{\\pos" not in text
