# Minimal GFPGAN v1.4 face restoration (arch files vendored from TencentARC/GFPGAN, basicsr stubbed),
# applied to the lower face of an already-composited frame, at partial strength.
import os, sys, types, time
import numpy as np
import cv2
import torch

GDIR = "/workspace/gfpgan"


def _stub_basicsr():
    if "basicsr" in sys.modules:
        return
    class _Reg:
        def register(self, *a, **k):
            return (lambda x: x) if not a or not callable(a[0]) else a[0]
    b = types.ModuleType("basicsr"); u = types.ModuleType("basicsr.utils"); r = types.ModuleType("basicsr.utils.registry")
    ar = types.ModuleType("basicsr.archs"); au = types.ModuleType("basicsr.archs.arch_util")
    r.ARCH_REGISTRY = _Reg()
    au.default_init_weights = lambda *a, **k: None
    sys.modules.update({"basicsr": b, "basicsr.utils": u, "basicsr.utils.registry": r, "basicsr.archs": ar,
                        "basicsr.archs.arch_util": au})


def load_gfpgan(half=True):
    _stub_basicsr()
    pkg = types.ModuleType("gfparch"); pkg.__path__ = [GDIR]; sys.modules["gfparch"] = pkg
    import importlib
    importlib.import_module("gfparch.stylegan2_clean_arch")
    arch = importlib.import_module("gfparch.gfpganv1_clean_arch")
    net = arch.GFPGANv1Clean(out_size=512, num_style_feat=512, channel_multiplier=2, decoder_load_path=None,
                             fix_decoder=False, num_mlp=8, input_is_latent=True, different_w=True, narrow=1, sft_half=True)
    sd = torch.load(f"{GDIR}/GFPGANv1.4.pth", map_location="cpu")
    net.load_state_dict(sd["params_ema"] if "params_ema" in sd else sd["params"], strict=True)
    net = net.eval().cuda()
    if half:
        net = net.half()
    return net


# FFHQ 5-point template for 512x512 (facexlib FaceRestoreHelper)
TEMPLATE = np.array([[192.98138, 239.94708], [318.90277, 240.1936], [256.63416, 314.01935],
                     [201.26117, 371.41043], [313.08905, 371.15118]], np.float32)


def five_points(lm68):
    return np.array([lm68[36:42].mean(0), lm68[42:48].mean(0), lm68[30], lm68[48], lm68[54]], np.float32)


@torch.no_grad()
def restore(net, frames, lms68, masks_full, strength=0.5, T=None):
    """frames: list of BGR uint8 full frames (already composited); lms68: per-frame base landmarks;
    masks_full: per-frame float [0,1] full-frame region to take the restored pixels into. Batched."""
    T = T if T is not None else {}
    t0 = time.perf_counter()
    Ms, crops = [], []
    for f, lm in zip(frames, lms68):
        M, _ = cv2.estimateAffinePartial2D(five_points(lm), TEMPLATE, method=cv2.LMEDS)
        Ms.append(M)
        crops.append(cv2.warpAffine(f, M, (512, 512), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT101))
    x = torch.from_numpy(np.stack(crops)).cuda().permute(0, 3, 1, 2).flip(1)  # BGR->RGB
    x = (x.float() / 255.0 - 0.5) / 0.5
    x = x.to(next(net.parameters()).dtype).contiguous()
    t1 = time.perf_counter(); T["gfp_warp_in"] = T.get("gfp_warp_in", 0) + t1 - t0
    torch.cuda.synchronize(); ta = time.perf_counter()
    y = net(x, return_rgb=False, randomize_noise=False, weight=0.5)[0]  # fixed noise -> less temporal flicker
    torch.cuda.synchronize(); tb = time.perf_counter(); T["gfp_net"] = T.get("gfp_net", 0) + tb - ta
    y = ((y.float().clamp(-1, 1) + 1) * 127.5).flip(1).permute(0, 2, 3, 1).round().byte().cpu().numpy()
    outs = []
    for f, M, r, mf in zip(frames, Ms, y, masks_full):
        H, W = f.shape[:2]
        Mi = cv2.invertAffineTransform(M)
        back = cv2.warpAffine(r, Mi, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).astype(np.float32)
        a = (mf * strength)[..., None]
        outs.append((back * a + f.astype(np.float32) * (1 - a)).round().clip(0, 255).astype(np.uint8))
    T["gfp_warp_out"] = T.get("gfp_warp_out", 0) + time.perf_counter() - tb
    return outs
