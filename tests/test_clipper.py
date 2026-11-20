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
    plan = editorial.plan_from_edl(units, [{"i": 2, "role": "setup"}, {"i": 3, "role": "point"}, {"i": 5, "role": "payoff"}], [4, 6], 3)
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

def test_pop_captions_highlight_each_word_in_turn_and_srt_is_valid():
    words = _words("Faith comes by hearing, and hearing by the word of God.")
    text = captions.ass(words, style="bold", width=1080, height=1920)          # the old name = pop
    lines = [l for l in text.splitlines() if l.startswith("Dialogue:")]
    assert len(lines) == len(words)
    assert all(l.count("\\c&H003AE0FF") == 1 for l in lines)                  # #FFE03A: the spoken word
    assert "FAITH" in lines[0]
    srt = captions.srt(words)
    assert srt.startswith("1\n00:00:00,000 --> ")
    clean = captions.ass(words, style="clean", width=1080, height=1920)
    assert "Faith comes by hearing," in clean


def test_caption_text_cannot_inject_override_tags():
    text = captions.ass([[0, 1, "{\\pos(0,0)}hi"]], style="clean", width=1080, height=1920)
    assert "{\\pos" not in text


# ─── sources ──────────────────────────────────────────────────────────

def test_youtube_links_and_cookie_files():
    from app.core.clipper import media

    assert media.is_youtube("https://www.youtube.com/watch?v=abc")
    assert media.is_youtube("https://youtu.be/abc")
    assert media.is_youtube("https://m.youtube.com/live/abc")
    assert not media.is_youtube("https://notyoutube.com/watch")
    assert not media.is_youtube("https://archive.org/download/x.mp4")
    good = "# Netscape HTTP Cookie File\n" + "\n".join(
        f"{'#HttpOnly_' if k % 2 else ''}.youtube.com\tTRUE\t/\tTRUE\t1999999999\tNAME{k}\tvalue{k}" for k in range(4))
    assert media.valid_cookies(good)
    assert not media.valid_cookies("hello world")
    assert not media.valid_cookies(".example.com\tTRUE\t/\tTRUE\t0\ta\tb\n" * 5)


def test_silences_are_found_relative_to_the_recording():
    from app.core.clipper import media

    # A noisy room: floor at -30 dB, speech at -10 dB; pauses of 0.5 s at 2 s and 6 s.
    levels = [-10.0] * 400
    for start in (100, 300):
        levels[start:start + 25] = [-30.0] * 25
    gaps = media.silences_from_levels(levels)
    assert gaps == [[2.0, 2.5], [6.0, 6.5]]
    assert media.silences_from_levels([-20.0] * 400) == []       # music throughout: no contrast, no cuts


def test_a_leaning_sentence_gets_back_what_it_leans_on():
    units = _units(20, secs=5.0)
    units[7].text = "He says, a genuine son of Israel."
    units[4].text = "And so he convinced Nathanael to come."
    f = editorial.fit_short(units, {"edl": [{"i": 4}, {"i": 5}, {"i": 7}, {"i": 8}]}, floor=10, target=60)
    ids = [x["i"] for x in f["edl"]]
    assert 6 in ids                    # the line "He says" refers back to is put back
    assert ids[0] == 3                 # the opening "And so he..." gets its antecedent
    # Not when it would blow the length.
    g = editorial.fit_short(units, {"edl": [{"i": 4}, {"i": 5}, {"i": 7}, {"i": 8}]}, floor=10, target=20)
    assert g["length"] <= 20 * 1.08 * 1.1 + 1e-6


# ─── speed: hosted transcription and the single-pass render ──────────

from app.core.clipper import cloud_speech, render  # noqa: E402


def test_cut_points_split_long_audio_at_the_longest_nearby_pause():
    gaps = [[598.0, 598.3], [610.0, 612.0], [1195.0, 1199.0]]
    pts = cloud_speech.cut_points(1800.0, gaps)
    assert pts[0] == 0.0 and pts[-1] == 1800.0
    assert pts[1] == 611.0          # the 2 s pause beats the 0.3 s one
    assert pts[2] == 1197.0
    assert all(b - a <= cloud_speech.CHUNK + cloud_speech.SEARCH for a, b in zip(pts, pts[1:]))
    assert cloud_speech.cut_points(300.0, []) == [0.0, 300.0]
    assert cloud_speech.cut_points(3600.0, [])[1] == 600.0   # no pauses: cut on time


def test_punctuate_copies_segment_punctuation_onto_timed_words():
    words = [{"word": "grace", "start": 0.0, "end": 0.4}, {"word": "is", "start": 0.4, "end": 0.5},
             {"word": "enough", "start": 0.5, "end": 0.9}, {"word": "amen", "start": 1.2, "end": 1.5}]
    segments = [{"text": " Grace is enough."}, {"text": " Amen!"}]
    out = cloud_speech.punctuate(words, segments)
    assert [w[2] for w in out] == ["Grace", "is", "enough.", "Amen!"]
    assert out[0][:2] == [0.0, 0.4]
    # a word the segment text spells differently keeps its own spelling, and alignment recovers after it
    out = cloud_speech.punctuate([{"word": "uh", "start": 0, "end": .1}, {"word": "faith", "start": .2, "end": .5}],
                                 [{"text": "Faith."}])
    assert [w[2] for w in out] == ["uh", "Faith."]
    assert speech.build_units(cloud_speech.punctuate(words, segments))[0].text == "Grace is enough."


def test_render_clusters_share_one_input_for_nearby_segments():
    segs = [[100.0, 104.0], [104.6, 110.0], [300.0, 305.0], [111.0, 115.0]]
    assert render.clusters(segs) == [[0, 1, 3], [2]]


def test_render_graph_is_one_encode_with_concat_captions_and_loudness():
    segs = [[100.0, 104.0], [104.6, 110.0], [300.0, 305.0]]
    vfs = ["crop=w=270:h=480:x='10':y=0,scale=1080:1920,setsar=1"] * 2 + [
        "split[a][b];[a]scale=1080:1920,boxblur=24:3[bg];[b]scale=1080:1920[fg];[bg][fg]overlay=0:0,setsar=1"]
    inputs, g = render.graph(segs, vfs, 30, subtitles="subtitles='x.ass'")
    assert inputs == [(99.0, 11.5), (299.0, 6.5)]
    assert "[0:v]split=2[sv0][sv1]" in g and "[0:a]asplit=2[sa0][sa1]" in g
    assert "trim=start=1.000:end=5.000" in g and "trim=start=5.600:end=11.000" in g
    assert "[2:v]" not in g and "[1:v]trim=start=1.000:end=6.000" in g
    assert "split[xa2][xb2]" in g and "[xbg2][xfg2]overlay" in g and "[a]" not in g and "[bg]" not in g
    labels = re.findall(r"\[([a-z]+\d+)\]", g)
    outs = [x for x in labels if labels.count(x) != 2]
    assert not outs, outs        # every label is written once and read once
    assert "concat=n=3:v=1:a=1[vc][ac]" in g
    assert "[vc]subtitles='x.ass'[vo]" in g and "loudnorm=I=-14" in g
    _, g2 = render.graph(segs[:1], vfs[:1], 30, subtitles=None)
    assert "[vc]null[vo]" in g2


