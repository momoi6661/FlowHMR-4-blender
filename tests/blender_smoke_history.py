"""Blender history UI adapters, with local fixtures and no real user deletion."""
from pathlib import Path
from unittest.mock import patch
import copy, json, shutil, sys, time
import bpy
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import blender_addon as addon
from blender_addon import operators, motion


def wait(s):
    limit=time.monotonic()+60
    while s.busy and time.monotonic()<limit:
        operators._pump()
        time.sleep(.05)
    assert not s.busy,"Request timed out"
    assert not s.last_error,s.message


class FixtureClient:
    def __init__(self):
        self.jobs={"history-1":dict(id="history-1",filename="original-dance.mp4",status="done",progress=100,
            created=1700000000,num_frames=30,models_loaded=False,
            params=dict(start_frame=30,end_frame=59,hamer=False,postprocess=False,steps=17,release_after_job=True)),
            "active-job":dict(id="active-job",filename="running.mp4",status="running",progress=20,
            created=1699999999,params={})}
        self.deleted=[]
        self.rerun=None
    def job(self, job_id):
        return copy.deepcopy(self.jobs[job_id])
    def info(self):
        return dict(models_loaded=True, runtime=dict(ready=True), model_files=dict(items=[]))
    def save(self, route, destination):
        destination=Path(destination)
        destination.parent.mkdir(parents=True,exist_ok=True)
        source=ROOT/(".cache/blender-test/history-cover.jpg" if "thumbnail.jpg" in route else
                    ".cache/blender-test/motion.npz" if "blender-motion" in route else "assets/example/example.mp4")
        shutil.copyfile(source,destination)
        return str(destination)
    def json(self, route, body=None, method=None):
        if method=="DELETE":
            assert body and body.get("ids"),"Never send an empty delete request"
            self.deleted=body["ids"]
            deleted=[]
            skipped=[]
            for key in body["ids"]:
                if self.jobs[key]["status"]=="running":
                    skipped.append(dict(id=key,reason="job is still active"))
                else:
                    self.jobs.pop(key)
                    deleted.append(key)
            return dict(deleted=deleted,skipped=skipped)
        if route.endswith("/rerun"):
            self.rerun=body
            row=copy.deepcopy(self.jobs["history-1"])
            row.update(id="new-history",status="done")
            self.jobs[row["id"]]=row
            return dict(job_id=row["id"])
        assert route.startswith("/api/jobs?")
        return dict(jobs=list(self.jobs.values()),total=len(self.jobs),next_cursor=None)


addon.register()
try:
    scene=bpy.context.scene
    s=scene.flowhmr
    s.backend_root=str(ROOT)
    picture=bpy.data.images.new("History cover fixture",width=180,height=120)
    picture.generated_color=(.1,.5,.2,1)
    picture.filepath_raw=str(ROOT/".cache/blender-test/history-cover.jpg")
    picture.file_format="JPEG"
    picture.save()
    bpy.data.images.remove(picture)
    fixture=FixtureClient()
    with patch.object(operators,"_history_client",return_value=fixture),patch.object(operators,"_client",return_value=fixture):
        bpy.ops.flowhmr.refresh_history()
        wait(s)
        assert len(s.history)==2
        assert s.models_loaded
        assert "1 人" in s.history[0].detail
        assert operators._HISTORY_PREVIEWS and len(operators._HISTORY_PREVIEWS) == 2
        assert all(list(v.image_size) == [180, 120] for v in operators._HISTORY_PREVIEWS.values())
        assert "1.00–2.00" in s.history[0].source_detail
        print("HISTORY_SMALL_THUMBNAILS_AND_SELECTED_CLIP_DETAILS_OK",flush=True)
        bpy.ops.flowhmr.open_history()
        wait(s)
        assert s.source_name=="original-dance.mp4"
        assert s.start_time==1 and s.end_time==2 and not s.to_video_end
        assert s.steps==17 and s.status=="done"
        assert s.contact_mode == "AIRBORNE" and not s.postprocess
        assert len(json.loads(s.panel_frames_json))==1
        assert not s.auto_release_vram
        print("HISTORY_PROJECT_LIST_MEDIA_AND_PARAMETERS_RESTORED_OK",flush=True)
        # A real native target skeleton gets a separate Action through history import.
        target=motion.import_motion(bpy.context,ROOT/".cache/blender-test/motion.npz",include_mesh=False)[0]
        del target["flowhmr_is_source"]
        target.name="History Target Rig"
        original_action=target.animation_data.action
        s.target_rig=target
        s.root_motion, s.vertical_motion, s.motion_height_offset = False, True, .45
        assert sum(bool(m.target) for m in s.bone_maps)==52
        bpy.ops.flowhmr.open_history(import_result=True)
        wait(s)
        assert target.animation_data.action!=original_action
        assert original_action.name in bpy.data.actions
        imported_action=target.animation_data.action
        assert imported_action["flowhmr_vertical_motion"] and not imported_action["flowhmr_horizontal_motion"]
        assert abs(imported_action["flowhmr_height_offset_m"] - .45) < 1e-5
        assert s.imported_job_id=="history-1"
        print("HISTORY_RESULT_MAPPED_TO_TARGET_NEW_ACTION_OLD_ACTION_RETAINED_OK",flush=True)
        s.history[0].selected=s.history[1].selected=True
        bpy.ops.flowhmr.delete_history(job_ids=json.dumps(["history-1","active-job"]))
        wait(s)
        assert fixture.deleted==["history-1","active-job"]
        assert len(s.history)==1 and s.history[0].job_id=="active-job"
        assert not s.job_id and s.status=="idle"
        assert target.name in bpy.data.objects and target.animation_data.action==imported_action
        assert "跳过 1" in s.message
        assert bpy.ops.flowhmr.delete_history()=={"CANCELLED"}
        print("HISTORY_DELETE_SYNC_ACTIVE_SKIP_AND_BLENDER_ACTION_PRESERVED_OK",flush=True)
        fixture.jobs["history-1"]=dict(id="history-1",filename="again.mp4",status="done",progress=100,
            created=1700000000,num_frames=30,params={})
        bpy.ops.flowhmr.refresh_history()
        wait(s)
        s.history_index=next(i for i,item in enumerate(s.history) if item.job_id=="history-1")
        s.auto_import=False
        bpy.ops.flowhmr.rerun_history()
        wait(s)
        assert s.job_id=="new-history"
        assert fixture.rerun["release_after_job"] is False and fixture.rerun["reselect_person"]
        print("HISTORY_RERUN_NEW_JOB_RESELECT_PERSON_AND_KEEP_AI_MODELS_OK",flush=True)
finally:
    addon.unregister()
print("BLENDER_HISTORY_SMOKE_OK",flush=True)
