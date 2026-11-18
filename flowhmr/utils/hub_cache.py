"""Load the installed DINOv3 source without GitHub's default-branch probe."""

from pathlib import Path


def load_dinov3(hub, model, **options):
    project_cache = Path(__file__).resolve().parents[2] / ".cache/torch/hub"
    roots = dict.fromkeys((Path(hub.get_dir()), project_cache))
    for root in roots:
        directory = root / "facebookresearch_dinov3_main"
        if (directory / "hubconf.py").is_file() and (directory / "dinov3").is_dir():
            return hub.load(str(directory), model, source="local", **options)
    # An explicit ref avoids torch.hub's extra main/master discovery request.
    try:
        return hub.load("facebookresearch/dinov3:main", model, source="github", trust_repo=True, **options)
    except OSError as error:
        raise RuntimeError(
            f"DINOv3 模型代码尚未缓存，连接 GitHub 失败。请检查本地代理后重试；缓存目录：{next(iter(roots))}"
        ) from error
