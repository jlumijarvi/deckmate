import os
import sys
import time
import unittest

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mpris import ArtCache, MediaPlayer, Track, candidates, cover, is_browser, parse_metadata, pick  # noqa: E402


class MetadataTest(unittest.TestCase):
    def test_song(self):
        t = parse_metadata({"xesam:title": "Song", "xesam:artist": ["A", "B"], "xesam:album": "Al",
                            "mpris:artUrl": "https://x/y"})
        self.assertEqual(t, Track("Song", "A, B", "Al", "https://x/y"))

    def test_podcast_has_an_empty_artist_list(self):
        t = parse_metadata({"xesam:title": "Ep 47", "xesam:artist": [""], "xesam:album": "Show"})
        self.assertEqual((t.title, t.artist, t.album, t.art_url), ("Ep 47", "", "Show", ""))

    def test_nothing(self):
        self.assertEqual(parse_metadata(None), Track())
        self.assertEqual(parse_metadata({}), Track())


class InstanceTest(unittest.TestCase):
    NAMES = [":1.3", "org.freedesktop.DBus", "org.mpris.MediaPlayer2.spotify",
             "org.mpris.MediaPlayer2.firefox.instance_1_23", "org.mpris.MediaPlayer2.chromium.instance559273",
             "org.mpris.MediaPlayer2.chromium.instance7", "org.mpris.MediaPlayer2.vlc", "org.mpris.MediaPlayer2"]

    def test_candidates(self):
        self.assertEqual(candidates(self.NAMES, "spotify"), ["org.mpris.MediaPlayer2.spotify"])
        self.assertEqual(candidates(self.NAMES, "firefox"), ["org.mpris.MediaPlayer2.firefox.instance_1_23"])
        self.assertEqual(candidates(self.NAMES, "chromium"), ["org.mpris.MediaPlayer2.chromium.instance559273",
                                                              "org.mpris.MediaPlayer2.chromium.instance7"])
        self.assertEqual(candidates(self.NAMES, "browser"), ["org.mpris.MediaPlayer2.chromium.instance559273",
                                                             "org.mpris.MediaPlayer2.chromium.instance7",
                                                             "org.mpris.MediaPlayer2.firefox.instance_1_23"])
        self.assertEqual(candidates(self.NAMES, "spot"), [])
        self.assertEqual(candidates([], "browser"), [])

    def test_pick_prefers_the_one_playing(self):
        self.assertIsNone(pick({}))
        self.assertEqual(pick({"b": "Paused", "a": "Stopped"}), "b")
        self.assertEqual(pick({"b": "Paused", "a": "Playing", "c": "Playing"}), "a")
        self.assertEqual(pick({"b": "", "a": ""}), "a")

    def test_is_browser(self):
        self.assertTrue(is_browser("browser") and is_browser("firefox") and is_browser("chromium"))
        self.assertFalse(is_browser("spotify"))


class CoverTest(unittest.TestCase):
    def test_cover_fills_the_square(self):
        self.assertEqual(cover(Image.new("RGB", (300, 100)), 72).size, (72, 72))
        self.assertEqual(cover(Image.new("RGB", (10, 40)), 72).size, (72, 72))


def png_bytes(size=(20, 30)):
    import io
    buf = io.BytesIO()
    Image.new("RGB", size, (1, 2, 3)).save(buf, "PNG")
    return buf.getvalue()


class ArtCacheTest(unittest.TestCase):
    def wait(self, cache, url):
        for _ in range(100):
            img = cache.get(url)
            if img is not None or url in cache._images:
                return img
            time.sleep(0.01)
        self.fail("fetch never finished")

    def test_fetches_once_and_caches(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return png_bytes()
        c = ArtCache(fetch=fetch)
        self.assertIsNone(c.get("u1"))        # first ask starts the fetch
        img = self.wait(c, "u1")
        self.assertEqual(img.size, (72, 72))
        self.assertIs(c.get("u1"), img)
        self.assertEqual(calls, ["u1"])

    def test_bad_picture_is_remembered_as_missing(self):
        c = ArtCache(fetch=lambda url: b"not a picture")
        c.get("bad")
        self.assertIsNone(self.wait(c, "bad"))
        self.assertIsNone(c.get("bad"))
        self.assertFalse(c._pending)

    def test_empty_url(self):
        self.assertIsNone(ArtCache(fetch=lambda u: png_bytes()).get(""))


class PlayerWithoutBusTest(unittest.TestCase):
    def test_not_running_player_is_harmless(self):
        p = MediaPlayer("nonexistent-player-for-tests")
        p.refresh()
        self.assertFalse(p.running)
        self.assertEqual(p.bus_name, "org.mpris.MediaPlayer2.nonexistent-player-for-tests")
        p.play_pause()      # must not raise, with or without a session bus
        p.next()
        self.assertIsNone(p.artwork())


if __name__ == "__main__":
    unittest.main()