def test_captions_are_off_unless_turned_on():
    assert options.DEFAULTS["caption_style"] == "none"
    st, _ = options.clean_settings({"format": "square"})
    assert st["caption_style"] == "none" and st["format"] == "square"
    st, _ = options.clean_settings({"caption_style": "bold"})       # the old name means the first design
    assert st["caption_style"] == "pop"
    st, _ = options.clean_settings({"caption_style": "glow"})
    assert st["caption_style"] == "glow"


def _tone(tmp_path, seconds=3):
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        import pytest
        pytest.skip("ffmpeg is not installed here")
    wav = tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-ac", "1", "-ar",
                    "16000", "-c:a", "pcm_s16le", str(wav)], check=True)
    return wav


def test_hosted_transcription_sends_pieces_and_shifts_word_times(tmp_path, monkeypatch):
    wav = _tone(tmp_path)
    sent = []

    class Reply:
        status_code = 200
        headers = {}
        text = ""

        def json(self):
            return {"segments": [{"text": " Hello there, church."}],
                    "words": [{"word": "hello", "start": 0.1, "end": 0.4}, {"word": "there", "start": 0.5, "end": 0.8},
                              {"word": "church", "start": 0.9, "end": 1.4}]}

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        sent.append((url, headers, dict(data), files["file"][0]))
        return Reply()

    monkeypatch.setattr(cloud_speech.httpx, "post", fake_post)
    monkeypatch.setattr(cloud_speech, "cut_points", lambda duration, gaps: [0.0, 1.5, 3.0])
    words = cloud_speech.transcribe_words(wav, key="gsk_test", duration=3.0, gaps=[], language="pcm", hotwords="Scent of Water")
    assert len(sent) == 2 and sent[0][0] == cloud_speech.URL
    assert sent[0][1]["Authorization"] == "Bearer gsk_test"
    assert sent[0][2]["model"] == "whisper-large-v3-turbo" and sent[0][2]["language"] == "en"
    assert sent[0][2]["response_format"] == "verbose_json" and sent[0][2]["prompt"] == "Scent of Water"
    assert [w[2] for w in words] == ["Hello", "there,", "church.", "Hello", "there,", "church."]
    assert words[3][0] == 1.6                       # second piece shifted by its start
    assert not list(tmp_path.glob("groq_*"))        # pieces cleaned up


def test_hosted_transcription_refused_key_falls_back(tmp_path, monkeypatch):
    import pytest

    wav = _tone(tmp_path)

    class Refused:
        status_code = 401
        headers = {}
        text = "invalid api key"

    monkeypatch.setattr(cloud_speech.httpx, "post", lambda *a, **k: Refused())
    with pytest.raises(cloud_speech.CloudUnavailable):
        cloud_speech.transcribe_words(wav, key="bad", duration=3.0, gaps=[])
    with pytest.raises(cloud_speech.CloudUnavailable):
        cloud_speech.transcribe_words(wav, key="", duration=3.0, gaps=[])


def test_local_transcription_works_in_pieces_and_resumes(tmp_path, monkeypatch):
    """A long video is written down ~10 minutes at a time (flat memory) and a retry skips finished pieces."""
    import types

    from app.core.clipper import media, speech

    calls = []

    class Model:
        def transcribe(self, path, **kw):
            calls.append(path)
            w = types.SimpleNamespace(word=" grace", start=1.0, end=1.4)
            seg = types.SimpleNamespace(words=[w], end=2.0)
            return iter([seg]), types.SimpleNamespace(duration=600.0)

    monkeypatch.setattr(speech, "_engine", lambda: Model())
    monkeypatch.setattr(media, "run", lambda *a, **k: None)
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"x" * 10)
    partial = tmp_path / "words.partial.json"
    words = speech.transcribe_words(audio, duration=1700.0, gaps=[], partial=partial)
    assert len(calls) == 3 and [w[0] for w in words] == [1.0, 601.0, 1201.0]
    assert partial.exists()

    import json
    saved = json.loads(partial.read_text())
    del saved["pieces"]["1200.000"]
    partial.write_text(json.dumps(saved))
    calls.clear()
    again = speech.transcribe_words(audio, duration=1700.0, gaps=[], partial=partial)
    assert len(calls) == 1 and again == words


def test_review_runs_in_parallel_groups_and_maps_back_to_each_short(monkeypatch):
    import asyncio

    from app.core.clipper import editorial

    seen = []

    async def fake(units, shorts, **kw):
        seen.append([s["n"] for s in shorts])
        return [{"k": i, "verdict": "keep", "title": f"t{s['n']}"} for i, s in enumerate(shorts)]

    monkeypatch.setattr(editorial, "review", fake)
    shorts = [{"n": n, "edl": []} for n in range(7)]
    out = asyncio.run(editorial.review_all([], shorts, brief="", floor=30, target=45, style="passage"))
    assert seen == [[0, 1, 2], [3, 4, 5], [6]]
    assert {r["k"]: r["title"] for r in out} == {n: f"t{n}" for n in range(7)}


def test_sound_is_cut_accurately_and_the_picture_counts_as_ready_only_once_probed(tmp_path, monkeypatch):
    import json

    from app.core.clipper import media

    got = []
    monkeypatch.setattr(media, "run", lambda cmd, **k: got.append(cmd))
    media.extract_audio(tmp_path / "s.m4a", tmp_path / "a.wav", 600.0, 4200.0)
    cmd = got[0]
    assert cmd[cmd.index("-ss") + 1] == "600.000" and cmd[cmd.index("-t") + 1] == "3600.000"
    assert cmd.index("-ss") < cmd.index("-i")

    monkeypatch.setattr(media, "project_dir", lambda pid: tmp_path)
    monkeypatch.setattr(media, "source_path", lambda pid: tmp_path / "source.mp4")
    assert not media.video_ready(1)
    (tmp_path / "source.mp4").write_bytes(b"x")
    (tmp_path / "info.json").write_text(json.dumps({"duration": 10}))
    assert not media.video_ready(1)
    (tmp_path / "info.json").write_text(json.dumps({"duration": 10, "width": 1280, "offset": 600}))
    assert media.video_ready(1)


# ─── ministry hashtags, Zernio post body ─────────────────────────────

