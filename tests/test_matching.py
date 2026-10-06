import json
import os
import unittest
from unittest.mock import patch

import downloader as dl

LABELS = os.path.join(os.path.dirname(__file__), "fixtures", "mv_labels.json")


def entry(title, channel="", duration=None, url=None, verified=None):
    return {"url": url or "u:" + title, "title": title, "channel": channel,
            "duration": duration, "channel_is_verified": verified}


class LabelTests(unittest.TestCase):
    """人工标注集:库里对的 MV 必须继续通过内容检查,标注为错的必须被否决或排到原版之后。"""

    def test_labelled_videos(self):
        with open(LABELS, encoding="utf-8") as f:
            labels = json.load(f)
        for label in labels:
            q = dl.SongQuery(label["title"], label["artist"])
            for v in label["good"]:
                if v["title"]:
                    with self.subTest(good=v["title"]):
                        self.assertEqual(dl.content_verdict(v["title"], q)[0], "ok")
                        self.assertFalse(dl.variant_words(v["title"], q))
            for v in label["bad"]:
                with self.subTest(bad=v["title"]):
                    rejected = dl.content_verdict(v["title"], q)[0] == "reject"
                    score = dl.score_entry({"title": v["title"], "channel": v["channel"],
                                            "duration": v.get("duration")}, q)
                    self.assertTrue(rejected or score < dl.ACCEPT_THRESHOLD, (v["title"], score))


class TitleTests(unittest.TestCase):
    def test_suffix_forms(self):
        self.assertIn(("Bad", 0), dl.title_forms("Bad (2012 Remaster)"))
        self.assertIn(("Seve", 0), dl.title_forms("Seve (Radio Edit)"))
        self.assertIn(("Happy", 0), dl.title_forms('Happy (From "Despicable Me 2")'))
        self.assertIn(("Cupid", dl.VARIANT_PENALTY), dl.title_forms("Cupid (Twin Version)"))
        self.assertNotIn("Cupid", [f for f, _ in dl.title_forms("Cupid (Twin Version)", allow_variant=False)])

    def test_whole_word_and_cjk_matching(self):
        self.assertTrue(dl._contains_phrase("Michael Jackson - Bad (Shortened Version)", "Bad"))
        self.assertFalse(dl._contains_phrase("Billie Eilish - bad guy", "Bad Guys"))
        self.assertFalse(dl._contains_phrase("Badlands (Official Video)", "Bad"))
        self.assertTrue(dl._contains_phrase("YOASOBI「群青」Official Music Video", "群青"))
        self.assertTrue(dl._contains_phrase("Charlie Puth - We Don’t Talk Anymore", "We Don't Talk Anymore"))

    def test_alias_from_catalog(self):
        q = dl.SongQuery("Gunjou", "YOASOBI", {"titles": ["群青"], "duration": 248})
        e = entry("YOASOBI「群青」Official Music Video", "YOASOBI and Echoes", 263, verified=True)
        self.assertGreaterEqual(dl.score_entry(e, q), dl.ACCEPT_THRESHOLD)
        self.assertEqual(dl.score_entry(e, dl.SongQuery("Gunjou", "YOASOBI")), dl.REJECT)

    def test_junk_and_variants(self):
        q = dl.SongQuery("All My Fellas", "Frizk")
        self.assertEqual(dl.content_verdict("How I made ALL MY FELLAS", q)[0], "reject")
        q = dl.SongQuery("Life Goes On", "Oliver Tree")
        acoustic = dl.score_entry(entry("Oliver Tree - Life Goes On (Acoustic) [Official Music Video]", "Oliver Tree"), q)
        original = dl.score_entry(entry("Oliver Tree - Life Goes On [Official Music Video]", "Oliver Tree"), q)
        self.assertGreater(original, acoustic)
        self.assertLess(acoustic, dl.ACCEPT_THRESHOLD)
        # 请求的就是 Acoustic 版时不扣分
        q = dl.SongQuery("Life Goes On (Acoustic)", "Oliver Tree")
        self.assertGreaterEqual(dl.score_entry(entry(
            "Oliver Tree - Life Goes On (Acoustic) [Official Music Video]", "Oliver Tree"), q), dl.ACCEPT_THRESHOLD)


