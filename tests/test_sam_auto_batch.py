"""Cover changing resource budgets without stressing the user's hardware."""

from contextlib import nullcontext
import importlib.util
from types import SimpleNamespace
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec("torch"), "requires the inference runtime")
class SAMAutoBatchTest(unittest.TestCase):
    def setUp(self):
        import torch
        from flowhmr.utils import sam_batch
        self.sam = sam_batch
        self.mib = sam_batch.MIB
        with patch.object(sam_batch.sys, "platform", "linux"):
            self.planner = sam_batch.SAMBatchPlanner(torch.device("cuda:0"), 6 * self.mib)

    def test_batch_grows_from_observed_work_and_shrinks_for_either_resource(self):
        planner = self.planner
        memory = self.sam._Memory(ram_free=8000 * self.mib, ram_room=6000 * self.mib,
                                  vram_room=7000 * self.mib, process_ram=0, process_ram_peak=0)
        work = lambda n: 2 * n
        self.assertEqual(planner.choose(memory, 100, 0, work), 1)

        # A whole warm task has sustained several small batches. A 16 GiB
        # card with more headroom can use batches above the old five-frame cap.
        planner.last_good = 5
        planner.seen_hands = True
        planner.vram_per_crop = 350 * self.mib
        self.assertEqual(planner.choose(memory, 100, 0, work), 7)
        memory.vram_room = 1900 * self.mib
        self.assertEqual(planner.choose(memory, 100, 0, work), 2)
        memory.vram_room = 7000 * self.mib
        memory.ram_room = 75 * self.mib
        self.assertEqual(planner.choose(memory, 100, 0, work), 2)
        self.assertEqual(planner.choose(memory, 100, 30 * self.mib, work), 1)
        with self.assertRaises(self.sam.SAMMemoryError) as error:
            planner.choose(memory, 100, 70 * self.mib, work)
        self.assertEqual(error.exception.resource, "ram")

    def test_peak_from_body_only_limits_later_sparse_batches(self):
        planner = self.planner
        memory = self.sam._Memory(ram_free=8000 * self.mib, ram_room=6000 * self.mib,
                                  vram_room=2000 * self.mib, process_ram=0, process_ram_peak=0)
        with patch.object(self.sam.torch.cuda, "max_memory_allocated", return_value=700 * self.mib), \
                patch.object(self.sam.torch.cuda, "max_memory_reserved", return_value=720 * self.mib):
            planner.observe(memory, memory, 1, 0)
        self.assertEqual(planner.choose(memory, 100, 0, lambda n: 0), 2)
        planner.last_good = 10
        planner.seen_hands = True
        self.assertEqual(planner.choose(memory, 100, 0, lambda n: 2), 2)
        memory.vram_room = 880 * self.mib
        with self.assertRaises(self.sam.SAMMemoryError) as error:
            planner.choose(memory, 100, 0, lambda n: 0)
        self.assertEqual(error.exception.resource, "vram")

    def test_driver_budget_and_commit_are_independent_limits(self):
        planner = self.planner
        mib = self.mib
        planner.driver = SimpleNamespace(local=lambda: (9000 * mib, 7000 * mib))
        with patch.object(self.sam, "system_memory", return_value=(5000 * mib, 32000 * mib)), \
                patch.object(self.sam.torch.cuda, "mem_get_info", return_value=(10600 * mib, 16384 * mib)), \
                patch.object(self.sam.torch.cuda, "memory_allocated", return_value=5000 * mib), \
                patch.object(self.sam.torch.cuda, "memory_reserved", return_value=5200 * mib):
            memory = planner.snapshot()
        self.assertLess(memory.vram_room, 1000 * mib)
        self.assertLess(memory.ram_room, 4000 * mib)

    def test_squeezed_budget_is_rechecked_after_releasing_cache(self):
        planner = self.planner
        mib = self.mib
        squeezed = self.sam._Memory(8000 * mib, 6000 * mib, 0, 0,
                                    vram_room=600 * mib, allocated=4000 * mib, reserved=5000 * mib,
                                    driver_budget=4500 * mib, driver_usage=4700 * mib)
        after_release = self.sam._Memory(8000 * mib, 6000 * mib, 0, 0,
                                        vram_room=400 * mib, allocated=4000 * mib, reserved=4000 * mib)
        with patch.object(planner, "snapshot", side_effect=[squeezed, after_release]), \
                patch.object(planner, "release_cache") as release:
            self.assertIs(planner.before_batch(), after_release)
        release.assert_called_once()

    def test_cuda_reuses_healthy_cache_below_the_general_reserve(self):
        planner = self.planner
        mib = self.mib
        # 650 MiB is below the 768 MiB reserve, but the cached 1 GiB
        # working set is reusable and WDDM has not reduced our budget.
        memory = self.sam._Memory(8000 * mib, 6000 * mib, 0, 0,
                                  vram_free=650 * mib, vram_room=882 * mib,
                                  allocated=4000 * mib, reserved=5000 * mib,
                                  driver_budget=6000 * mib, driver_usage=5350 * mib)
        with patch.object(planner, "snapshot", return_value=memory), \
                patch.object(planner, "release_cache") as release:
            self.assertIs(planner.before_batch(), memory)
        release.assert_not_called()

    def test_cuda_does_not_charge_retained_initialization_twice(self):
        planner = self.planner
        mib = self.mib
        before = self.sam._Memory(8000 * mib, 6000 * mib, 0, 0,
                                  allocated=3000 * mib, reserved=3000 * mib)
        after = self.sam._Memory(8000 * mib, 6000 * mib, 0, 0,
                                 allocated=3400 * mib, reserved=4500 * mib,
                                 vram_room=800 * mib, driver_usage=4500 * mib)
        with patch.object(self.sam.torch.cuda, "max_memory_allocated", return_value=3700 * mib), \
                patch.object(self.sam.torch.cuda, "max_memory_reserved", return_value=4500 * mib):
            planner.observe(before, after, 1, 2)
        # 400 MiB initialization is retained; 1100 MiB of allocator cache
        # is reusable. Neither is another temporary allocation next frame.
        self.assertEqual(planner.vram_per_crop, 150 * mib)
        self.assertEqual(planner.choose(after, 100, 0, lambda n: 2 * n), 2)

    def test_separate_hands_remeasure_the_smaller_working_set(self):
        planner = self.planner
        mib = self.mib
        planner.last_good = 1
        planner.seen_hands = True
        planner.vram_per_crop = planner.vram_per_frame = 1800 * mib
        planner.split_hands()
        memory = self.sam._Memory(8000 * mib, 6000 * mib, 0, 0,
                                  vram_free=779 * mib, vram_total=6144 * mib,
                                  vram_room=11 * mib)
        self.assertEqual(planner.choose(memory, 100, 0, lambda n: n), 1)

    def test_warm_cuda_batches_can_grow_below_the_cold_memory_guess(self):
        planner = self.planner
        mib = self.mib
        memory = self.sam._Memory(8000 * mib, 6000 * mib, 0, 0,
                                  vram_room=800 * mib)
        with patch.object(self.sam.torch.cuda, "max_memory_allocated", return_value=300 * mib), \
                patch.object(self.sam.torch.cuda, "max_memory_reserved", return_value=400 * mib):
            planner.observe(memory, memory, 1, 2)
        # A measured 300 MiB frame needs at most 750 MiB for two frames,
        # including the 25% margin. The cold 256 MiB-per-crop guess would
        # incorrectly keep this at one frame despite 800 MiB of headroom.
        self.assertEqual(planner.choose(memory, 100, 0, lambda n: 2 * n), 2)
        memory.vram_room = 374 * mib
        with self.assertRaises(self.sam.SAMMemoryError):
            planner.choose(memory, 100, 0, lambda n: 2 * n)

    def test_minimum_cuda_batch_can_use_the_general_reserve(self):
        planner = self.planner
        mib = self.mib
        # A real 6 GiB run completed with 779 MiB free. The ordinary
        # 768 MiB reserve must not prevent the single-frame fallback.
        memory = self.sam._Memory(8000 * mib, 6000 * mib, 0, 0,
                                  vram_free=779 * mib, vram_total=6144 * mib,
                                  vram_room=11 * mib)
        with self.assertRaises(self.sam.SAMMemoryError):
            planner.choose(memory, 100, 0, lambda n: 2 * n)
        planner.split_hands()
        self.assertEqual(planner.choose(memory, 100, 0, lambda n: n), 1)
        planner.last_good = 5
        planner.seen_hands = True
        self.assertEqual(planner.choose(memory, 100, 0, lambda n: n), 1)

    def test_minimum_cuda_batch_keeps_real_capacity_and_ram_limits(self):
        planner = self.planner
        mib = self.mib
        planner.low_memory = True
        memory = self.sam._Memory(8000 * mib, 6000 * mib, 0, 0,
                                  vram_free=779 * mib, vram_total=6144 * mib,
                                  vram_room=11 * mib)
        for changes in (
            {"vram_free": 300 * mib},
            {"driver_budget": 6000 * mib, "driver_usage": 5700 * mib},
        ):
            with self.subTest(changes=changes), patch.multiple(memory, **changes):
                with self.assertRaises(self.sam.SAMMemoryError) as failure:
                    planner.choose(memory, 1, 0, lambda n: n)
                self.assertEqual(failure.exception.resource, "vram")
        planner.vram_per_frame = 700 * mib
        with self.assertRaises(self.sam.SAMMemoryError):
            planner.choose(memory, 1, 0, lambda n: n)
        planner.vram_per_frame = 0
        memory.ram_room = 1024 * mib
        with self.assertRaises(self.sam.SAMMemoryError) as failure:
            planner.choose(memory, 1, 0, lambda n: n)
        self.assertEqual(failure.exception.resource, "ram")

    def test_minimum_cuda_reserve_does_not_change_other_backends(self):
        planner = self.planner
        mib = self.mib
        planner.low_memory = True
        memory = self.sam._Memory(8000 * mib, 6000 * mib, 0, 0,
                                  vram_free=779 * mib, vram_total=6144 * mib,
                                  vram_room=11 * mib)
        for changes in (
            {"shared_gpu": True},
            {"device": SimpleNamespace(type="xpu")},
        ):
            with self.subTest(changes=changes), patch.multiple(planner, **changes):
                with self.assertRaises(self.sam.SAMMemoryError):
                    planner.choose(memory, 1, 0, lambda n: n)
        with patch.object(self.sam.torch.version, "hip", "6.4"):
            with self.assertRaises(self.sam.SAMMemoryError):
                planner.choose(memory, 1, 0, lambda n: n)

    def test_allocation_failures_and_smaller_retry(self):
        planner = self.planner
        for message in ("HIPBLAS_STATUS_ALLOC_FAILED", "rocblas_status_memory_error", "miopenStatusAllocFailed"):
            with self.subTest(message=message):
                self.assertEqual(self.sam.memory_error_resource(RuntimeError(message)), "vram")
        self.assertEqual(self.sam.memory_error_resource(MemoryError()), "ram")
        self.assertEqual(self.sam.memory_error_resource(RuntimeError("DefaultCPUAllocator: can't allocate memory")), "ram")
        self.assertIsNone(self.sam.memory_error_resource(RuntimeError("MIOpen convolution shape mismatch")))
        with patch.object(self.sam.torch.cuda, "device", return_value=nullcontext()), \
                patch.object(self.sam.torch.cuda, "empty_cache"):
            planner.retry(5, "vram")
            self.assertEqual(planner.retry_size, 2)
            self.assertLess(planner.retry_limit, 5)
            planner.retry(1, "vram")
            self.assertEqual(planner.retry_limit, 1)
            self.assertTrue(planner.low_memory)
        self.assertEqual(planner.info["retries"], 2)

    def test_sparse_interpolation_across_blocks(self):
        import numpy as np
        import torch
        from flowhmr.utils.runtime_sam_features import fill_hand_slots

        torch.set_num_threads(min(torch.get_num_threads(), 4))
        total = 640
        indices = np.array([5, 31, 237, 411, 620])
        tokens = torch.full((total, 3072), torch.nan)
        tokens[:, :1024] = 7
        tokens[indices, 1024:] = torch.from_numpy(indices.astype(np.float32))[:, None]
        ordinary = fill_hand_slots(tokens, indices)
        result = fill_hand_slots(tokens, indices, in_place=True)
        self.assertIs(result, tokens)
        self.assertTrue(torch.equal(result, ordinary))
        expected = np.interp(np.arange(total), indices, indices)
        np.testing.assert_allclose(result[:, 1024].numpy(), expected, atol=1e-4)
        self.assertTrue(torch.all(result[:, :1024] == 7))
