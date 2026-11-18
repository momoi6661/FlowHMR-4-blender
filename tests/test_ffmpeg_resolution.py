"""ffmpeg resolution: the bundled imageio-ffmpeg build wins, $FFMPEG is validated."""

import contextlib
import io
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from flowhmr.utils import runtime_video_processing as video

EXE_NAME = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"


class FindFfmpegTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def executable(self, *parts):
        path = self.root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        return str(path)

    def resolve(self, *, ffmpeg_env=None, bundled=None, on_path=None):
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {}, clear=False))
            os.environ.pop("FFMPEG", None)
            if ffmpeg_env is not None:
                os.environ["FFMPEG"] = ffmpeg_env
            binary = stack.enter_context(patch("imageio_ffmpeg.get_ffmpeg_exe"))
            if bundled is None:
                binary.side_effect = RuntimeError("imageio-ffmpeg is not installed")
            else:
                binary.return_value = bundled
            stack.enter_context(patch("shutil.which", return_value=on_path))
            return video.find_ffmpeg()

    def test_bundled_binary_is_the_default(self):
        bundled = self.executable("site-packages", "imageio_ffmpeg", "binaries", EXE_NAME)
        system = self.executable("system", EXE_NAME)
        self.assertEqual(self.resolve(bundled=bundled, on_path=system), bundled)

    def test_ffmpeg_env_executable_overrides_bundled(self):
        bundled = self.executable("site-packages", "imageio_ffmpeg", "binaries", EXE_NAME)
        custom = self.executable("custom", "bin", EXE_NAME)
        self.assertEqual(self.resolve(ffmpeg_env=custom, bundled=bundled), custom)

    def test_ffmpeg_env_directory_resolves_the_executable(self):
        # Issue #14: the variable is commonly set to the bin directory.
        bundled = self.executable("site-packages", "imageio_ffmpeg", "binaries", EXE_NAME)
        directory = self.root / "custom" / "bin"
        custom = self.executable("custom", "bin", EXE_NAME)
        self.assertEqual(self.resolve(ffmpeg_env=str(directory), bundled=bundled), custom)

    def test_invalid_ffmpeg_env_falls_back_to_bundled(self):
        bundled = self.executable("site-packages", "imageio_ffmpeg", "binaries", EXE_NAME)
        missing = str(self.root / "missing" / "bin")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            found = self.resolve(ffmpeg_env=missing, bundled=bundled)
        self.assertEqual(found, bundled)
        self.assertIn("ignoring $FFMPEG", output.getvalue())

    def test_path_is_the_last_resort(self):
        system = self.executable("system", EXE_NAME)
        self.assertEqual(self.resolve(on_path=system), system)

    def test_missing_ffmpeg_is_reported(self):
        with self.assertRaises(RuntimeError) as error:
            self.resolve()
        self.assertIn("ffmpeg not found", str(error.exception))


if __name__ == "__main__":
    unittest.main()
