"""Create an export copy without silently grounding airborne model output."""


def generation_origin(output, smpl_mesh, *, align_floor):
    import torch
    from torch import Tensor
    origin = {k: v.clone() if isinstance(v, Tensor) else v for k, v in output.items()}
    if align_floor:
        with torch.no_grad():
            batch = origin["rot6d"].shape[0]
            mesh = smpl_mesh({"rot6d": origin["rot6d"][:, :1].reshape(batch, 52, 6),
                              "shapes": origin["shapes"].reshape(batch, -1),
                              "trans": origin["trans"][:, :1].reshape(batch, 3)})
            floor = mesh["vertices"][:, :, 1].min(dim=-1)[0]
            origin["trans"][:, :, 1] -= floor[:, None]
    return origin
