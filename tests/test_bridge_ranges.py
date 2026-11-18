from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
import unittest

_spec = spec_from_file_location("bridge_ranges", Path(__file__).resolve().parents[1] / "blender_addon/ranges.py")
ranges = module_from_spec(_spec)
_spec.loader.exec_module(ranges)


class BridgeRangesTest(unittest.TestCase):
    def settings(self, **options):
        return SimpleNamespace(**{**dict(use_time_range=True, to_video_end=False,
                                        start_time=2, end_time=5, start_frame=0,
                                        end_frame=0, video_duration=10), **options})

    def test_seconds_use_exclusive_end_on_processing_timeline(self):
        self.assertEqual(ranges.frame_range(self.settings()), (60, 149))
        self.assertEqual(ranges.frame_range(self.settings(start_time=0.01, end_time=0.1)), (1, 2))

    def test_explicit_frames_include_zero_and_single_frames(self):
        self.assertEqual(ranges.frame_range(self.settings(use_time_range=False)), (0, 0))
        self.assertEqual(ranges.frame_range(self.settings(use_time_range=False, start_frame=71, end_frame=71)), (71, 71))

    def test_to_end_clamping_and_experimental_long_motion(self):
        self.assertEqual(ranges.frame_range(self.settings(to_video_end=True)), (60, None))
        self.assertEqual(ranges.frame_range(self.settings(end_time=100)), (60, 299))
        self.assertEqual(ranges.frame_range(self.settings(start_time=0, end_time=200, video_duration=300)), (0, 5999))

    def test_invalid_or_empty_ranges_are_rejected(self):
        for options in (dict(start_time=10), dict(end_time=1), dict(end_time=2),
                        dict(start_time=float("nan")), dict(end_time=float("inf")),
                        dict(start_time=0.001, end_time=0.002),
                        dict(use_time_range=False, start_frame=5, end_frame=4)):
            with self.subTest(options=options), self.assertRaises(ValueError):
                ranges.frame_range(self.settings(**options))