def test_ministry_hashtags_are_parsed_limited_and_always_on_the_caption():
    from app.core import social

    assert social.parse_ministry_tags("#ScentOfWater, #SWM #swm") == ["ScentOfWater", "SWM"]
    assert social.ministry_tags_problem("") and social.ministry_tags_problem("#a #b #c")
    assert social.ministry_tags_problem("#ScentOfWater") is None
    cap = social.with_ministry("Faith grows in the dark.\n#faith #church #prayer #hope #grace #love #god", ["ScentOfWater", "SWM"])
    body, tags = cap.split("\n")
    assert body == "Faith grows in the dark."
    assert tags.split()[:2] == ["#ScentOfWater", "#SWM"] and len(tags.split()) == 7
    again = social.with_ministry(cap, ["ScentOfWater", "SWM"])
    assert again == cap                                         # never added twice
    assert social.with_ministry("No tags here", ["SWM"]) == "No tags here\n#SWM"


def test_post_body_carries_platform_options_and_a_short_x_caption():
    from app.core import social

    cap = ("word " * 80).strip() + "\n#SWM #faith"
    body = social.build_post(caption=cap, title="Grace", media_url="https://media.zernio.com/x.mp4",
                             targets=[{"platform": "youtube", "accountId": "a1", "options": {"visibility": "unlisted"}},
                                      {"platform": "tiktok", "accountId": "a2", "options": {}},
                                      {"platform": "twitter", "accountId": "a3"}],
                             when_utc="2026-12-01T09:00:00Z", tz="UTC", tags=["SWM"])
    assert body["scheduledFor"] == "2026-12-01T09:00:00Z" and "publishNow" not in body
    yt, tt, x = body["platforms"]
    assert yt["platformSpecificData"]["visibility"] == "unlisted" and yt["platformSpecificData"]["title"] == "Grace"
    assert tt["platformSpecificData"]["privacyLevel"] == "PUBLIC_TO_EVERYONE"
    assert len(x["customContent"]) <= 280 and x["customContent"].endswith("#SWM #faith")
    now = social.build_post(caption="hi", title="t", media_url="u", targets=[], when_utc=None, tz="UTC", tags=[])
    assert now["publishNow"] is True


def test_post_status_from_zernio_reply():
    from app.core import social

    ok = {"post": {"status": "published", "platforms": [{"platform": "youtube", "status": "published", "platformPostUrl": "u"}]}}
    assert social.summarize(ok)[0] == "published"
    mixed = {"post": {"platforms": [{"platform": "youtube", "status": "published"},
                                    {"platform": "tiktok", "status": "failed", "errorMessage": "too long"}]}}
    status, rows = social.summarize(mixed)
    assert status == "partial" and rows[1]["error"] == "too long"


# ─── speaker opening ─────────────────────────────────────────────────

def test_a_short_opening_on_a_scripture_slide_opens_on_the_speaker_instead():
    from app.core.clipper import opening
    from app.core.clipper.speech import Unit

    def unit(i, s, e):
        return Unit(i=i, w0=i * 10, w1=i * 10 + 10, start=s, end=e, text=f"sentence {i}", speech=e - s)

    units = [unit(0, 0, 6), unit(1, 6, 10), unit(2, 10, 16)]
    plan = [{"w0": 0, "w1": 30, "start": 0, "end": 16, "text": "...", "role": "setup"}]
    slide_until = 9.0
    look = lambda a, b: 0.0 if a < slide_until else 1.0         # a verse on screen for the first 9 s
    new, how = opening.choose_opening(plan, units, [[0, 16]], look)
    assert how == "cold_open" and new[0]["cold_open"] and new[0]["start"] >= slide_until
    seen, how2 = opening.choose_opening(plan, units, [[0, 20]], lambda a, b: 1.0)
    assert how2 == "kept" and seen == plan
    # No line in the right length range: start later instead (if long enough).
    units2 = [unit(0, 0, 6), unit(1, 6, 9.5), unit(2, 9.5, 30)]
    new3, how3 = opening.choose_opening(plan, units2, [[0, 30]], lambda a, b: 0.0 if a < 9.5 else 1.0,
                                        length_after_skip=lambda pl: 20.0, floor=15.0)
    assert how3 == "skipped" and new3[0]["w0"] == 20


# ─── editor timeline ─────────────────────────────────────────────────

def test_editor_clips_are_cleaned_joined_and_cut_like_the_render():
    from app.core.clipper import timeline

    assert timeline.clips_from_segments([[10, 12], [12.4, 15], [40, 42]]) == [{"start": 10, "end": 15}, {"start": 40, "end": 42}]
    with __import__("pytest").raises(timeline.TimelineError):
        timeline.clean([], 100)
    assert timeline.clean([{"start": -3, "end": 5}, {"start": 7, "end": 7.1}], 100) == [{"start": 0.0, "end": 5.0}]
    words = [[10.0, 10.4, "Grace"], [10.5, 10.9, "is"], [12.5, 12.9, "um"], [13.0, 13.5, "enough."]]
    out = timeline.cut_for({"clips": [{"start": 9.9, "end": 13.8}], "tighten": True}, words, [])
    assert [w[2] for w in out["words"]] == ["Grace", "is", "enough."]          # the "um" is gone
    assert out["length"] < 3.9                                                 # and so is the pause
    raw = timeline.cut_for({"clips": [{"start": 9.9, "end": 13.8}], "tighten": False}, words, [])
    assert raw["segments"] == [[9.9, 13.8]]


# ─── caption designs ─────────────────────────────────────────────────

def test_every_caption_design_makes_a_valid_ass_file_and_legacy_names_map():
    from app.core.clipper import captions

    words = [[0.0, 0.4, "Grace"], [0.45, 0.8, "is"], [0.85, 1.3, "enough,"], [1.6, 2.0, "today."]]
    for sid in captions.STYLES:
        text = captions.ass(words, style=sid, width=1080, height=1920)
        assert "[Events]" in text and text.count("Dialogue:") >= 1, sid
        assert "{\r}" not in text or sid != "glow"
    assert captions.style_id("bold") == "pop" and captions.style_id("none") is None
    assert set(captions.public_styles()) == set(captions.STYLES)


def test_camera_does_not_chase_a_preacher_pacing_to_and_fro():
    import math

    pace = [(t / 6, 0.5 + 0.07 * math.sin(t / 6 * 2.2), False) for t in range(120)]     # 20 s of pacing
    keys = reframe.camera_path(pace, 0.32, duration=20)
    xs = [c for _, c in keys]
    assert max(xs) - min(xs) < 1e-6                     # framed as one zone: the camera never moves


def test_camera_stays_with_its_speaker_when_someone_passes():
    speaker = (0.5, 0.4, 0.08, 0.1, 0.9)
    passer = (0.85, 0.4, 0.2, 0.25, 0.9)
    samples = [{"t": n / 6, "faces": [speaker] + ([passer] if 10 <= n < 14 else []), "cut": False} for n in range(30)]
    picked = [x for _, x, _ in reframe.choose_subject(samples, start_x=0.5)]
    assert all(abs(x - 0.5) < 0.01 for x in picked)      # a 0.7 s walk-past never takes the frame


