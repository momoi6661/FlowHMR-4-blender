"""Build the small source bridge ZIP; optionally install into a known scripts folder."""
import argparse
from datetime import datetime
from pathlib import Path
import shutil
from zipfile import ZipFile, ZIP_DEFLATED


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", type=Path, help="Blender user scripts directory")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    source = root / "blender_addon"
    files = sorted([*source.glob("*.py"), source / "README.md"])
    destination = root / "dist" / "FlowHMR-Blender.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, "w", ZIP_DEFLATED) as package:
        for file in files:
            package.write(file, "flowhmr_bridge/" + file.name)
    print("ZIP", destination, destination.stat().st_size)
    if args.install:
        scripts = args.install.resolve()
        target = scripts / "addons" / "flowhmr_bridge"
        if target.exists():
            init = target / "__init__.py"
            if not init.is_file() or "FlowHMR" not in init.read_text(encoding="utf-8"):
                raise RuntimeError("Refusing to overwrite an unrelated add-on")
            backup = root / ".cache/blender-install-backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            shutil.copytree(target, backup, ignore=shutil.ignore_patterns("__pycache__"))
        target.mkdir(parents=True, exist_ok=True)
        for file in files:
            shutil.copy2(file, target / file.name)
        # Older installations may contain the build-time locator. Remove it so
        # an update cannot silently re-bind the add-on to an old checkout.
        (target / "backend-location.json").unlink(missing_ok=True)
        # This module only belonged to the removed workspace mode.
        (target / "workspace.py").unlink(missing_ok=True)
        print("INSTALLED", target)


if __name__ == "__main__":
    main()
