"""Real FBX SDK round trips for the current-role export API, when installed."""

import importlib.util
from contextlib import ExitStack
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

_spec = importlib.util.spec_from_file_location("flowhmr_current_fbx_web",
                                              Path(__file__).resolve().parents[1] / "tools/web/server.py")
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)


@unittest.skipUnless(importlib.util.find_spec("fbx"), "requires the Windows FBX SDK")
class CurrentFbxTest(unittest.TestCase):
    def setUp(self):
        from flowhmr.character_files import CharacterFiles
        from flowhmr.character_library import CharacterLibrary

        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(TemporaryDirectory()))
        self.files = stack.enter_context(patch.object(server, "CHARACTER_FILES", CharacterFiles()))
        self.library = stack.enter_context(patch.object(server, "CHARACTER_LIBRARY",
                                                        CharacterLibrary(self.root / "characters")))
        work = self.root / "group/scene"
        work.mkdir(parents=True)
        np.savez(work / "motion.npz", trans_1=np.zeros((3, 3)), trans_2=np.zeros((3, 3)), mocap_framerate=30)
        stack.enter_context(patch.object(server, "ARGS", SimpleNamespace(work_dir=str(self.root))))
        stack.enter_context(patch.object(server, "MEMBER_DIRS", {}))
        stack.enter_context(patch.object(server, "JOBS", {"group": {
            "status": "done", "person_batch": "group", "filename": "测试.mp4", "scene": {
                "people": [{"person": 1, "start": 0, "end": 2}, {"person": 2, "start": 7, "end": 9}],
            },
        }}))
        self.client = stack.enter_context(server.app.test_client())
        self.template = self.root / "原始骨架.fbx"
        self.output = self.root / "returned.fbx"

    def create_template(self, *, duplicate_siblings=False):
        import fbx
        from flowhmr.core.export.fbx_utils import create_manager, export_scene_atomic, store_bind_pose
        manager = create_manager()
        try:
            scene = fbx.FbxScene.Create(manager, "Skin")
            root = scene.GetRootNode()

            def add_bone(parent, name, xyz):
                node = fbx.FbxNode.Create(scene, name)
                attr = fbx.FbxSkeleton.Create(scene, name)
                attr.SetSkeletonType(fbx.FbxSkeleton.EType.eLimbNode)
                node.SetNodeAttribute(attr)
                node.LclTranslation.Set(fbx.FbxDouble3(*xyz))
                parent.AddChild(node)
                return node

            pelvis = add_bone(root, "角色根", (0, 0, 0))
            wrist_left = add_bone(pelvis, "左腕", (0, 12, 0))
            finger = add_bone(wrist_left, "食指", (5, 0, 0))
            wrist_right = add_bone(pelvis, "右腕", (0, -12, 0))
            other = add_bone(wrist_left if duplicate_siblings else wrist_right, "食指", (-5, 0, 0))
            accessory = add_bone(root, "额外控制骨", (0, 0, 18))
            mesh = fbx.FbxMesh.Create(scene, "蒙皮")
            mesh.InitControlPoints(3)
            for i, value in enumerate(((0, 0, 0), (4, 1, 0), (3, 0, 1))):
                mesh.SetControlPointAt(fbx.FbxVector4(*value), i)
            mesh.BeginPolygon()
            for i in range(3):
                mesh.AddPolygon(i)
            mesh.EndPolygon()
            node = fbx.FbxNode.Create(scene, "网格")
            root.AddChild(node)
            node.SetNodeAttribute(mesh)
            skin = fbx.FbxSkin.Create(scene, "Skin")
            cluster = fbx.FbxCluster.Create(scene, "Bind")
            cluster.SetLink(finger)
            cluster.SetLinkMode(fbx.FbxCluster.ELinkMode.eTotalOne)
            weights = (1.0, .75, .25)
            for i, weight in enumerate(weights):
                cluster.AddControlPointIndex(i, weight)
            cluster.SetTransformMatrix(node.EvaluateGlobalTransform())
            cluster.SetTransformLinkMatrix(finger.EvaluateGlobalTransform())
            skin.AddCluster(cluster)
            mesh.AddDeformer(skin)
            store_bind_pose(scene, [pelvis, wrist_left, finger, wrist_right, other, accessory, node])
            export_scene_atomic(manager, scene, str(self.template))
        finally:
            manager.Destroy()

    def payload(self, identifier=None):
        from scipy.spatial.transform import Rotation
        paths = [["角色根"], ["角色根", "左腕", "食指"]]
        values = np.zeros((len(paths), 3, 10), dtype="<f4")
        values[..., 6:] = 1
        values[0, :, 0] = [11, 12, 13]
        values[1, :, 0] = 5
        values[1, :, 3:7] = Rotation.from_euler("z", [12, 24, 36], degrees=True).as_quat()
        source = ({"character": identifier} if identifier else
                  {"character_file": (BytesIO(self.template.read_bytes()), self.template.name)})
        return {**source, "tracks": (BytesIO(json.dumps(paths, ensure_ascii=False).encode("utf-8")), "paths.json"),
                "samples": (BytesIO(values.tobytes()), "samples.bin")}

    def read_result(self, data, *, with_skin):
        import fbx
        from scipy.spatial.transform import Rotation
        from flowhmr.core.export.fbx_utils import create_manager, load_scene
        from flowhmr.core.export.fbx_profiles import _walk

        self.output.write_bytes(data)
        manager = create_manager()
        try:
            scene = load_scene(manager, str(self.output))
            root = scene.GetRootNode()
            nodes = {(node.GetParent().GetName(), node.GetName()): node for node in _walk(root)[1:]}
            self.assertIn(("角色根", "右腕"), nodes)
            self.assertIn(("右腕", "食指"), nodes)  # Same short name on a different chain.
            self.assertIn((root.GetName(), "额外控制骨"), nodes)  # Unanimated, separate branch.
            mesh = nodes.get((root.GetName(), "网格"))
            self.assertEqual(mesh is not None, with_skin)
            if mesh is not None:
                skin = mesh.GetMesh().GetDeformer(0, fbx.FbxDeformer.EDeformerType.eSkin)
                cluster = skin.GetCluster(0)
                self.assertEqual(cluster.GetLink().GetName(), "食指")
                self.assertEqual(cluster.GetLink().GetParent().GetName(), "左腕")
                self.assertEqual(list(cluster.GetControlPointWeights()), [1.0, .75, .25])
                bind = fbx.FbxAMatrix()
                cluster.GetTransformLinkMatrix(bind)
                np.testing.assert_allclose([bind.GetT()[i] for i in range(3)], [5, 12, 0])
            else:
                poses = [scene.GetPose(i) for i in range(scene.GetPoseCount())]
                self.assertEqual(len(poses), 1)
                self.assertTrue(poses[0].IsBindPose())
                index = next(i for i in range(poses[0].GetCount())
                             if poses[0].GetNode(i).GetUniqueID() == nodes[("左腕", "食指")].GetUniqueID())
                np.testing.assert_allclose([poses[0].GetMatrix(index).Get(3,j) for j in range(3)], [5,12,0])
            stack_class = fbx.FbxCriteria.ObjectType(fbx.FbxAnimStack.ClassId)
            self.assertEqual(scene.GetSrcObjectCount(stack_class), 1)
            scene.SetCurrentAnimationStack(scene.GetSrcObject(stack_class))
            time = fbx.FbxTime()
            time.SetSecondDouble(8 / 30)
            pelvis = nodes[(root.GetName(), "角色根")]
            self.assertAlmostEqual(pelvis.EvaluateLocalTransform(time).GetT()[0], 12)
            q = nodes[("左腕", "食指")].EvaluateLocalTransform(time).GetQ()
            self.assertAlmostEqual(np.rad2deg(Rotation.from_quat([q[i] for i in range(4)]).magnitude()), 24, places=4)
            np.testing.assert_allclose([nodes[("右腕", "食指")].EvaluateLocalTransform(time).GetT()[i]
                                        for i in range(3)], [-5,0,0])
        finally:
            manager.Destroy()

    def post_export(self, query, **kwargs):
        with self.client.post("/api/jobs/group/target-fbx?profile=retarget_fbx&" + query, **kwargs) as res:
            return res.status_code, res.headers, res.get_data(), res.json if res.is_json else None

    def test_original_hierarchy_skin_bind_and_delayed_entry(self):
        self.create_template()
        # Read the library FBX for the skinned case and a transient browser file
        # for the skeleton-only case. The latter must still retain bind pose.
        record = self.library.save(self.files, self.files.register(str(self.template))["ticket"], None)
        for mode in (False, True):
            code, headers, data, error = self.post_export("person=2&mesh=" + str(int(mode)),
                data=self.payload(record["id"] if mode else None))
            self.assertEqual(code, 200, error)
            suffix = "skin" if mode else "skeleton"
            self.assertIn(f"_person2_retarget_fbx_{suffix}.fbx", headers["Content-Disposition"])
            self.read_result(data, with_skin=mode)

    def test_rejects_ambiguous_paths_and_non_local_requests(self):
        self.create_template(duplicate_siblings=True)
        code, _, _, error = self.post_export("person=2", data=self.payload())
        self.assertEqual((code, error["error_code"]), (422, "target_fbx_ambiguous_path"))
        self.assertEqual(error["error_params"]["path"], "角色根 / 左腕 / 食指")
        code, _, _, error = self.post_export("", data=self.payload())
        self.assertEqual((code, error["error_code"]), (400, "target_fbx_one_character"))
        code, _, _, error = self.post_export("person=2", headers={"Origin": "https://example.invalid"})
        self.assertEqual((code, error["error_code"]), (403, "target_fbx_local_only"))
