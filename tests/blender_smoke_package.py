"""Enable the delivered ZIP in an isolated directory, preserving installed addons."""
from pathlib import Path
import importlib
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import bpy

ROOT = Path(__file__).resolve().parents[1]
with TemporaryDirectory(prefix="flowhmr-package-") as directory:
    with ZipFile(ROOT / "dist/FlowHMR-Blender.zip") as archive:
        assert archive.testzip() is None
        expected = {"flowhmr_bridge/" + path.name
                    for path in (ROOT / "blender_addon").glob("*.py")}
        expected.add("flowhmr_bridge/README.md")
        assert set(archive.namelist()) == expected
        for path in (ROOT / "blender_addon").glob("*.py"):
            assert archive.read("flowhmr_bridge/" + path.name) == path.read_bytes()
        assert "flowhmr_bridge/backend-location.json" not in archive.namelist()
        archive.extractall(directory)
    sys.path.insert(0, directory)
    addon = importlib.import_module("flowhmr_bridge")
    addon.register()
    try:
        settings = bpy.context.scene.flowhmr
        assert settings.panel_page == "MEDIA"
        assert [item.identifier for item in settings.bl_rna.properties["panel_page"].enum_items] == [
            "MEDIA", "HISTORY", "RIG", "MODELS"]
        assert not settings.auto_release_vram
        assert settings.manual_select and not settings.select_single_person
        assert bpy.ops.flowhmr.align_pose_ground.get_rna_type()
        assert bpy.ops.flowhmr.edit_person_box.get_rna_type()
        assert bpy.ops.flowhmr.repair_bone_display.get_rna_type()
        assert bpy.ops.flowhmr.create_arp_character.get_rna_type()
        assert [i.identifier for i in settings.bl_rna.properties['output_rig'].enum_items] == ['STANDARD', 'TARGET']
        assert not hasattr(bpy.types, 'FLOWHMR_PT_workbench')
        assert 'flowhmr_bridge/workspace.py' not in archive.namelist()
        assert addon.FLOWHMR_PT_Main.bl_region_type == "UI"
        assert bpy.types.FLOWHMR_PT_main == addon.FLOWHMR_PT_Main
    finally:
        addon.unregister()
    assert not hasattr(bpy.types.Scene, "flowhmr")
print("DELIVERED_ZIP_CONTENTS_NATIVE_PANEL_AND_OPERATORS_REGISTRATION_OK", flush=True)
