from pathlib import Path
from unittest.mock import Mock

import pytest

from flowhmr.utils.hub_cache import load_dinov3


def test_cached_dino_never_probes_github(tmp_path):
    cache = tmp_path / "facebookresearch_dinov3_main"
    (cache / "dinov3").mkdir(parents=True)
    (cache / "hubconf.py").write_text("# fixture")
    hub = Mock(get_dir=Mock(return_value=str(tmp_path)))
    load_dinov3(hub, "dinov3_vith16plus", pretrained=False, drop_path=.1)
    hub.load.assert_called_once_with(str(cache), "dinov3_vith16plus", source="local", pretrained=False, drop_path=.1)


def test_uncached_download_uses_explicit_ref_and_reports_network_failure(tmp_path, monkeypatch):
    # Replace the project fallback so a developer's real cache cannot hide failure.
    monkeypatch.setattr("flowhmr.utils.hub_cache.__file__", str(tmp_path / "flowhmr/utils/hub_cache.py"))
    hub = Mock(get_dir=Mock(return_value=str(tmp_path)), load=Mock(side_effect=OSError("disconnected")))
    with pytest.raises(RuntimeError, match="DINOv3.*GitHub"):
        load_dinov3(hub, "dinov3_vith16plus", pretrained=False)
    hub.load.assert_called_once_with("facebookresearch/dinov3:main", "dinov3_vith16plus", source="github", trust_repo=True, pretrained=False)
