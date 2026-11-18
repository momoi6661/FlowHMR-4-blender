"""Retry all CUDA OOM forms without skipping crops or masking other failures."""
import unittest
from unittest.mock import Mock

import torch

from flowhmr.utils.runtime_hamer import _worker_error_message
from flowhmr.utils.runtime_hamer_worker import _initial_batch_size, _predict_with_retry


class HamerMemoryTest(unittest.TestCase):
    def test_small_cards_start_with_one_hand(self):
        gib = 1024 ** 3
        self.assertEqual(_initial_batch_size(3 * gib, 6 * gib), 1)
        self.assertEqual(_initial_batch_size(7 * gib, 8 * gib), 1)
        self.assertEqual(_initial_batch_size(gib, 24 * gib), 1)
        self.assertEqual(_initial_batch_size(16 * gib, 24 * gib), 8)

    def test_oom_retries_same_crops_for_pytorch_and_driver_exceptions(self):
        for error in (torch.cuda.OutOfMemoryError("CUDA out of memory"),
                      torch.AcceleratorError("CUDA error: out of memory"),
                      RuntimeError("HIP error: out of memory")):
            with self.subTest(error=type(error).__name__):
                crops = list(range(8))
                predict = Mock(side_effect=[error, error, ["left", "right"]])
                clear, report = Mock(), Mock()
                batch, hands, size = _predict_with_retry(crops, 8, predict, clear, report)
                self.assertEqual([call.args[0] for call in predict.call_args_list],
                                 [crops, crops[:4], crops[:2]])
                self.assertEqual((batch, hands, size), ([0, 1], ["left", "right"], 2))
                self.assertEqual(clear.call_count, 2)
                self.assertEqual([call.args[0] for call in report.call_args_list], [4, 2])

    def test_retry_does_not_hide_other_cuda_errors_or_loop_at_one_hand(self):
        for size, error in ((8, RuntimeError("CUDA error: device-side assert triggered")),
                            (1, RuntimeError("CUDA error: out of memory"))):
            with self.subTest(size=size):
                clear, report = Mock(), Mock()
                with self.assertRaises(RuntimeError) as raised:
                    _predict_with_retry([0, 1], size, Mock(side_effect=error), clear, report)
                self.assertIs(raised.exception, error)
                clear.assert_not_called()
                report.assert_not_called()

    def test_ui_explains_oom_and_preserves_other_root_exceptions(self):
        advice = "Compile with `TORCH_USE_CUDA_DSA` to enable device-side assertions."
        self.assertIn("显存不足", _worker_error_message([
            "torch.AcceleratorError: CUDA error: out of memory", advice]))
        self.assertEqual(_worker_error_message(["ValueError: invalid crop", advice]),
                         "ValueError: invalid crop")

    def test_native_crash_is_not_reported_as_the_last_import_warning(self):
        warning = 'warnings.warn("deprecated import", FutureWarning)'
        self.assertIn("0xC0000005", _worker_error_message([warning], -1073741819))
        self.assertNotIn("deprecated", _worker_error_message([warning], 1))
        self.assertIn("系统内存不足", _worker_error_message([warning], 0xC0000017))
        self.assertEqual(_worker_error_message(["RuntimeError: invalid weights", warning], 1),
                         "RuntimeError: invalid weights")