def test_the_editor_asks_for_more_candidates_than_shorts_wanted():
    from app.core.clipper import editorial

    assert editorial.wanted(12) == 19 and editorial.wanted(1) == 3 and editorial.wanted(100) == 40


def test_make_shorts_refills_until_the_count_is_met(monkeypatch):
    import asyncio

    from app.core.clipper import editorial
    from app.core.clipper.speech import Unit

    units = [Unit(i=i, w0=i, w1=i + 1, start=i * 10.0, end=i * 10.0 + 10, text=f"s{i}", speech=10.0) for i in range(60)]
    rounds = []

    async def fake_round(units_, pool, *, ask, used, **kw):
        rounds.append(ask)
        free = [u.i for u in pool if u.i not in used][: ask * 4]
        # Each round only half of what was asked for survives review.
        got = [{"edl": [{"i": i, "role": "point"} for i in free[n * 4:n * 4 + 4]], "score": 50, "length": 40.0}
               for n in range(max(1, ask // 2))]
        return [g for g in got if g["edl"]], {"hashtags": []}

    async def fake_structure(*a, **k):
        return {"body": [[0.0, 100000.0]]}

    monkeypatch.setattr(editorial, "_round", fake_round)
    monkeypatch.setattr(editorial, "map_structure", fake_structure)
    out, _, _ = asyncio.run(editorial.make_shorts(units, count=12, floor=30, target=45, content_type="sermon",
                                                  title="", style="passage", duration=600))
    assert len(out) == 12 and len(rounds) >= 2
    ids = [i for s in out for i in s["ids"]]
    assert len(ids) == len(set(ids))                      # no sentence in two shorts


def test_whatsapp_caption_is_written_for_whatsapp():
    from app.core import social

    cap = "Grace is enough for *today*. #grace matters\n#SWM #faith #church"
    text = social.whatsapp_caption("Grace *Is* Enough", cap, ["SWM"])
    assert text.startswith("*Grace Is Enough*\n\n")                 # WhatsApp bold title, stray marks removed
    assert "#faith" not in text and "#church" not in text            # topic hashtags do nothing on WhatsApp
    assert "grace matters" in text and text.endswith("\n\n#SWM")     # inline tag reads as a word; ministry tag kept
    short = social.whatsapp_caption("T", "word " * 400, ["SWM"], limit=social.WA_STATUS_CAPTION)
    assert len(short) <= social.WA_STATUS_CAPTION and short.endswith("#SWM")


def test_one_status_across_platforms_and_whatsapp():
    from app.core import social

    assert social.overall([{"status": "published"}, {"status": "published"}]) == "published"
    assert social.overall([{"status": "published"}, {"status": "failed"}]) == "partial"
    assert social.overall([{"status": "failed"}]) == "failed"
    assert social.overall([{"status": "scheduled"}, {"status": "published"}]) == "scheduled"


# ─── YouTube watch ───────────────────────────────────────────────────

def test_watch_reads_channel_links_and_only_takes_finished_lives():
    from app.core.clipper import watch

    assert watch.streams_url("https://www.youtube.com/@scentofwater") == "https://www.youtube.com/@scentofwater/streams"
    assert watch.streams_url("youtube.com/channel/UCabcdefghijklmnopqrstuv/videos").endswith("/channel/UCabcdefghijklmnopqrstuv/streams")
    assert watch.streams_url("https://vimeo.com/x") is None
    assert watch.readiness({"live_status": "was_live", "duration": 9000}) == watch.READY
    assert watch.readiness({"live_status": "is_live"}) == watch.LATER
    assert watch.readiness({"live_status": "post_live", "duration": 9000}) == watch.LATER     # still processing
    assert watch.readiness({"live_status": "is_upcoming"}) == watch.LATER
    assert watch.readiness({"live_status": "was_live", "duration": 30}) == watch.SKIP
    assert watch.readiness({"live_status": "was_live", "duration": 0}) == watch.LATER      # recording not ready yet
    entries = [{"id": "new2"}, {"id": "new1"}, {"id": "old"}]
    look, mark = watch.pick_new(entries, set(), baseline=True, latest=True)
    assert look == ["new2"] and mark == {"new1", "old"}                 # first look: only the latest
    look, mark = watch.pick_new(entries, {"new1", "old"}, baseline=False, latest=True)
    assert look == ["new2"] and not mark


def test_watch_schedule_hours_days_and_weekdays():
    from datetime import datetime

    from app.core.clipper import watch

    t = datetime(2026, 11, 19, 10, 0)            # a Thursday, 11:00 in Lagos (UTC+1)
    assert watch.next_due({"every": "hours", "n": 6}, t, "Africa/Lagos") == datetime(2026, 11, 19, 16, 0)
    assert watch.next_due({"every": "days", "n": 1, "at": "15:00"}, t, "Africa/Lagos") == datetime(2026, 11, 19, 14, 0)
    assert watch.next_due({"every": "days", "n": 2, "at": "09:00"}, t, "Africa/Lagos") == datetime(2026, 11, 21, 8, 0)
    sunday = watch.next_due({"every": "weekdays", "days": [6], "at": "15:00"}, t, "Africa/Lagos")
    assert sunday == datetime(2026, 11, 22, 14, 0) and sunday.weekday() == 6


def test_value_bar_drops_weak_moments(monkeypatch):
    import asyncio

    from app.core.clipper import editorial
    from app.core.clipper.speech import Unit

    units = [Unit(i=i, w0=i, w1=i + 1, start=i * 10.0, end=i * 10.0 + 10, text=f"s{i}", speech=10.0) for i in range(40)]

    async def fake_round(units_, pool, *, ask, used, **kw):
        free = [u.i for u in pool if u.i not in used]
        return [{"edl": [{"i": i, "role": "point"} for i in free[n * 4:n * 4 + 4]], "score": 90 if n % 2 else 50,
                 "length": 40.0} for n in range(min(ask, len(free) // 4))], {"hashtags": []}

    async def fake_structure(*a, **k):
        return {"body": [[0.0, 100000.0]]}

    monkeypatch.setattr(editorial, "_round", fake_round)
    monkeypatch.setattr(editorial, "map_structure", fake_structure)
    out, _, _ = asyncio.run(editorial.make_shorts(units, count=30, floor=30, target=45, content_type="sermon", title="",
                                                  style="passage", duration=400, min_score=70, refills=4))
    assert out and all(s["score"] >= 70 for s in out)


def test_directions_in_messages_become_links_and_nothing_else_does():
    from app.core.template import _directions

    out = str(_directions("No key is saved. Add it under Settings → AI drafting, then Video Clipper → Channels. <b>x</b>"))
    assert '<a href="/admin/settings#ai" class="underline font-semibold">Settings → AI drafting</a>' in out
    assert 'href="/admin/clipper/channels"' in out
    assert "<b>" not in out and "&lt;b&gt;" in out                       # everything else stays escaped
    assert 'href="/admin/settings#social"' in str(_directions("Admin → Settings → Social publishing"))


# ─── frames ──────────────────────────────────────────────────────────

def test_frames_know_their_shape_and_shorts_get_the_right_one(tmp_path):
    from app.core.clipper import frames

    assert frames.shape_of(1080, 1920) == "vertical" and frames.shape_of(1080, 1080) == "square"
    assert frames.shape_of(1920, 1080) == "landscape" and frames.shape_of(1000, 300) is None
    cfg = frames.parse('{"items":[{"id":"a","format":"vertical","name":"Logo","file":"a.png"},'
                       '{"id":"b","format":"vertical","name":"Border","file":"b.png"},'
                       '{"id":"c","format":"landscape","name":"Wide","file":"c.png"}],'
                       '"default":{"vertical":"a","square":"zzz"}}')
    assert cfg["default"]["square"] is None                                   # unknown ids are dropped
    assert frames.resolve("default", "vertical", cfg)["id"] == "a"
    assert frames.resolve("b", "vertical", cfg)["id"] == "b"
    assert frames.resolve("none", "vertical", cfg) is None
    assert frames.resolve("c", "vertical", cfg)["id"] == "a"                  # another shape's frame: the default
    assert frames.resolve("default", "square", cfg) is None


def test_a_frame_png_needs_a_short_shape_and_a_see_through_middle():
    import io

    from PIL import Image

    from app.core.clipper import frames

    def png(w, h, mode):
        buf = io.BytesIO()
        Image.new(mode, (w, h)).save(buf, "PNG")
        return buf.getvalue()

    assert frames.check_png(png(108, 192, "RGBA"))[2] == "vertical"
    with __import__("pytest").raises(frames.FrameError):
        frames.check_png(png(108, 192, "RGB"))                                # no transparency
    with __import__("pytest").raises(frames.FrameError):
        frames.check_png(png(300, 100, "RGBA"))                               # not a short's shape
    with __import__("pytest").raises(frames.FrameError):
        frames.check_png(b"GIF89a....")


def test_finish_lays_the_frame_and_the_captions_on_in_one_encode(tmp_path):
    import io
    import json
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        import pytest
        pytest.skip("ffmpeg is not installed here")
    from PIL import Image

    from app.core.clipper import render

    master = tmp_path / "7.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=180x320:d=2", "-f", "lavfi",
                    "-i", "sine=f=440:d=2", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(master)], check=True)
    master.with_suffix(".words.json").write_text(json.dumps({"words": [[0.1, 0.6, "Grace"], [0.7, 1.4, "abounds."]],
                                                             "width": 180, "height": 320}))
    frame = tmp_path / "f.png"
    img = Image.new("RGBA", (90, 160), (0, 0, 0, 0))
    for x in range(90):
        img.putpixel((x, 0), (240, 200, 55, 255))
    img.save(frame)
    out = render.finish(master, "pop", frame, "abc")
    assert out.name == "7.fin-pop-abc-none.mp4" and out.stat().st_size > 1000
    assert render.finish(master, None) == master                               # nothing to add: the master itself
    assert render.finish(master, None, frame, "abc").name == "7.fin-none-abc-none.mp4"


# ─── noise reduction and outros ──────────────────────────────────────

def test_noise_reduction_uses_rnnoise_models_that_ship_with_the_app(tmp_path):
    import shutil
    import subprocess

    from app.core.clipper import render
    from app.core.clipper.media import ASSETS

    assert render.denoise_chain("off") == "" and render.denoise_chain(None) == ""
    for level, model in render.DENOISE_MODELS.items():
        assert (ASSETS / model).stat().st_size > 100_000
        assert f"arnndn=m='{(ASSETS / model).as_posix()}'" in render.denoise_chain(level)
    _, g = render.graph([[0, 1]], ["scale=1080:1920"], 30, subtitles=None, denoise="natural")
    assert "arnndn" in g and g.index("arnndn") < g.index("loudnorm")
    if not shutil.which("ffmpeg"):
        return
    out = tmp_path / "o.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=1:a=0.05", "-af",
                    render.denoise_chain("strong").rstrip(","), str(out)], check=True)
    assert out.stat().st_size > 1000


def test_outros_resolve_and_fit_any_shape():
    from app.core.clipper import outros

    cfg = outros.parse('{"items":[{"id":"a","kind":"image","name":"Card","file":"a.png","seconds":3},'
                       '{"id":"b","kind":"video","name":"Sting","file":"b.mp4","seconds":6.5}],"default":"a"}')
    assert outros.resolve("default", cfg)["id"] == "a" and outros.resolve("b", cfg)["id"] == "b"
    assert outros.resolve("none", cfg) is None and outros.resolve("gone", cfg)["id"] == "a"
    assert outros.image_seconds(9) == 4.0 and outros.image_seconds("0.2") == 1.0 and outros.image_seconds("x") == 3.0
    f = outros.fit_filter(1080, 1920, "2:v", "vx")
    assert f.startswith("[2:v]split[vxa][vxb]") and f.endswith("[vx]") and "boxblur" in f


def test_finish_adds_an_image_or_video_outro_after_the_short(tmp_path, monkeypatch):
    import json
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        import pytest
        pytest.skip("ffmpeg is not installed here")
    from PIL import Image

    from app.core.clipper import render
    from app.core.clipper.media import probe

    master = tmp_path / "9.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=180x320:r=30:d=2", "-f", "lavfi",
                    "-i", "sine=f=440:d=2", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(master)], check=True)
    master.with_suffix(".words.json").write_text(json.dumps({"words": [[0.1, 0.6, "Grace"]], "width": 180, "height": 320}))
    card = tmp_path / "card.png"
    Image.new("RGB", (320, 180), (240, 200, 55)).save(card)                   # a landscape card on a vertical short
    out = render.finish(master, "pop", None, None, card, {"id": "c1", "kind": "image", "seconds": 2})
    assert out.name == "9.fin-pop-none-c1.mp4"
    assert abs(probe(out)["duration"] - 4.0) < 0.3                             # 2 s short + 2 s card
    sting = tmp_path / "sting.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=320x180:r=25:d=1.5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(sting)], check=True)          # no sound of its own
    out2 = render.finish(master, None, None, None, sting, {"id": "v1", "kind": "video", "seconds": 1.5, "audio": False})
    info = probe(out2)
    assert abs(info["duration"] - 3.5) < 0.3 and info["width"] == 180 and info["has_audio"]


