# Ditto motion tuning (user feedback: head sways/bobs too much, mouth opens a bit too wide).
# Monkeypatches core.atomic_components.motion_stitch._mix_s_d_info (module-level, called once per frame from
# MotionStitch.__call__, single thread) so that, relative to the first generated frame d0:
#   pose (pitch/yaw/roll, 66-bin logits) -> degrees: deg(x_s) + A_POSE * (deg(x_d) - deg(d0))   (damped about the PHOTO pose)
#   t                                     -> x_s + A_T * (t_d - t_d0)
#   exp                                   -> x_s + (exp_d - exp_d0) * f, lip keypoints [6,12,14,17,19,20] additionally * A_LIP
#   (eyes: MotionStitch later replaces the eye rows with x_s + blink template and adds the gaze fix -> blinks untouched)
# Optional HP (seconds): slow re-centering of the pose/t delta (subtract an EMA with time constant HP) so the head
# does not drift away from the photo pose over a long answer (keeps the face aligned with the static hand).
# Every frame's final pose (deg), t and lip-delta norm are recorded in LOG for the motion statistics, also when
# the original function is used (install(None)).
import numpy as np
import core.atomic_components.motion_stitch as MS
from core.atomic_components.motion_stitch import bin66_to_degree

LIP = [6, 12, 14, 17, 19, 20]
_ORIG = MS._mix_s_d_info
LOG = []


def _deg(x):
    return np.asarray(bin66_to_degree(x), dtype=np.float32).reshape(-1)[:1]


def install(a_pose=None, a_t=1.0, a_lip=1.0, hp=0.0, fps=25.0):
    """a_pose None -> original Ditto mix (logging only)."""
    LOG.clear()
    st = {"d0": None, "ema": None}
    k_hp = (1.0 / (hp * fps)) if hp and hp > 0 else 0.0
    lipm = np.ones((21, 3), np.float32)
    lipm[LIP] = a_lip
    lipm = lipm.reshape(1, -1)

    def _record(out, x_s_info):
        LOG.append(np.concatenate([_deg(out["pitch"]), _deg(out["yaw"]), _deg(out["roll"]),
                                   np.asarray(out["t"], np.float32).reshape(-1)[:3],
                                   [float(np.linalg.norm((out["exp"] - x_s_info["exp"]).reshape(21, 3)[LIP]))]]))

    def mix_orig(x_s_info, x_d_info, use_d_keys=("exp", "pitch", "yaw", "roll", "t"), d0=None):
        out = _ORIG(x_s_info, x_d_info, use_d_keys, d0)
        _record(out, x_s_info)
        return out

    def mix(x_s_info, x_d_info, use_d_keys=("exp", "pitch", "yaw", "roll", "t"), d0=None):
        if d0 is None:
            return mix_orig(x_s_info, x_d_info, use_d_keys, d0)
        fac = use_d_keys if isinstance(use_d_keys, dict) else {k: 1.0 for k in use_d_keys}
        if st["d0"] is not d0:  # new session (setup) -> reset filter state
            st["d0"], st["ema"] = d0, None
        dp = {k: _deg(x_d_info[k]) - _deg(d0[k]) for k in ("pitch", "yaw", "roll")}
        dt = (x_d_info["t"] - d0["t"]).astype(np.float32)
        if k_hp:
            cur = np.concatenate([dp["pitch"], dp["yaw"], dp["roll"], dt.reshape(-1)])
            st["ema"] = cur.copy() if st["ema"] is None else st["ema"] + k_hp * (cur - st["ema"])
            cur = cur - st["ema"]
            dp = {"pitch": cur[0:1], "yaw": cur[1:2], "roll": cur[2:3]}
            dt = cur[3:].reshape(dt.shape)
        out = {}
        for k, v in x_d_info.items():
            if k not in fac:
                continue
            f = fac[k]
            if k in dp:
                out[k] = (_deg(x_s_info[k]) + a_pose * f * dp[k]).astype(np.float32)  # degrees, shape (1,)
            elif k == "t":
                out[k] = x_s_info[k] + a_t * f * dt
            elif k == "exp":
                out[k] = x_s_info[k] + (v - d0[k]) * f * lipm
            else:
                out[k] = x_s_info[k] + (v - d0[k]) * f
        for k, v in x_s_info.items():
            if k not in out:
                out[k] = v
        _record(out, x_s_info)
        return out

    MS._mix_s_d_info = mix_orig if a_pose is None else mix


def motion_stats(skip=0):
    """std / peak-to-peak of the head pose (deg) and t over the clip, and lip-delta stats."""
    if len(LOG) <= skip + 2:
        return {}
    a = np.stack(LOG[skip:])
    r = {}
    for i, n in enumerate(["pitch", "yaw", "roll"]):
        r[f"{n}_std_deg"] = round(float(a[:, i].std()), 3)
        r[f"{n}_p2p_deg"] = round(float(np.percentile(a[:, i], 98) - np.percentile(a[:, i], 2)), 3)
        r[f"{n}_speed_deg_per_s"] = round(float(np.abs(np.diff(a[:, i])).mean() * 25), 3)
    for i, n in enumerate(["tx", "ty"]):
        r[f"{n}_std"] = round(float(a[:, 3 + i].std()), 5)
    r["lip_delta_mean"] = round(float(a[:, 6].mean()), 5)
    r["lip_delta_p95"] = round(float(np.percentile(a[:, 6], 95)), 5)
    return r


def hand_keep_putback(putback, keep_png, feather=2.5):
    """Wrap PutBack: inside the (feathered) keep mask the ORIGINAL photo pixels (frame_rgb = the source photo for an
    image source) are kept -> static hand. keep_png must have the source image's size."""
    import cv2
    m = cv2.imread(keep_png, cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0
    if feather > 0:
        m = cv2.GaussianBlur(m, (0, 0), feather)
    ys, xs = np.where(m > 1e-3)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    mk = m[y0:y1, x0:x1, None]

    def pb(frame_rgb, render_img, M_c2o):
        assert frame_rgb.shape[:2] == m.shape, (frame_rgb.shape, m.shape)
        out = putback(frame_rgb, render_img, M_c2o)
        r = out[y0:y1, x0:x1].astype(np.float32)
        s = frame_rgb[y0:y1, x0:x1].astype(np.float32)
        out[y0:y1, x0:x1] = np.clip(r + mk * (s - r) + 0.5, 0, 255).astype(np.uint8)
        return out
    return pb
