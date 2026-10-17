# Better compositing of MuseTalk output into the base frame ("doesn't look pasted on").
# All ops work on the mask crop box (~440x440 for 王小姐). CPU/OpenCV reference implementation with timing.
import time
import numpy as np
import cv2
from mtlib import crop_pad


def lab_stats_transfer(G, O, w):
    """Global LAB mean/std transfer of G toward O, statistics taken on weight w (skin, similar structure)."""
    sel = w > 0.5
    if sel.sum() < 200:
        return G
    Gl = cv2.cvtColor(np.clip(G, 0, 255).astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
    Ol = cv2.cvtColor(np.clip(O, 0, 255).astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
    mg, sg = Gl[sel].mean(0), Gl[sel].std(0) + 1e-3
    mo, so = Ol[sel].mean(0), Ol[sel].std(0) + 1e-3
    s = np.clip(so / sg, 0.8, 1.25)
    Gl = (Gl - mg) * s + mo
    return cv2.cvtColor(np.clip(Gl, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR).astype(np.float32)


def lf_tone(G, O, w, sigma_px=14, ds=4):
    """Local low-frequency tone correction: add the smoothed (O-G) measured where structure matches (weights w).
    Computed at 1/ds resolution (cheap)."""
    h, wd = G.shape[:2]
    sm = (wd // ds, h // ds)
    D = cv2.resize(O - G, sm, interpolation=cv2.INTER_AREA)
    ww = cv2.resize(w, sm, interpolation=cv2.INTER_AREA)
    s = sigma_px / ds
    num = cv2.GaussianBlur(D * ww[..., None], (0, 0), s)
    den = cv2.GaussianBlur(ww, (0, 0), s)
    corr = num / (den[..., None] + 1e-4)
    corr *= np.clip(den / 0.05, 0, 1)[..., None]  # fade out where no reference
    return G + cv2.resize(corr, (wd, h), interpolation=cv2.INTER_LINEAR)


def _lip_mask(lab, zone):
    """Soft lip mask inside 'zone' (bool) from the LAB a* (redness) channel, Otsu threshold."""
    a = lab[..., 1]
    vals = a[zone].astype(np.uint8)
    if vals.size < 50:
        return np.zeros(a.shape, np.float32)
    thr, _ = cv2.threshold(vals.reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    m = ((a > thr) & zone & (lab[..., 0] > 40)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return m.astype(np.float32)


def lip_color_transfer(G, O, zone, strength=1.0):
    """Match generated lips' LAB mean/std to the base-frame lips (MuseTalk lips come out paler/pinker)."""
    ys, xs = np.where(zone)
    if len(ys) == 0:
        return G
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    g = np.clip(G[y0:y1, x0:x1], 0, 255).astype(np.uint8)
    o = np.clip(O[y0:y1, x0:x1], 0, 255).astype(np.uint8)
    z = zone[y0:y1, x0:x1]
    gl = cv2.cvtColor(g, cv2.COLOR_BGR2LAB).astype(np.float32)
    ol = cv2.cvtColor(o, cv2.COLOR_BGR2LAB).astype(np.float32)
    mg, mo = _lip_mask(gl, z), _lip_mask(ol, z)
    if mg.sum() < 30 or mo.sum() < 30:
        return G
    sg, so = gl[mg > 0], ol[mo > 0]
    mu_g, sd_g, mu_o, sd_o = sg.mean(0), sg.std(0) + 1e-3, so.mean(0), so.std(0) + 1e-3
    s = np.clip(sd_o / sd_g, 0.7, 1.6)
    t = (gl - mu_g) * s + mu_o
    w = cv2.GaussianBlur(mg, (0, 0), 1.5)[..., None] * strength
    gl2 = gl * (1 - w) + t * w
    G = G.copy()
    G[y0:y1, x0:x1] = cv2.cvtColor(np.clip(gl2, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR).astype(np.float32)
    return G


def compose(frame, face256, bbox, mask_u8, mcrop, keep=None, opts=None, T=None, rng=None, lm=None):
    """opts: dict(lab=bool, lf=bool, detail=float(0..1), sharpen=float, grain=bool, feather=int px, erode=int px)."""
    o = opts or {}
    T = T if T is not None else {}
    t0 = time.perf_counter()
    x1, y1, x2, y2 = bbox
    xs, ys, xe, ye = mcrop
    O = crop_pad(frame, mcrop).astype(np.float32)
    G = O.copy()
    G[y1 - ys:y2 - ys, x1 - xs:x2 - xs] = cv2.resize(face256, (x2 - x1, y2 - y1), interpolation=cv2.INTER_LANCZOS4)
    M = mask_u8.astype(np.float32) / 255.0
    if o.get("erode", 0) or o.get("feather", 0):
        Mb = (M > 0.5).astype(np.uint8)
        if o.get("erode", 0):
            e = o["erode"]
            Mb = cv2.erode(Mb, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * e + 1, 2 * e + 1)))
        M = cv2.GaussianBlur(Mb.astype(np.float32), (0, 0), max(1, o.get("feather", 8)))
    if keep is not None:
        M = M * (1.0 - crop_pad(keep, mcrop))
    t1 = time.perf_counter(); T["mask"] = T.get("mask", 0) + t1 - t0
    need_sim = o.get("lab") or o.get("lf") or o.get("detail", 0) > 0 or o.get("grain")
    if need_sim:
        Ob = cv2.GaussianBlur(O, (0, 0), 2.5)
        Gb = cv2.GaussianBlur(G, (0, 0), 2.5)
        delta = np.abs(Ob - Gb).mean(2)
        Mbin = (M > 0.3).astype(np.float32)
    t2 = time.perf_counter(); T["sim"] = T.get("sim", 0) + t2 - t1
    if o.get("lab"):
        G = lab_stats_transfer(G, O, Mbin * (delta < 10))
    t3 = time.perf_counter(); T["lab"] = T.get("lab", 0) + t3 - t2
    if o.get("lf"):
        w = Mbin * np.exp(-(delta / 8.0) ** 2)
        G = lf_tone(G, O, w)
    t4 = time.perf_counter(); T["lf"] = T.get("lf", 0) + t4 - t3
    if o.get("lipcolor") and lm is not None:
        # mouth zone from base landmarks 48-67 (crop coords), enlarged downward for an opening jaw
        pts = lm[48:68] - np.array([xs, ys], np.float32)
        cx, cy = pts.mean(0)
        w2 = (pts[:, 0].max() - pts[:, 0].min()) * 0.65
        h2 = (pts[:, 1].max() - pts[:, 1].min()) * 1.1 + 12
        zone = np.zeros(G.shape[:2], np.uint8)
        cv2.ellipse(zone, (int(cx), int(cy + 6)), (int(w2), int(h2)), 0, 0, 360, 1, -1)
        G = lip_color_transfer(G, O, zone > 0, o["lipcolor"])
    t4b = time.perf_counter(); T["lipcolor"] = T.get("lipcolor", 0) + t4b - t4
    t4 = t4b
    gloss_tex = None
    if (o.get("gloss", 0) > 0 or o.get("clarity", 0) > 0) and lm is not None:
        # mouth zone bbox (crop coords)
        pts = lm[48:68] - np.array([xs, ys], np.float32)
        zx0, zx1 = int(pts[:, 0].min() - 12), int(pts[:, 0].max() + 12)
        zy0, zy1 = int(pts[:, 1].min() - 10), int(pts[:, 1].max() + 30)
        zx0, zy0 = max(zx0, 0), max(zy0, 0)
        g = G[zy0:zy1, zx0:zx1]
        if o.get("clarity", 0) > 0:  # mid-frequency local contrast on the mouth (lip outline, teeth)
            band = cv2.GaussianBlur(g, (0, 0), 1.0) - cv2.GaussianBlur(g, (0, 0), 3.0)
            g = g + o["clarity"] * band
        if o.get("gloss", 0) > 0:  # transplant base-lip high-freq texture (gloss), lower lip shifted to the generated lower lip
            ob = O[zy0:zy1, zx0:zx1]
            gl = cv2.cvtColor(np.clip(g, 0, 255).astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
            ol = cv2.cvtColor(np.clip(ob, 0, 255).astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
            z = np.ones(g.shape[:2], bool)
            mg, mo = _lip_mask(gl, z), _lip_mask(ol, z)
            if mg.sum() > 30 and mo.sum() > 30:
                hf = (ob - cv2.GaussianBlur(ob, (0, 0), 1.0)) * mo[..., None]
                line = int(pts[12:20, 1].mean() - zy0)  # inner-lip landmarks 60-67 = base mouth line
                up, lo = hf.copy(), hf.copy()
                up[line:] = 0; lo[:line] = 0
                rb = np.where(mo.any(1))[0].max(); rg = np.where(mg.any(1))[0].max()
                dy = int(np.clip(rg - rb, -5, 25))
                lo = np.roll(lo, dy, axis=0)
                if dy > 0:
                    lo[:dy] = 0
                gloss_tex = ((up + lo) * cv2.GaussianBlur(mg, (0, 0), 1.0)[..., None] * o["gloss"], (zy0, zy1, zx0, zx1))
        G = G.copy()
        G[zy0:zy1, zx0:zx1] = g
    t4c = time.perf_counter(); T["gloss_clarity"] = T.get("gloss_clarity", 0) + t4c - t4
    t4 = t4c
    if o.get("detail", 0) > 0 or o.get("sharpen", 0) > 0 or o.get("grain"):
        hfO = O - cv2.GaussianBlur(O, (0, 0), 1.2)
        hfG = G - cv2.GaussianBlur(G, (0, 0), 1.2)
        ws = np.exp(-(delta / 10.0) ** 2)[..., None]  # 1 where generated ~ base (static skin, closed lips)
        if o.get("detail", 0) > 0:  # bring back base-clip texture/gloss where structure matches
            G = G + o["detail"] * ws * (hfO - hfG)
        if o.get("sharpen", 0) > 0:  # elsewhere (moving mouth) light unsharp mask
            G = G + o["sharpen"] * (1 - ws) * hfG
        if o.get("grain"):
            sel = Mbin > 0
            target = hfO[sel].std() if sel.any() else 0
            cur = hfG[sel].std() if sel.any() else 0
            add = np.sqrt(max(target ** 2 - cur ** 2, 0))
            if add > 0.3:
                rng = rng or np.random.default_rng()
                n = rng.standard_normal(G.shape[:2]).astype(np.float32) * add
                G = G + (1 - ws) * n[..., None]
    if gloss_tex is not None:
        tex, (zy0, zy1, zx0, zx1) = gloss_tex
        wsz = np.exp(-(delta[zy0:zy1, zx0:zx1] / 10.0) ** 2)[..., None] if need_sim else 0.0
        G[zy0:zy1, zx0:zx1] += tex[:G[zy0:zy1, zx0:zx1].shape[0], :G[zy0:zy1, zx0:zx1].shape[1]] * (1 - wsz)
    t5 = time.perf_counter(); T["detail"] = T.get("detail", 0) + t5 - t4
    out = frame.copy()
    bl = G * M[..., None] + O * (1 - M[..., None])
    H, W = frame.shape[:2]
    sx1, sy1, sx2, sy2 = max(0, xs), max(0, ys), min(W, xe), min(H, ye)
    out[sy1:sy2, sx1:sx2] = bl[sy1 - ys:sy2 - ys, sx1 - xs:sx2 - xs].round().clip(0, 255).astype(np.uint8)
    T["paste"] = T.get("paste", 0) + time.perf_counter() - t5
    return out