# ─── background music ────────────────────────────────────────────────

def test_music_choice_random_is_stable_and_start_stays_inside_the_track():
    from app.core.clipper import music

    cfg = music.parse('{"items":[{"id":"a","name":"Piano","file":"a.mp3","seconds":120,"start":30},'
                      '{"id":"b","name":"Pads","file":"b.mp3","seconds":60}],"default":"random"}')
    assert music.resolve("none", cfg, 5) is None
    assert music.resolve("default", cfg, 5) == music.resolve("random", cfg, 5) == music.resolve("random", cfg, 5)
    picks = {music.resolve("random", cfg, n)["id"] for n in range(20)}
    assert picks == {"a", "b"}                                          # random spreads over the library
    assert music.resolve("gone", cfg, 5) == music.resolve("random", cfg, 5)  # a deleted track: the default
    assert music.start_of(cfg["items"][0]) == 30 and music.start_of({"seconds": 20, "start": 99}) == 15
    assert music.cache_key(cfg["items"][0]) == "as30"
    assert music.parse('{"items":[],"default":"random"}')["default"] is None


def test_music_sits_far_under_the_voice_and_ducks_while_he_speaks(tmp_path):
    import shutil
    import subprocess
    import wave

    import numpy as np

    if not shutil.which("ffmpeg"):
        import pytest
        pytest.skip("ffmpeg is not installed here")
    from app.core.clipper import render

    master = tmp_path / "3.mp4"
    # "Speech": a 300 Hz voice at -14 LUFS-ish level, on for 1 s, off for 1 s, for 8 s.
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=180x320:r=25:d=8",
                    "-f", "lavfi", "-i", "sine=f=300:d=8,volume='if(lt(mod(t,2),1),0.5,0)':eval=frame",
                    "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(master)], check=True)
    track = tmp_path / "bed.wav"                                    # loud "music": a 1500 Hz tone at full scale
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=1500:d=20", "-af", "volume=0.9",
                    str(track)], check=True)
    out = render.finish(master, None, music_path=track, music_item={"id": "t", "seconds": 20, "start": 0})
    wav = tmp_path / "mix.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(out), "-ac", "1", "-ar", "16000", str(wav)], check=True)
    w = wave.open(str(wav))
    x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float64)

    def band(seg, f):
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        k = int(round(f * len(seg) / 16000))
        return 20 * np.log10(spec[k - 3:k + 4].max() + 1e-9)

    talk = [x[int(s * 16000):int((s + 0.5) * 16000)] for s in (2.3, 4.3, 6.3)]     # middle of speech bursts
    rest = [x[int(s * 16000):int((s + 0.5) * 16000)] for s in (3.3, 5.3)]          # middle of pauses
    under = np.mean([band(t, 300) - band(t, 1500) for t in talk])
    assert under >= 20, f"music only {under:.1f} dB under the voice"
    duck = np.mean([band(r, 1500) for r in rest]) - np.mean([band(t, 1500) for t in talk])
    assert duck >= 6, f"music dipped only {duck:.1f} dB while he spoke"


