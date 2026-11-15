import json
import tempfile
import unittest
from pathlib import Path

import kugou_krc_tools as krc


SYNTHETIC_TEXT = """[ti:合成测试]
[ar:测试者]
[offset:-120]
[1000,900]<0,300,7>甲<&><300,600,9>尾[]<>
[2500,700]无逐字<&>文本
"""


class DecodeTests(unittest.TestCase):
    def test_decode_xor_zlib_synthetic_payload(self) -> None:
        raw = krc.encode_krc_for_test(SYNTHETIC_TEXT)
        self.assertTrue(raw.startswith(b"krc1"))
        self.assertEqual(krc.decode_krc(raw), SYNTHETIC_TEXT)

    def test_decode_rejects_non_krc(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "KRC1"):
            krc.decode_krc(b"not-krc")


class ParseAndRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.metadata, self.lines = krc.parse_krc(SYNTHETIC_TEXT)

    def test_preserves_last_word_special_text_and_source_durations(self) -> None:
        first = self.lines[0]
        self.assertEqual(first["text"], "甲<&>尾[]<>")
        self.assertEqual(len(first["words"]), 2)
        self.assertEqual(first["words"][-1]["text"], "尾[]<>")
        self.assertEqual(first["words"][-1]["start_ms"], 1300)
        self.assertEqual(first["words"][-1]["duration_ms"], 600)
        self.assertEqual(first["words"][-1]["source_flag"], 9)

    def test_yrc_uses_absolute_word_start_and_no_fake_word_timing(self) -> None:
        rendered = krc.render_yrc(self.metadata, self.lines)
        self.assertIn(
            "[1000,900](1000,300,0)甲<&>(1300,600,0)尾[]<>", rendered
        )
        self.assertIn("[2500,700]无逐字<&>文本", rendered)
        self.assertNotIn("(2500,700,0)无逐字", rendered)
        self.assertIn("[offset:-120]", rendered)

    def test_default_preserves_source_timestamps_and_offset_tag(self) -> None:
        rendered = krc.render_lrc(self.metadata, self.lines)
        self.assertIn("[offset:-120]", rendered)
        self.assertIn("[00:01.000]甲<&>尾[]<>", rendered)

    def test_apply_offset_shifts_starts_only_and_removes_tag(self) -> None:
        yrc = krc.render_yrc(self.metadata, self.lines, apply_offset=True)
        self.assertNotIn("[offset:", yrc)
        self.assertIn("[880,900](880,300,0)甲<&>(1180,600,0)尾[]<>", yrc)
        self.assertIn("[2380,700]无逐字<&>文本", yrc)

    def test_invalid_offset_is_not_silently_ignored(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "offset"):
            krc.global_offset_ms({"offset": "later"})


class OutputRoundTripTests(unittest.TestCase):
    def test_actual_outputs_from_synthetic_krc(self) -> None:
        raw = krc.encode_krc_for_test(SYNTHETIC_TEXT)
        decoded = krc.decode_krc(raw)
        metadata, lines = krc.parse_krc(decoded)

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            paths = krc.write_outputs(
                output_dir,
                "synthetic",
                decoded,
                metadata,
                lines,
                raw_krc=raw,
                source={"type": "test"},
                include_json=True,
                include_csv=True,
            )
            names = {path.name for path in paths}
            self.assertEqual(
                names,
                {
                    "synthetic.lrc",
                    "synthetic.yrc",
                    "synthetic.decoded.krc.txt",
                    "synthetic.krc",
                    "synthetic.timeline.json",
                    "synthetic.words.csv",
                },
            )
            self.assertEqual((output_dir / "synthetic.krc").read_bytes(), raw)
            self.assertEqual(
                (output_dir / "synthetic.decoded.krc.txt").read_text(encoding="utf-8"),
                SYNTHETIC_TEXT,
            )
            timeline = json.loads(
                (output_dir / "synthetic.timeline.json").read_text(encoding="utf-8")
            )
            self.assertEqual(timeline["global_offset_ms"], -120)
            self.assertEqual(timeline["lines"][0]["words"][-1]["text"], "尾[]<>")
            csv_text = (output_dir / "synthetic.words.csv").read_text(
                encoding="utf-8-sig"
            )
            self.assertIn("word_effective_start_ms", csv_text)
            self.assertIn("1180", csv_text)

    def test_local_decoded_text_does_not_invent_raw_krc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "source.txt"
            input_path.write_text(SYNTHETIC_TEXT, encoding="utf-8")
            raw, decoded = krc.load_local_input(input_path)
            self.assertIsNone(raw)
            self.assertEqual(decoded, SYNTHETIC_TEXT)


class StrictMatchingTests(unittest.TestCase):
    def test_song_matching_is_strict_not_substring(self) -> None:
        exact = {"singername": "测试者", "songname": "测试曲"}
        live = {"singername": "测试者", "songname": "测试曲 (Live)"}
        other_artist = {"singername": "另一位", "songname": "测试曲"}
        self.assertTrue(krc.song_matches(exact, "测试者", "测试曲"))
        self.assertFalse(krc.song_matches(live, "测试者", "测试曲"))
        self.assertFalse(krc.song_matches(other_artist, "测试者", "测试曲"))

    def test_choose_song_rejects_only_nearby_results(self) -> None:
        songs = [{"singername": "测试者", "songname": "测试曲 (Live)"}]
        with self.assertRaisesRegex(RuntimeError, "严格匹配"):
            krc.choose_song(songs, "测试者", "测试曲")


if __name__ == "__main__":
    unittest.main()
