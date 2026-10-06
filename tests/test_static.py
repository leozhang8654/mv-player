import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import app
import downloader as dl


def make_video(path, src):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src,
                    "-f", "lavfi", "-i", "sine=d=40", "-t", "40", "-shortest", path],
                   check=True, capture_output=True)


def read(path):
    with open(path) as f:
        return f.read()


class StaticVideoTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def test_detects_still_image_but_not_moving_video(self):
        still = os.path.join(self.folder.name, "still.mp4")
        moving = os.path.join(self.folder.name, "moving.mp4")
        make_video(still, "color=c=red:s=320x180:d=40")
        make_video(moving, "testsrc2=s=320x180:d=40")
        self.assertTrue(dl.is_static_video(still))
        self.assertFalse(dl.is_static_video(moving))

    def test_find_mv_skips_excluded_urls(self):
        entries = [{"url": "u1", "title": "A - Song (Official Video)", "channel": "A", "duration": 200},
                   {"url": "u2", "title": "A - Song (Official MV)", "channel": "A", "duration": 200}]
        with patch.object(dl, "_search", return_value=entries):
            best = dl.find_mv("Song", "A", ["youtube"], exclude={"u1"})
        self.assertEqual(best[1]["url"], "u2")


def cand(score, url, title="A - Song (Official Video)"):
    return (score, {"url": url, "title": title, "channel": "A"}, "youtube")


class ProcessSongTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.patches = [patch.object(app, "MEDIA_DIR", self.folder.name),
                        patch.object(app, "THUMB_DIR", self.folder.name),
                        patch.object(app, "SUBS_DIR", self.folder.name),
                        patch.object(app, "save_state", lambda: None),
                        patch.object(app, "collect_subs", lambda _id: []),
                        patch.object(app, "make_thumb", lambda *a: True),
                        patch.object(app, "measure_loudness", lambda *a: -10.0),
                        patch.object(app.catalog, "lookup", return_value={}),
                        patch.object(app, "known_channels", return_value=[])]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def fake_download(self, url, media_dir, song_id, cb=None):
        path = os.path.join(media_dir, song_id + ".mp4")
        with open(path, "w") as f:
            f.write(url)
        return path

    def song(self, **kw):
        s = {"id": "abc", "title": "Song", "artist": "A", "status": "searching", "forced": False}
        s.update(kw)
        app.state = {"playlists": [], "songs": [s]}
        return s

    def old_file(self):
        path = os.path.join(self.folder.name, "Song - A.mp4")
        with open(path, "w") as f:
            f.write("old")
        return path

    def run_song(self, s, ranked, static=lambda p: False):
        with patch.object(app.downloader, "search_mv", return_value=ranked), \
                patch.object(app.downloader, "download", side_effect=self.fake_download) as dl, \
                patch.object(app.downloader, "is_static_video", side_effect=static):
            app.process_song(s)
        return dl

    def test_static_candidate_rejected_and_next_one_used(self):
        s = self.song()
        self.run_song(s, [cand(10, "still"), cand(9, "moving")], static=lambda p: read(p) == "still")
        self.assertEqual(s["status"], "done")
        self.assertEqual(s["video_url"], "moving")
        self.assertEqual(s["rejected_urls"], ["still"])
        self.assertFalse(s["static"])

    def test_only_static_candidates_gives_no_mv(self):
        s = self.song()
        self.run_song(s, [cand(10, "still")], static=lambda p: True)
        self.assertEqual(s["status"], "no_mv")
        self.assertFalse(os.listdir(self.folder.name))

    def test_existing_static_file_deleted_when_no_better_mv(self):
        old = self.old_file()
        s = self.song(status="pending", video_file="Song - A.mp4", video_url="old-url",
                      static=True, rejected_urls=["old-url"])
        self.run_song(s, [])
        self.assertEqual(s["status"], "no_mv")
        self.assertIsNone(s["video_file"])
        self.assertFalse(os.path.exists(old))

    def test_manual_link_accepted_even_if_static(self):
        s = self.song(forced=True, video_url="manual-url")
        with patch.object(app.downloader, "download", side_effect=self.fake_download), \
                patch.object(app.downloader, "is_static_video", return_value=True) as check:
            app.process_song(s)
        self.assertEqual(s["status"], "done")
        check.assert_not_called()

    def test_uncertain_candidates_go_to_review(self):
        s = self.song()
        dl = self.run_song(s, [cand(6, "u1"), cand(4, "u2"), cand(1, "u3")])
        dl.assert_not_called()
        self.assertEqual(s["status"], "review")
        self.assertEqual([c["url"] for c in s["candidates"]], ["u1", "u2"])

    def test_recheck_keeps_current_file_when_it_is_still_the_pick(self):
        self.old_file()
        s = self.song(status="pending", video_file="Song - A.mp4", video_url="cur")
        dl = self.run_song(s, [cand(12, "cur")])
        dl.assert_not_called()
        self.assertEqual(s["status"], "done")
        self.assertEqual(s["video_file"], "Song - A.mp4")

    def test_recheck_replaces_variant_with_better_original(self):
        old = self.old_file()
        s = self.song(status="pending", video_file="Song - A.mp4", video_url="acoustic")
        self.run_song(s, [cand(14, "original"), cand(7, "acoustic")])
        self.assertEqual(s["status"], "done")
        self.assertEqual(s["video_url"], "original")
        self.assertEqual(read(os.path.join(self.folder.name, s["video_file"])), "original")
        self.assertFalse(os.path.exists(old) and read(old) == "old")

    def test_failed_recheck_keeps_old_video_playable(self):
        self.old_file()
        s = self.song(status="pending", video_file="Song - A.mp4", video_url="acoustic",
                      video_title="A - Song (Acoustic)")
        with patch.object(app.downloader, "search_mv", return_value=[cand(14, "original")]), \
                patch.object(app.downloader, "download",
                             side_effect=app.downloader.DownloadError("Sign in to confirm you're not a bot")):
            app.process_song(s)
        self.assertEqual((s["status"], s["video_url"], s["video_title"]), ("done", "acoustic", "A - Song (Acoustic)"))
        self.assertTrue(s["recheck"] and s["auto_retry_at"])
        s["auto_retry_at"] = 1
        app.requeue_due_retries()
        self.assertEqual(s["status"], "pending")
        app.cooldown_until = 0

    def test_choose_candidate_downloads_it_as_manual(self):
        s = self.song(status="review", candidates=[{"url": "https://www.youtube.com/watch?v=x", "title": "T"}])
        client = app.app.test_client()
        self.assertEqual(client.post("/api/songs/abc/choose", json={"url": "https://evil.example"}).status_code, 400)
        self.assertEqual(client.post("/api/songs/abc/choose", json={"url": "https://www.youtube.com/watch?v=x"}).status_code, 200)
        self.assertEqual((s["status"], s["forced"], s["video_url"]), ("pending", True, "https://www.youtube.com/watch?v=x"))

    def test_choose_none_marks_no_mv(self):
        s = self.song(status="review", candidates=[{"url": "u"}])
        app.app.test_client().post("/api/songs/abc/choose", json={"url": None})
        self.assertEqual(s["status"], "no_mv")


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        for p in (patch.object(app, "MEDIA_DIR", self.folder.name),
                  patch.object(app, "THUMB_DIR", self.folder.name),
                  patch.object(app, "SUBS_DIR", self.folder.name),
                  patch.object(app, "save_state", lambda: None),
                  patch.object(app.catalog, "lookup", return_value={})):
            p.start()
            self.addCleanup(p.stop)

    def test_audit_requeues_wrong_variant_and_missing(self):
        path = os.path.join(self.folder.name, "wrong.mp4")
        with open(path, "w") as f:
            f.write("x")
        songs = [
            {"id": "w", "artist": "Zella Day", "title": "East of Eden", "status": "done",
             "video_file": "wrong.mp4", "video_url": "u-wrong", "video_title": "Zella Day - Hypnotic (Official Video)"},
            {"id": "v", "artist": "Oliver Tree", "title": "Life Goes On", "status": "done", "video_file": "v.mp4",
             "video_url": "u-ac", "video_title": "Oliver Tree - Life Goes On (Acoustic) [Official Music Video]"},
            {"id": "ok", "artist": "M83", "title": "Midnight City", "status": "done", "video_file": "m.mp4",
             "video_url": "u-ok", "video_title": "M83 'Midnight City' Official video"},
            {"id": "n", "artist": "YOASOBI", "title": "Gunjou", "status": "no_mv"},
            {"id": "f", "artist": "X", "title": "Y", "status": "done", "forced": True,
             "video_url": "u-f", "video_title": "unrelated"},
        ]
        app.state = {"playlists": [], "songs": songs}
        app.audit_library()
        by = {s["id"]: s for s in songs}
        self.assertEqual(by["w"]["status"], "pending")
        self.assertIsNone(by["w"]["video_file"])
        self.assertFalse(os.path.exists(path))
        self.assertIn("u-wrong", by["w"]["rejected_urls"])
        self.assertEqual((by["v"]["status"], by["v"]["video_file"]), ("pending", "v.mp4"))
        self.assertEqual(by["ok"]["status"], "done")
        self.assertEqual(by["n"]["status"], "pending")
        self.assertEqual(by["f"]["status"], "done")
        app.audit_library()  # 每个版本只跑一次
        self.assertEqual(by["ok"]["status"], "done")


if __name__ == "__main__":
    unittest.main()