class VersionTests(unittest.TestCase):
    def test_extra_featured_artist_means_other_version(self):
        q = dl.SongQuery("Life Goes On", "Oliver Tree")
        remix = dl.score_entry(entry("Oliver Tree - Life Goes On feat. Trippie Redd & Ski Mask (Official Music Video)",
                                     "Lyrical Lemonade", verified=True), q)
        original = dl.score_entry(entry("Oliver Tree - Life Goes On [Official Music Video]", "Oliver Tree", verified=True), q)
        self.assertGreater(original, remix)
        q = dl.SongQuery("Peaches (feat. Daniel Caesar & GIVĒON)", "Justin Bieber")
        self.assertEqual(dl.extra_feats("Justin Bieber - Peaches ft. Daniel Caesar, Giveon", q), [])

    def test_other_artists_mv_only_goes_to_review(self):
        q = dl.SongQuery("Toosie Slide", "Dance Fruits Music & Steve Void")
        drake = entry("Drake - Toosie Slide (Official Music Video)", "Drake", 250, verified=True)
        with patch.object(dl, "_search", return_value=[drake]), \
                patch.object(dl, "deep_check", return_value={"bonus": 9, "artist": False,
                                                             "channel_id": None, "verified": True}):
            ranked = dl.search_mv(q, ["youtube"])
        self.assertLess(ranked[0][0], dl.ACCEPT_THRESHOLD)
        self.assertGreaterEqual(ranked[0][0], dl.REVIEW_MIN)


class ChannelAndDurationTests(unittest.TestCase):
    def test_fan_channel_scores_below_artist_channel(self):
        q = dl.SongQuery("Chicago", "Michael Jackson")
        title = "Michael Jackson - Chicago (Official Video)"
        self.assertGreater(dl.score_entry(entry(title, "Michael Jackson"), q),
                           dl.score_entry(entry(title, "Michael Jackson Fan France"), q) + 6)

    def test_duration_against_reference(self):
        q = dl.SongQuery("Bad", "Michael Jackson", {"duration": 247})
        short = dl.score_entry(entry("Michael Jackson - Bad (Shortened Version)", "Michael Jackson", 260), q)
        film = dl.score_entry(entry("Michael Jackson - Bad (Official Video)", "Michael Jackson", 1086), q)
        clip = dl.score_entry(entry("Michael Jackson - Bad (Official Video)", "Michael Jackson", 60), q)
        # 正常长度的 MV 必须胜过十几分钟的电影版和一分钟的片段
        self.assertGreater(short, film)
        self.assertGreater(short, clip)
        self.assertLess(film, dl.ACCEPT_THRESHOLD)


class SearchTests(unittest.TestCase):
    def test_deep_check_cannot_rescue_wrong_song(self):
        q = dl.SongQuery("East of Eden", "Zella Day")
        wrong = entry("Zella Day - Hypnotic (Official Video)", "Zella Day")
        with patch.object(dl, "_search", return_value=[wrong]), \
                patch.object(dl, "deep_check", return_value={"bonus": 9, "artist": True,
                                                             "channel_id": None, "verified": True}):
            self.assertEqual(dl.search_mv(q, ["youtube"]), [])

    def test_known_channel_searched_first(self):
        q = dl.SongQuery("Bad (2012 Remaster)", "Michael Jackson", {"duration": 247})
        hit = {"url": "https://www.youtube.com/watch?v=dsUXAEzaC3Q",
               "title": "Michael Jackson - Bad (Shortened Version)", "channel": None,
               "duration": 260, "channel_is_verified": True}
        with patch.object(dl, "channel_search", return_value=[hit]) as chan, \
                patch.object(dl, "_search", return_value=[]) as search:
            ranked = dl.search_mv(q, ["youtube"], channels=[("UC1", "Michael Jackson")])
        chan.assert_called()
        search.assert_not_called()
        self.assertEqual(ranked[0][1]["url"], hit["url"])
        self.assertGreaterEqual(ranked[0][0], dl.ACCEPT_THRESHOLD)

    def test_cache_replay(self):
        import tempfile
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        with patch.object(dl, "CACHE_DIR", folder.name), \
                patch.object(dl, "_run", return_value=type("P", (), {
                    "returncode": 0, "stderr": "",
                    "stdout": json.dumps({"url": "u1", "title": "t"})})()) as run:
            dl.CACHE_MODE = "record"
            self.assertEqual(dl._search("youtube", "x")[0]["url"], "u1")
            dl.CACHE_MODE = "replay"
            try:
                self.assertEqual(dl._search("youtube", "x")[0]["url"], "u1")
                self.assertEqual(dl._search("youtube", "never searched"), [])
            finally:
                dl.CACHE_MODE = "record"
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