# ─── effects: light transitions, smart zoom, finishing touches ───────

def test_light_transitions_only_on_new_points_and_never_overused():
    from app.core.clipper import render

    # 40 s short: pause cuts (small gaps) and two real jumps (skips of 30 s and 50 s), one back-jump (a cold open's return).
    segs = [[100, 104], [100.0 + 4.3, 110], [140, 146], [146.4, 152], [200, 206], [100, 108], [108.3, 115]]
    at = render.transition_points(segs)
    assert 1 <= len(at) <= render.FLASH_MAX and len(at) <= int(sum(e - s for s, e in segs) // render.FLASH_EVERY) or 1
    assert all(b - a >= render.FLASH_SPACING for a, b in zip(at, at[1:]))
    starts = [sum(e - s for s, e in segs[:k]) for k in range(len(segs))]
    jumps = {round(starts[k], 3) for k in (2, 4, 5)}                         # the joins that really jump
    assert set(at) <= jumps and round(starts[5], 3) in at                     # the return from the teaser comes first
    assert render.transition_points([[0, 10], [10.3, 20]]) == []              # pause cuts never flash
    assert render.flash_filter([]) == "" and "eval=frame" in render.flash_filter([3.2])


def test_smart_zoom_hides_jump_cuts_and_pushes_in_on_the_payoff():
    from app.core.clipper import render

    segs = [[0, 3], [3.4, 7], [7.5, 11], [11.4, 16], [50, 56]]               # pause cuts, then a new point
    plan = render.zoom_plan(segs, emphasis=[(50, 56)])
    assert plan[0] == (1.0, 1.0)
    assert any(z0 == render.PUNCH for z0, _ in plan[1:4])                    # a punch-in on a jump cut
    assert plan[4][0] == 1.0 and plan[4][1] > 1.0                            # new point starts wide, then pushes in
    changes = sum(1 for a, b in zip(plan, plan[1:]) if a[1] != b[0]) + sum(1 for z0, z1 in plan if z1 != z0)
    assert changes <= max(1, int(sum(e - s for s, e in segs) // render.ZOOM_EVERY)) + 1
    assert render.zoom_filter(1.0, 1.0, 2, 1080, 1920) == ""
    assert "eval=frame" in render.zoom_filter(1.0, 1.07, 3, 1080, 1920)


def test_scripture_is_recognised_where_it_is_read():
    from app.core.clipper import render

    assert render.scripture_like("Turn with me to John 3:16") and render.scripture_like("Romans chapter 8 verse 28")
    assert render.scripture_like("the Bible says do not fear") and not render.scripture_like("John came to church")
    words = [[0.2, 0.6, "Turn"], [0.7, 1.0, "to"], [1.1, 1.5, "Psalm"], [1.6, 2.0, "23"], [4.0, 4.5, "Amen"]]
    assert render.scripture_segments([[10, 13], [20, 23]], words) == [0]


def test_a_short_renders_with_every_effect_switched_on(tmp_path):
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        import pytest
        pytest.skip("ffmpeg is not installed here")
    from app.core.clipper import render
    from app.core.clipper.media import probe

    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:r=25:d=60", "-f", "lavfi",
                    "-i", "sine=f=220:d=60", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)],
                   check=True)
    segs = [[1, 5], [5.4, 9], [9.4, 13], [30, 36], [36.4, 40]]
    words = [[0.5, 1.0, "Turn"], [1.1, 1.4, "to"], [1.5, 2.0, "John"], [2.1, 2.5, "3"], [14.0, 14.5, "Amen."]]
    out = tmp_path / "short.mp4"
    res = render.render(src=src, info={"width": 320, "height": 180, "fps": 25, "has_video": True}, segments=segs,
                        words=words, out=out, fmt="vertical", quality=720, framing="centre", caption_style="none",
                        denoise="natural", flashes=True, zooms=True, emphasis=[(30, 40)], touches=True)
    info = probe(out)
    assert abs(info["duration"] - sum(e - s for s, e in segs)) < 0.4 and info["width"] == 720
    fx = " · ".join(res["effects"])
    assert "light transition + whoosh" in fx and "fade out" in fx and "sharpen" in fx and "scripture glow" in fx



