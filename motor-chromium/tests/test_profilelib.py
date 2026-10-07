"""Mirrors the Node SDK's profilelib tests. The two implementations must agree — a persona
chosen on one SDK must be the persona chosen on another for the same host and key."""

import pytest

from clearcote._profilelib import (
    DEFAULT_MAX_ENCODED,
    eligible,
    gpu_vendor_class,
    score_profile,
    select_profile,
)

HOST = {
    "os_family": "windows",
    "browser_major": 150,
    "gpu_vendor": "intel",
    "gpu_tier": 0,
    "screen_width": 3440,
    "screen_height": 1440,
    "device_pixel_ratio": 1,
    "hardware_concurrency": 16,
    "device_memory": 16,
}


def entry(pid, **over):
    base = {
        "id": pid,
        "os_family": "windows",
        "browser_major": 150,
        "gpu_vendor": "intel",
        "gpu_tier": 0,
        "screen_width": 1920,
        "screen_height": 1080,
        "device_pixel_ratio": 1,
        "hardware_concurrency": 16,
        "device_memory": 16,
        "encoded_size": 10000,
        "captured_at": "2026-07-20T00:00:00+00:00",
    }
    base.update(over)
    return base


class TestGpuVendorClass:
    def test_real_angle_strings(self):
        assert gpu_vendor_class(
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 3080 (0x0000220A) Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ) == "nvidia"
        assert gpu_vendor_class(
            "ANGLE (Intel, Intel(R) UHD Graphics 770 (0xA780) Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ) == "intel"
        assert gpu_vendor_class("ANGLE (AMD, AMD Radeon RX 6800 XT Direct3D11 vs_5_0 ps_5_0, D3D11)") == "amd"
        assert gpu_vendor_class("ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)") == "apple"

    def test_software_rasterizers(self):
        assert gpu_vendor_class("Google SwiftShader") == "software"
        assert gpu_vendor_class("llvmpipe (LLVM 15.0.7, 256 bits)") == "software"

    def test_missing_is_unknown_not_guessed(self):
        assert gpu_vendor_class(None) == "unknown"
        assert gpu_vendor_class("") == "unknown"


class TestHardFilters:
    def test_rejects_mismatched_os(self):
        assert not eligible(entry("a", os_family="linux"), HOST)

    def test_rejects_mismatched_major_even_when_perfect(self):
        # A Chrome-138 capture on a 150 engine contradicts itself; no score should rescue it.
        perfect_but_old = entry("old", browser_major=138, screen_width=3440, screen_height=1440)
        assert score_profile(perfect_but_old, HOST) > 0.8
        assert not eligible(perfect_but_old, HOST)

    def test_rejects_oversized_payload(self):
        assert not eligible(entry("big", encoded_size=DEFAULT_MAX_ENCODED + 1), HOST)
        assert eligible(entry("ok", encoded_size=DEFAULT_MAX_ENCODED - 1), HOST)

    def test_os_override(self):
        assert eligible(entry("l", os_family="linux"), HOST, os_override="linux")


class TestScoring:
    def test_matching_gpu_beats_mismatch(self):
        assert score_profile(entry("m", gpu_vendor="intel"), HOST) > score_profile(
            entry("x", gpu_vendor="nvidia"), HOST
        )

    def test_software_scores_lowest(self):
        assert score_profile(entry("sw", gpu_vendor="software"), HOST) < score_profile(
            entry("real", gpu_vendor="nvidia"), HOST
        )

    def test_larger_screen_penalised_more_than_smaller(self):
        smaller = entry("s", screen_width=1920, screen_height=1080)
        larger = entry("l", screen_width=5120, screen_height=2880)
        assert score_profile(smaller, HOST) > score_profile(larger, HOST)

    def test_fresher_preferred(self):
        fresh = entry("f", captured_at="2026-07-24T00:00:00+00:00")
        stale = entry("s", captured_at="2024-01-01T00:00:00+00:00")
        assert score_profile(fresh, HOST) > score_profile(stale, HOST)


class TestSelectionModes:
    index = [entry(f"p{i}", screen_width=1280 + i * 16, hardware_concurrency=8 + (i % 8)) for i in range(40)]

    def test_best_is_top_scoring_and_stable(self):
        a = select_profile(self.index, HOST, mode="best")
        b = select_profile(self.index, HOST, mode="best")
        assert a["entry"]["id"] == b["entry"]["id"]
        top = max(self.index, key=lambda e: score_profile(e, HOST))
        assert a["entry"]["id"] == top["id"]

    def test_rotate_is_sticky_per_key(self):
        ids = {select_profile(self.index, HOST, mode="rotate", key="account-42")["entry"]["id"] for _ in range(5)}
        assert len(ids) == 1

    def test_rotate_spreads_across_keys(self):
        ids = {
            select_profile(self.index, HOST, mode="rotate", key=f"acct-{i}")["entry"]["id"] for i in range(60)
        }
        # The whole point is not converging on one identity.
        assert len(ids) > 5

    def test_keyless_rotate_is_stable_not_random(self):
        # An identity whose device changes every launch is a harder tell than a fixed one.
        ids = {select_profile(self.index, HOST)["entry"]["id"] for _ in range(5)}
        assert len(ids) == 1

    def test_top_n_bounds_the_pool(self):
        ids = {
            select_profile(self.index, HOST, mode="rotate", key=f"k{i}", top_n=3)["entry"]["id"]
            for i in range(60)
        }
        assert len(ids) <= 3

    def test_reports_pool_size(self):
        assert select_profile(self.index, HOST)["pool_size"] == len(self.index)


class TestFailureBehaviour:
    def test_raises_rather_than_returning_incoherent(self):
        # A wrong persona is worse than none: it turns a clean browser into a contradictory one.
        idx = [entry("a", browser_major=138), entry("b", os_family="linux")]
        with pytest.raises(ValueError, match="no profile matches host"):
            select_profile(idx, HOST)

    def test_error_names_the_reason(self):
        with pytest.raises(ValueError, match="chromium major=150"):
            select_profile([], HOST)
