import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import app
import downloader as dl


URL = "https://www.youtube.com/watch?v=DYptgVvkVLQ"


def result(code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        settings = patch.object(dl, "SETTINGS_FILE", os.path.join(self.folder.name, "settings.json"))
        settings.start()
        self.addCleanup(settings.stop)
        dl.account_warning = ""

    def test_account_used_from_first_request_only_for_youtube(self):
        dl.save_settings({"browser": "chrome", "profile": "Profile 1"})
        with patch.object(dl.subprocess, "run", return_value=result()) as run:
            for target in (URL, "ytsearch1:test", "https://www.bilibili.com/video/BV123"):
                dl._run(target, ["--simulate"])
        for call in run.call_args_list[:2]:
            cmd = call.args[0]
            self.assertEqual(cmd[cmd.index("--cookies-from-browser") + 1], "chrome:Profile 1")
        self.assertNotIn("--cookies-from-browser", run.call_args_list[2].args[0])

    def test_unreadable_cookies_fall_back_and_explain(self):
        with patch.object(dl.subprocess, "run", side_effect=[
            result(1, stderr="ERROR: could not find chrome cookies database"), result(stdout="ok")
        ]) as run:
            self.assertEqual(dl._run(URL, []).stdout, "ok")
        self.assertEqual(run.call_count, 2)
        self.assertNotIn("--cookies-from-browser", run.call_args_list[1].args[0])
        self.assertIn("未登录", dl.account_warning)

    def test_bot_check_does_not_silently_drop_account(self):
        with patch.object(dl.subprocess, "run", return_value=result(1, stderr="Sign in to confirm you're not a bot")) as run:
            self.assertEqual(dl._run(URL, []).returncode, 1)
            self.assertEqual(run.call_count, 1)

    def test_guest_setting_and_profile_validation(self):
        dl.save_settings({"browser": "none", "profile": "Default"})
        self.assertNotIn("--cookies-from-browser", dl._command(URL))
        with self.assertRaises(ValueError):
            dl.save_settings({"browser": "chrome", "profile": "../../other"})
        self.assertEqual(dl.get_settings()["browser"], "none")

    def test_search_failure_keeps_retry_classification(self):
        with patch.object(dl, "_run", return_value=result(1, stderr="HTTP Error 403: Forbidden")):
            with self.assertRaises(dl.DownloadError) as raised:
                dl._search("youtube", "test")
        self.assertTrue(raised.exception.transient)

    def test_failed_or_audio_only_file_is_not_success(self):
        path = os.path.join(self.folder.name, "song.mp4")
        with open(path, "wb") as f:
            f.write(b"unfinished")
        with patch.object(dl.subprocess, "run", return_value=result(stdout=json.dumps({
            "streams": [{"codec_type": "audio"}]
        }))) as probe:
            self.assertIsNone(dl._find_output(self.folder.name, "song", 1))
            probe.assert_not_called()
            self.assertIsNone(dl._find_output(self.folder.name, "song", 0))

    def test_subtitle_failure_does_not_fail_video(self):
        expected = os.path.join(self.folder.name, "song.mp4")
        with patch.object(dl, "_stream_download", return_value=(0, [])) as stream, \
                patch.object(dl, "_find_output", return_value=expected), \
                patch.object(dl, "fetch_subs", return_value=False):
            self.assertEqual(dl.download(URL, self.folder.name, "song"), expected)
        self.assertNotIn("--write-subs", stream.call_args.args[0])

    def test_download_cookie_fallback(self):
        expected = os.path.join(self.folder.name, "song.mp4")
        with patch.object(dl, "_stream_download", side_effect=[
            (1, ["ERROR: could not find chrome cookies database"]), (0, [])
        ]) as stream, patch.object(dl, "_find_output", return_value=expected), \
                patch.object(dl, "fetch_subs", return_value=True):
            self.assertEqual(dl.download(URL, self.folder.name, "song"), expected)
        self.assertIn("--cookies-from-browser", stream.call_args_list[0].args[0])
        self.assertNotIn("--cookies-from-browser", stream.call_args_list[1].args[0])

    def test_api_settings_persist_and_reject_remote_changes(self):
        client = app.app.test_client()
        saved = client.post("/api/download-settings", json={"browser": "chrome", "profile": "Default"})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(client.get("/api/download-settings").json["profile"], "Default")
        for extra in ({"environ_overrides": {"REMOTE_ADDR": "192.168.1.2"}},
                      {"headers": {"Origin": "https://example.com"}},
                      {"headers": {"Host": "example.com:8471"}}):
            self.assertEqual(client.post("/api/download-settings", json={"browser": "none"}, **extra).status_code, 403)
        self.assertEqual(client.post("/api/download-settings", json={"browser": "unknown"}).status_code, 400)
        self.assertEqual(dl.get_settings()["browser"], "chrome")


if __name__ == "__main__":
    unittest.main()