def test_only_the_sermon_is_clipped(monkeypatch):
    import asyncio

    import pytest

    from app.core.clipper import editorial
    from app.core.clipper.speech import Unit

    units = [Unit(i=i, w0=i, w1=i + 1, start=i * 10.0, end=i * 10.0 + 10, text=f"s{i}", speech=10.0) for i in range(60)]

    async def no_sermon(*a, **k):
        return {"body": []}

    monkeypatch.setattr(editorial, "map_structure", no_sermon)
    with pytest.raises(editorial.EditorialError):
        asyncio.run(editorial.make_shorts(units, count=3, floor=30, target=45, content_type="sermon", title="",
                                          style="passage", duration=600))

    async def sermon(*a, **k):                    # the sermon runs 200-500 s; before it the moderator, after it songs
        return {"body": [[200.0, 500.0]]}

    seen_used = []

    async def greedy_round(units_, pool, *, ask, used, **kw):
        seen_used.append(set(used))
        assert all(200 <= u.start and u.end <= 500 for u in pool)            # the editor only sees the sermon
        # A misbehaving round tries to sneak in a moderator line (id 5) next to sermon lines.
        return [{"edl": [{"i": 5, "role": "hook"}, {"i": 25, "role": "point"}], "score": 90, "length": 40.0},
                {"edl": [{"i": i, "role": "point"} for i in (30, 31, 32, 33)], "score": 80, "length": 40.0}], {}

    monkeypatch.setattr(editorial, "map_structure", sermon)
    monkeypatch.setattr(editorial, "_round", greedy_round)
    out, _, _ = asyncio.run(editorial.make_shorts(units, count=2, floor=30, target=45, content_type="sermon", title="",
                                                  style="passage", duration=600))
    assert {5, 0, 59} <= seen_used[0]                                         # outside lines are locked out
    assert out and all(all(20 <= i < 50 for i in s["ids"]) for s in out)      # the short with line 5 was dropped


def test_sermon_edges_are_read_sentence_by_sentence(monkeypatch):
    import asyncio

    from app.core.clipper import editorial
    from app.core.clipper.speech import Unit

    units = [Unit(i=i, w0=i, w1=i + 1, start=i * 10.0, end=i * 10.0 + 9, text=f"s{i}", speech=9.0) for i in range(100)]
    asked = []

    async def fake_ask(prompt, **kw):
        asked.append(prompt)
        return {"first": 23, "last": 71, "why": "moderator hands over at 3:50; closing hymn after 11:59"}

    monkeypatch.setattr(editorial, "_ask", fake_ask)
    body = asyncio.run(editorial.refine_sermon(units, [[200.0, 700.0]]))     # the digest guessed 200-700 s
    assert body == [[230.0, 719.0]]
    assert "[23]" in asked[0] and "[71]" in asked[0]

    async def unsure(prompt, **kw):
        return {"first": None, "last": 999}

    monkeypatch.setattr(editorial, "_ask", unsure)
    assert asyncio.run(editorial.refine_sermon(units, [[200.0, 700.0]])) == [[200.0, 700.0]]   # unsure: unchanged



# ─── only the senior pastor (voices) ─────────────────────────────────

def _voice(seed, n=6, spread=0.25):
    import numpy as np

    rng = np.random.default_rng(seed)
    centre = rng.normal(size=256)
    centre /= np.linalg.norm(centre)
    out = []
    for _ in range(n):
        v = centre + rng.normal(size=256) * spread / 16
        out.append((v / np.linalg.norm(v)).tolist())
    return centre, out


def test_the_senior_pastor_is_told_apart_from_a_pastor_who_sounds_like_him():
    import numpy as np

    from app.core.clipper import speakers

    him, s1 = _voice(1)
    _, s2 = _voice(1, spread=0.3)
    # A pastor who has learned him: close (cosine ~0.6) but his own voice.
    rng = np.random.default_rng(9)
    alike = him * 0.6 + rng.normal(size=256) / 16 * 0.8
    alike /= np.linalg.norm(alike)
    imit = [((alike + rng.normal(size=256) / 64) / np.linalg.norm(alike + rng.normal(size=256) / 64)).tolist() for _ in range(6)]
    cfg = speakers.parse(json_dump({"mode": "senior", "people": [
        {"id": "a", "name": "Pastor Samuel", "role": "senior", "samples": [{"id": "x", "embs": s1}, {"id": "y", "embs": s2}]},
        {"id": "b", "name": "Pastor B", "role": "other", "samples": [{"id": "z", "embs": imit}]}]}))
    cal = speakers.calibrate(cfg)
    assert cal["accept"] is not None and cal["accept"] >= speakers.MIN_ACCEPT
    # A sermon: him for 3 minutes, the look-alike for 3 minutes, him again for 2.
    def w(v):
        x = np.asarray(v) + rng.normal(size=256) / 40
        return x / np.linalg.norm(x)
    windows = ([(t, w(him)) for t in np.arange(0, 180, 15)] + [(t, w(alike)) for t in np.arange(180, 360, 15)]
               + [(t, w(him)) for t in np.arange(360, 480, 15)] + [(480.0, None)])
    verdicts = speakers.judge(windows, cfg, cal["accept"])
    ranges = speakers.his_ranges(verdicts, [[0, 495]])
    assert ranges and ranges[0][0] == 0 and 165 <= ranges[0][1] <= 195
    assert all(not (200 <= a < 340) for a, _ in ranges)                       # the look-alike's part is left out
    assert any(a >= 345 for a, _ in ranges)                                   # he is picked up again after him
    assert 0.5 <= speakers.share(verdicts) <= 0.7


def test_voice_setup_needs_two_samples_and_falls_back_safely():
    from app.core.clipper import speakers

    _, s1 = _voice(3)
    one = speakers.parse(json_dump({"mode": "senior", "people": [{"id": "a", "name": "P", "role": "senior",
                                                                  "samples": [{"id": "x", "embs": s1}]}]}))
    assert speakers.calibrate(one)["accept"] is None and speakers.calibrate(one)["strength"] == "missing"
    assert speakers.parse("{nonsense")["mode"] == "all"


def test_fbank_matches_the_kaldi_layout():
    import numpy as np

    from app.core.clipper import voice

    x = np.sin(2 * np.pi * 440 * np.arange(16000) / 16000).astype(np.float32) * 0.3
    f = voice.fbank(x)
    assert f.shape == (98, 80) and abs(float(f.mean())) < 1e-3              # 25 ms / 10 ms frames, mean removed
    low = voice.fbank(np.concatenate([x, np.zeros(16000, np.float32)]))     # tone then silence: bands differ over time
    assert float(low[:90, 10:20].mean()) > float(low[110:, 10:20].mean())    # 440 Hz energy sits in the low mel bands


def json_dump(x):
    import json

    return json.dumps(x)


# ─── ranking: the shorts of one message, best first ─────────────────────

