# A/V offset of a remote subscriber recording without clock sync: align the engine's publish timeline
# (logs/timeline_<sid>.json) with a subscriber timeline (lk_record.py) separately for audio (RMS envelope)
# and for video (mouth-openness proxy). Clock offsets cancel in (video lag - audio lag).
# Usage: python av_compare.py <engine_timeline.json> <subscriber.timeline.json>
import json, sys
import numpy as np

tl = json.load(open(sys.argv[1]))
rc = json.load(open(sys.argv[2]))
rows = tl["rows"]
tp = np.array([r[0] for r in rows])
rms = np.array([r[4] for r in rows])
mouth = np.array([r[6] for r in rows])
DT = 0.01


def zoh(ts, vals, grid):
    idx = np.searchsorted(ts, grid, side="right") - 1
    out = np.asarray(vals, np.float64)[np.clip(idx, 0, len(vals) - 1)]
    out[idx < 0] = 0
    return out


def lag(a_t, a_v, b_t, b_v):
    """offset d (s) maximising corr(a(t), b(t + d)); b's clock = a's clock + d."""
    ga = np.arange(a_t[0], a_t[-1], DT)
    gb = np.arange(b_t[0], b_t[-1], DT)
    A = zoh(a_t, a_v, ga)
    B = zoh(b_t, b_v, gb)
    A = A - A.mean()
    B = B - B.mean()
    n = 1 << int(np.ceil(np.log2(len(A) + len(B))))
    c = np.fft.irfft(np.fft.rfft(B, n) * np.conj(np.fft.rfft(A, n)), n)
    k = int(np.argmax(c))
    if k > n // 2:
        k -= n
    peak = c[k] / (np.linalg.norm(A) * np.linalg.norm(B) + 1e-12)
    # parabolic refinement
    km, kp = c[(k - 1) % n], c[(k + 1) % n]
    den = km - 2 * c[k % n] + kp
    frac = 0.5 * (km - kp) / den if den != 0 else 0.0
    return gb[0] - ga[0] + (k + frac) * DT, float(peak)


ra_t = rc["audio_t0"] + np.arange(len(rc["audio_rms10"])) * DT
la, ca = lag(tp, rms, ra_t, np.array(rc["audio_rms10"]))
vt = np.array(rc["video_t"])
vmm = np.array(rc["video_mouth"], np.float64)
ok = ~np.isnan(vmm)
vt, vmm = vt[ok], vmm[ok]
lv, cv = lag(tp, mouth, vt, vmm)
vtd = np.diff(vt)
out = {"audio_clock_lag_s": round(la, 4), "audio_corr": round(ca, 3), "video_clock_lag_s": round(lv, 4),
       "video_corr": round(cv, 3), "av_offset_ms_video_minus_audio": round((lv - la) * 1000, 1),
       "subscriber_fps": round((len(vt) - 1) / (vt[-1] - vt[0]), 2), "subscriber_frames": len(vt),
       "gaps_gt_60ms": int((vtd > 0.06).sum()), "gaps_gt_100ms": int((vtd > 0.1).sum()),
       "max_gap_ms": round(float(vtd.max()) * 1000, 1)}
print(json.dumps(out, indent=1))
