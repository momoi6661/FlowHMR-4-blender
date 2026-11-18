import json
from pathlib import Path
import struct
import tempfile
import unittest
from pydantic import ValidationError
from flowhmr.mcp.characters import PosePatch, can_request_ik, texture_references


class CharacterContractsTest(unittest.TestCase):
    def test_ik_requires_a_native_or_explicit_controller(self):
        mapping = list(range(52))
        pose = {'retarget': {'map': mapping, 'ik': {'leftFoot': 57}}}
        self.assertTrue(can_request_ik('pmx', {}))
        for kind in ('fbx', 'vrm'):
            with self.subTest(kind=kind):
                self.assertFalse(can_request_ik(kind, {}))
                self.assertTrue(can_request_ik(kind, pose))
                self.assertFalse(can_request_ik(kind, {'retarget': {
                    'map': mapping, 'ik': {'leftFoot': 7}}}))
                self.assertFalse(can_request_ik(kind, {'retarget': {
                    'map': mapping[:7] + [-1] + mapping[8:], 'ik': {'leftFoot': 57}}}))

    def test_pose_rejects_invalid_values_and_preserves_unedited_controls(self):
        for values in ({'root_rotation':[0,181,0]}, {'root_translation':[0,6,0]},
                       {'rotations':{'99':[0,0,0]}}, {'rotations':{'16':[0,0]}},
                       {'root_rotation':[float('nan'),0,0]}, {'unknown':True}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                PosePatch.model_validate(values)
        current={'rotations':{'16':[10,0,0],'17':[-10,0,0]}, 'rootRotation':[0,20,0]}
        patched=PosePatch(rotations={'16':[0,0,0]}).patch(current)
        self.assertEqual(patched['rotations']['17'],[-10,0,0])
        self.assertEqual(patched['rootRotation'],[0,20,0])

    def test_vrm_versions_and_external_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'character.vrm'
            def write(document):
                data=json.dumps(document).encode('utf-8')
                data+=b' ' * (-len(data)%4)
                path.write_bytes(struct.pack('<4sIIII',b'glTF',2,20+len(data),len(data),0x4e4f534a)+data)
            for extension in ('VRM','VRMC_vrm'):
                write({'asset':{'version':'2.0'},'extensions':{extension:{}}})
                self.assertEqual(texture_references(path),[])
            write({'asset':{'version':'2.0'}})
            with self.assertRaises(ValueError):
                texture_references(path)
            write({'extensions':{'VRM':{}},'images':[{'uri':'private.png'}]})
            with self.assertRaises(ValueError):
                texture_references(path)