def test_ranking_puts_the_rankers_order_first_and_scores_follow_it():
    shorts = [{"title": "a", "score": 90}, {"title": "b", "score": 50}, {"title": "c", "score": 70}, {"title": "d", "score": 65}]
    out = editorial.apply_ranking(shorts, [
        {"k": 1, "score": 88, "why": "Bold opening question"},
        {"k": 0, "score": 88, "why": "tie must be broken"},
        {"k": 1, "score": 10},                 # a repeat is ignored
        {"k": 9, "score": 99},                 # out of range is ignored
        "junk",
        {"k": 2, "why": "no score: keeps its own"},
    ])
    assert [s["title"] for s in out] == ["b", "a", "c", "d"]          # d was left out: follows, own score
    scores = [s["score"] for s in out[:3]]
    assert scores == sorted(scores, reverse=True) and len(set(scores)) == 3
    assert out[0]["why"] == "Bold opening question"
    assert out[3]["score"] == 65 and "why" not in out[3]


def test_without_a_ranking_the_editors_own_scores_order_the_shorts():
    out = editorial.apply_ranking([{"score": 40}, {"score": 80}, {"score": None}], None)
    assert [s["score"] for s in out] == [80, 40, None]


def test_the_ranker_sees_every_short_with_its_teaser_first():
    units = speech.build_units(_words("One thing. Two things here. Three is the last. Four comes after."), [])
    shorts = [{"edl": [{"i": 0}, {"i": 1}], "cold_open": 3, "length": 20, "title": "First", "value": "v"},
              {"edl": [{"i": 2}], "cold_open": None, "length": 9, "title": "Second", "value": ""}]
    text = editorial.rank_prompt(units, shorts, brief="Niche: faith")
    assert "SHORT 0" in text and "SHORT 1" in text and "Niche: faith" in text
    assert text.index("Four comes after") < text.index("One thing")      # the cold open plays first


def test_saved_shorts_become_ranker_input():
    from types import SimpleNamespace as NS

    from app.core.clipper.pipeline import ranking_input

    s1 = NS(id=1, plan=[{"ids": [7], "cold_open": True}, {"ids": [3, 4]}, {"ids": [9], "spare": True}],
            length=30, title="t", value=None, score=70)
    s2 = NS(id=2, plan=[], length=5, title="empty", value="", score=None)
    out = ranking_input([s1, s2])
    assert len(out) == 1 and out[0]["id"] == 1
    assert out[0]["edl"] == [{"i": 3}, {"i": 4}] and out[0]["cold_open"] == 7


# ─── watermark: the ministry logo in a corner ──────────────────────────

def test_a_new_church_gets_the_ministry_logo_top_right():
    from app.core.clipper import watermark

    cfg = watermark.parse(None)
    assert cfg["default"] == watermark.MINISTRY and cfg["position"] == "top-right"
    assert watermark.resolve("default", cfg)["id"] == watermark.MINISTRY
    assert watermark.ministry_path().exists()                       # the site's own logo ships with the app


def test_watermark_choices_none_and_cleaning():
    import json

    from app.core.clipper import watermark

    cfg = watermark.parse(json.dumps({"items": [{"id": "abc", "file": "abc.png", "name": "Youth"}], "default": None,
                                      "position": "middle", "size": 99, "opacity": 3, "tone": "neon"}))
    assert cfg["default"] is None and watermark.resolve("default", cfg) is None      # the church chose none
    assert watermark.resolve("none", cfg) is None
    assert watermark.resolve("abc", cfg)["file"] == "abc.png"                         # a short can still pick one
    assert watermark.resolve(watermark.MINISTRY, cfg)["id"] == watermark.MINISTRY
    assert watermark.resolve("deleted", cfg) is None
    assert (cfg["position"], cfg["size"], cfg["opacity"], cfg["tone"]) == ("top-right", 30, 20, "original")
    assert [m["id"] for m in watermark.choices(cfg)] == [watermark.MINISTRY, "abc"]
    gone = watermark.parse(json.dumps({"items": [], "default": "abc"}))
    assert gone["default"] is None                                                   # a default that was deleted


def test_watermark_placement_stays_inside_the_short():
    from app.core.clipper import watermark

    cfg = {**watermark.DEFAULTS, "size": 20}
    x, y, w, h = watermark.placement(cfg, 1080, 1920, 2102, 2102)
    assert (w, h) == (216, 216) and x == 1080 - 216 - 43 and y == 43
    x, y, w, h = watermark.placement({**cfg, "position": "bottom-left"}, 1080, 1920, 400, 100)
    assert x == 43 and y + h == 1920 - 43 and w % 2 == 0 and h % 2 == 0
    _x, _y, w, h = watermark.placement({**cfg, "size": 30}, 1920, 1080, 100, 400)   # a tall logo: capped at 1/4
    assert h <= 270
    lines = watermark.overlay_filters({**cfg, "tone": "white", "opacity": 60}, "1:v", "vf", "vw", 1080, 1920, 10, 10)
    assert "aa=0.60" in lines[0] and "lutrgb" in lines[0] and lines[1].endswith("[vw]")
    assert watermark.cache_key(cfg, None) is None
    assert watermark.cache_key(cfg, {"id": "m"}) != watermark.cache_key({**cfg, "size": 21}, {"id": "m"})


def test_the_watermark_is_burned_into_the_finished_copy(tmp_path):
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        import pytest
        pytest.skip("ffmpeg is not installed here")
    from PIL import Image

    from app.core.clipper import render, watermark

    master = tmp_path / "5.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=360x640:r=25:d=2",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(master)], check=True)
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (200, 200), (255, 255, 255, 255)).save(logo)
    cfg = {**watermark.DEFAULTS, "opacity": 100}
    out = render.finish(master, None, mark_path=logo, mark_cfg=cfg, mark_key=watermark.cache_key(cfg, {"id": "x"}))
    assert out != master and out.exists()
    still = tmp_path / "still.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", str(out), "-frames:v", "1", str(still)], check=True)
    img = Image.open(still).convert("L")
    x, y, w, h = watermark.placement(cfg, 360, 640, 200, 200)
    assert img.getpixel((x + w // 2, y + h // 2)) > 200          # white logo in the top-right corner
    assert img.getpixel((20, 600)) < 40                           # the rest of the picture untouched


def test_glow_is_a_soft_halo_under_sharp_letters():
    from app.core.clipper import captions

    words = [[0.0, 0.4, "Grace"], [0.45, 0.8, "is"], [0.85, 1.3, "enough."]]
    text = captions.ass(words, style="glow", width=1080, height=1920)
    halo = [l for l in text.splitlines() if l.startswith("Dialogue: 0,")]
    sharp = [l for l in text.splitlines() if l.startswith("Dialogue: 1,")]
    assert halo and len(halo) == len(sharp)                      # every moment has both layers
    assert all(r"\1a&HFF&" in l and r"\blur9" in l for l in halo)   # the halo: no letters, only the blurred glow
    assert all(r"\blur0" in l and r"\1a&HFF&" not in l for l in sharp)   # the letters: never blurred
    assert all(r"\fscx116" in l for l in halo + sharp)           # the spoken word grows on both, so they line up
