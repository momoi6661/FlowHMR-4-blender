from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from flowhmr.character_files import CharacterFiles
from flowhmr.character_library import CharacterLibrary, RetargetPresetError
from flowhmr.mcp.characters import PosePatch


class RetargetPersistenceTest(unittest.TestCase):
    def test_bundle_reselection_keeps_personal_pose_and_preset_is_independent(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, changed = root / "actor.fbx", root / "changed" / "actor.fbx"
            changed.parent.mkdir()
            source.write_bytes(b"first rig")
            changed.write_bytes(b"changed rig")
            files = CharacterFiles()
            library = CharacterLibrary(root / "library")
            ticket = files.register(str(source))["ticket"]
            self.assertIsNone(library.lookup(files, ticket))
            character = library.save(files, ticket, "First")
            self.assertEqual(library.lookup(files, files.register(str(source))["ticket"]), character)
            self.assertIsNone(library.lookup(files, files.register(str(changed))["ticket"]))
            preset = {"version": 1, "id": "f" * 64, "name": "Studio fit", "kind": "fbx",
                      "links": [f"/hips/bone-{i}" for i in range(52)], "ik": {},
                      "offsets": {"boneOffsets": {"rotations": {"15": [12, 0, 0]}}}}
            identifier = character["id"]
            library.set_actor("actor", "single", identifier,
                              {"boneOffsets": {"rotations": {"15": [19, 0, 0]}}},
                              select=True, remember=True, retarget_preset=preset)
            library = CharacterLibrary(library.root)
            data = library.snapshot(["actor"])
            self.assertEqual(data["presets"][identifier]["boneOffsets"]["rotations"]["15"], [19, 0, 0])
            self.assertEqual(data["retarget_presets"][preset["id"]]["offsets"]["boneOffsets"]["rotations"]["15"], [12, 0, 0])
            with self.assertRaises(RetargetPresetError):
                library.set_actor("actor", "single", identifier, {}, select=True,
                                  retarget_preset={**preset, "kind": "pmx"})
            self.assertEqual(library.snapshot(["actor"]), data)
            self.assertTrue(library.remove(identifier, files))
            self.assertEqual(library.snapshot([])["retarget_presets"], data["retarget_presets"])

    def test_remembers_mapping_per_character_and_preserves_it_on_mcp_edits(self):
        """A saved map must survive a restart and follow the character to another actor."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "one.fbx", root / "two.fbx"
            first.write_bytes(b"first rig")
            second.write_bytes(b"second rig")
            files = CharacterFiles()
            library = CharacterLibrary(root / "library")
            left, right = [library.save(files, files.register(str(file))["ticket"], file.stem)["id"]
                           for file in (first, second)]
            mapping = list(range(52))
            mapping[23] = float("inf")
            profile = {"version": 1, "map": mapping, "root": 60, "ik": {"leftFoot": 61, "rightFoot": True},
                       "rotations": {"15": [182, -15, float("nan")], "41": [4, 0, 0], "99": [15, 0, 0]},
                       "translations": {"20": [.06, 0, .01]}}
            library.set_actor("actor-a", "1", left, {"retarget": profile, "ikMode": "ik"}, select=True)
            library.set_actor("actor-b", "2", right, {"rootTranslation": [.1, 0, 0]}, select=True)
            library = CharacterLibrary(library.root)
            data = library.snapshot(["actor-a", "actor-b"])
            body = data["actors"]["actor-a"]["poses"][left]
            self.assertEqual(data["presets"][left], body)
            self.assertNotIn(right, data["presets"])
            self.assertEqual(body["retarget"]["map"][23], -1)
            self.assertEqual(body["retarget"]["map"][39], 39)  # independent right-hand map
            self.assertEqual(body["retarget"]["root"], 60)
            self.assertEqual(body["retarget"]["ik"]["leftFoot"], 61)
            self.assertEqual(body["retarget"]["ik"]["rightFoot"], -1)
            self.assertEqual(body["retarget"]["rotations"], {"15": [180, -15, 0], "41": [4, 0, 0]})
            self.assertEqual(body["retarget"]["translations"], {"20": [.06, 0, .01]})
            library.update_pose("actor-a", "1", left, PosePatch(root_translation=[.5, .1, 0]).patch)
            data = library.snapshot(["actor-a", "actor-b"])
            edited = data["actors"]["actor-a"]["poses"][left]
            self.assertEqual(edited["retarget"], body["retarget"])
            self.assertEqual(edited["rootTranslation"], [.5, .1, 0])
            self.assertNotIn("retarget", data["actors"]["actor-b"]["poses"][right])
