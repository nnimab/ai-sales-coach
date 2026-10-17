# GPU (torch) port of the phase-1 "v11" compositing (mt/blend2.py compose() with the v11 options):
# feathered u57 jaw mask x hand keep-mask, low-frequency tone match, lip LAB colour transfer, mouth clarity,
# base-lip gloss transplant, HF detail/sharpen, grain, causal temporal EMA on the 256 face.
# Everything that depends only on the base frame is precomputed once per clip with the original OpenCV code
# (exact), the per-frame part runs batched on the GPU with no host syncs.
import numpy as np
import cv2
import torch
import torch.nn.functional as F

V11 = dict(tema=0.6, lf=True, lipcolor=1.0, clarity=0.8, gloss=1.0, detail=1.0, sharpen=0.8, grain=True, erode=6, feather=10)


# ----------------------------------------------------------------------------------------------- small GPU ops
_KCACHE = {}


def _gauss_kernel(sigma, device):
    """Same size/weights as cv2.GaussianBlur(float img, (0,0), sigma)."""
    key = (float(sigma), str(device))
    k = _KCACHE.get(key)
    if k is None:
        n = int(round(sigma * 4 * 2 + 1)) | 1
        x = np.arange(n, dtype=np.float64) - (n - 1) / 2
        w = np.exp(-(x ** 2) / (2 * sigma * sigma))
        w /= w.sum()
        k = torch.tensor(w, dtype=torch.float32, device=device)
        _KCACHE[key] = k
    return k


def blur(x, sigma):
    """Separable Gaussian blur, BORDER_REFLECT_101 like OpenCV. x: (N,C,H,W) or (C,H,W) float32."""
    squeeze = x.dim() == 3
    if squeeze:
        x = x.unsqueeze(0)
    n, c, h, w = x.shape
    k = _gauss_kernel(sigma, x.device)
    p = k.numel() // 2
    if p >= h or p >= w:  # tiny input: fall back to replicate padding
        mode = "replicate"
    else:
        mode = "reflect"
    y = x.reshape(n * c, 1, h, w)
    y = F.conv2d(F.pad(y, (p, p, 0, 0), mode=mode), k.view(1, 1, 1, -1))
    y = F.conv2d(F.pad(y, (0, 0, p, p), mode=mode), k.view(1, 1, -1, 1))
    y = y.reshape(n, c, h, w)
    return y[0] if squeeze else y


def _f_lab(t):
    return torch.where(t > 0.008856, t.clamp(min=1e-12).pow(1.0 / 3.0), 7.787 * t + 16.0 / 116.0)


def rgb2lab8(x):
    """x: (3,h,w) RGB 0..255 float -> OpenCV 8-bit Lab convention (L*255/100, a+128, b+128), float."""
    v = (x / 255.0).clamp(0, 1)
    lin = torch.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055).pow(2.4))
    r, g, b = lin[0], lin[1], lin[2]
    X = (0.412453 * r + 0.357580 * g + 0.180423 * b) / 0.950456
    Y = 0.212671 * r + 0.715160 * g + 0.072169 * b
    Z = (0.019334 * r + 0.119193 * g + 0.950227 * b) / 1.088754
    fx, fy, fz = _f_lab(X), _f_lab(Y), _f_lab(Z)
    L = torch.where(Y > 0.008856, 116.0 * fy - 16.0, 903.3 * Y)
    return torch.stack([L * 2.55, 500.0 * (fx - fy) + 128.0, 200.0 * (fy - fz) + 128.0])


def lab82rgb(lab):
    L = lab[0] / 2.55
    a = lab[1] - 128.0
    b = lab[2] - 128.0
    fy = (L + 16.0) / 116.0
    fx = fy + a / 500.0
    fz = fy - b / 200.0

    def finv(t):
        return torch.where(t > 0.206893, t ** 3, (t - 16.0 / 116.0) / 7.787)
    X = finv(fx) * 0.950456
    Y = torch.where(L > 7.9996, fy ** 3, L / 903.3)
    Z = finv(fz) * 1.088754
    r = 3.240479 * X - 1.537150 * Y - 0.498535 * Z
    g = -0.969256 * X + 1.875991 * Y + 0.041556 * Z
    bb = 0.055648 * X - 0.204043 * Y + 1.057311 * Z
    lin = torch.stack([r, g, bb]).clamp(0, 1)
    s = torch.where(lin <= 0.0031308, 12.92 * lin, 1.055 * lin.clamp(min=1e-12).pow(1 / 2.4) - 0.055)
    return (s * 255.0).clamp(0, 255)


def otsu_thr(vals, valid):
    """Otsu threshold (OpenCV semantics, result is 'pixels > thr') of integer values 0..255 where valid."""
    v = vals.clamp(0, 255).long().flatten()
    wts = valid.flatten().float()
    hist = torch.zeros(256, device=vals.device).scatter_add_(0, v, wts)
    tot = hist.sum().clamp(min=1.0)
    p = hist / tot
    i = torch.arange(256, device=vals.device, dtype=torch.float32)
    q1 = torch.cumsum(p, 0)
    m1 = torch.cumsum(p * i, 0)
    mt = m1[-1]
    q2 = 1 - q1
    ok = (q1 > 1e-6) & (q2 > 1e-6)
    sb = torch.where(ok, (mt * q1 - m1) ** 2 / (q1 * q2).clamp(min=1e-12), torch.zeros_like(q1))
    return torch.argmax(sb).float()


def lip_mask(lab, zone=None):
    """Soft-free lip mask like blend2._lip_mask: Otsu on rounded a* inside zone, a>thr & zone & L>40, 3x3 open."""
    a = lab[1].round()
    z = torch.ones_like(a, dtype=torch.bool) if zone is None else zone
    thr = otsu_thr(a, z)
    m = ((a > thr) & z & (lab[0] > 40)).float()[None, None]
    m = 1 - F.max_pool2d(1 - m, 3, 1, 1)  # erode (outside = ignored, like OpenCV's default border)
    m = F.max_pool2d(m, 3, 1, 1)  # dilate
    return m[0, 0]


def shift_rows(x, dy):
    """Rows moved down by dy (tensor, may be negative), zero fill. x: (C,h,w)."""
    h = x.shape[1]
    src = torch.arange(h, device=x.device) - dy.long()
    ok = ((src >= 0) & (src < h)).float()
    g = x[:, src.clamp(0, h - 1)]
    return g * ok.view(1, h, 1)


# ----------------------------------------------------------------------------------------------- per-clip statics
def _crop_pad(img, box):
    x1, y1, x2, y2 = box
    H, W = img.shape[:2]
    out = np.zeros((y2 - y1, x2 - x1) + img.shape[2:], img.dtype)
    sx1, sy1, sx2, sy2 = max(0, x1), max(0, y1), min(W, x2), min(H, y2)
    if sx2 > sx1 and sy2 > sy1:
        out[sy1 - y1:sy2 - y1, sx1 - x1:sx2 - x1] = img[sy1:sy2, sx1:sx2]
    return out


def _lip_mask_cv(lab, zone):
    a = lab[..., 1]
    vals = a[zone].astype(np.uint8)
    if vals.size < 50:
        return np.zeros(a.shape, np.float32)
    thr, _ = cv2.threshold(vals.reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    m = ((a > thr) & zone & (lab[..., 0] > 40)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return m.astype(np.float32)


class ClipStatics:
    """Everything of the v11 blend that depends only on the base clip, on the GPU.
    frames: list of BGR uint8 full frames; coords/mcoords: per-frame bbox / mask crop box (fixed bbox -> constant);
    masks: per-frame u8 jaw masks (crop sized); keep: float [0,1] full-frame keep mask; lms: (N,68,2) landmarks."""

    def __init__(self, frames, coords, masks, mcoords, keep, lms, device, opts=V11):
        self.o = opts
        self.dev = device
        n = len(frames)
        self.n = n
        self.H, self.W = frames[0].shape[:2]
        mc0 = tuple(mcoords[0])
        bb0 = tuple(coords[0])
        assert all(tuple(m) == mc0 for m in mcoords) and all(tuple(c) == bb0 for c in coords), \
            "engine expects a fixed bbox per clip (prep with --bbox fixed)"
        self.mcrop = [int(v) for v in mc0]
        self.bbox = [int(v) for v in bb0]
        xs, ys, xe, ye = self.mcrop
        Hc, Wc = ye - ys, xe - xs
        self.Hc, self.Wc = Hc, Wc
        x1, y1, x2, y2 = self.bbox
        self.fbox = (y1 - ys, y2 - ys, x1 - xs, x2 - xs)
        O_l, M_l, grain_t = [], [], []
        self.lip, self.gloss = [], []
        e = opts.get("erode", 0)
        se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * e + 1, 2 * e + 1)) if e else None
        for k in range(n):
            O = _crop_pad(frames[k], self.mcrop).astype(np.float32)  # BGR
            M = masks[k].astype(np.float32) / 255.0
            if e or opts.get("feather", 0):
                Mb = (M > 0.5).astype(np.uint8)
                if e:
                    Mb = cv2.erode(Mb, se)
                M = cv2.GaussianBlur(Mb.astype(np.float32), (0, 0), max(1, opts.get("feather", 8)))
            if keep is not None:
                M = M * (1.0 - _crop_pad(keep, self.mcrop))
            Mbin = M > 0.3
            hfO = O - cv2.GaussianBlur(O, (0, 0), 1.2)
            grain_t.append(float(hfO[Mbin].std()) if Mbin.any() else 0.0)
            O_l.append(O[..., ::-1].copy())  # -> RGB
            M_l.append(M)
            pts = lms[k][48:68] - np.array([xs, ys], np.float32)
            # lip colour transfer statics (zone ellipse, base-lip LAB stats)
            cx, cy = pts.mean(0)
            w2 = (pts[:, 0].max() - pts[:, 0].min()) * 0.65
            h2 = (pts[:, 1].max() - pts[:, 1].min()) * 1.1 + 12
            zone = np.zeros((Hc, Wc), np.uint8)
            cv2.ellipse(zone, (int(cx), int(cy + 6)), (int(w2), int(h2)), 0, 0, 360, 1, -1)
            zb = zone > 0
            yy, xx = np.where(zb)
            lipst = None
            if len(yy):
                r0, r1, c0, c1 = yy.min(), yy.max() + 1, xx.min(), xx.max() + 1
                o = np.clip(O[r0:r1, c0:c1], 0, 255).astype(np.uint8)
                ol = cv2.cvtColor(o, cv2.COLOR_BGR2LAB).astype(np.float32)
                mo = _lip_mask_cv(ol, zb[r0:r1, c0:c1])
                if mo.sum() >= 30:
                    so = ol[mo > 0]
                    lipst = dict(rect=(int(r0), int(r1), int(c0), int(c1)),
                                 zone=torch.from_numpy(zb[r0:r1, c0:c1].copy()).to(device),
                                 mu_o=torch.tensor(so.mean(0), device=device).view(3, 1, 1),
                                 sd_o=torch.tensor(so.std(0) + 1e-3, device=device).view(3, 1, 1))
            self.lip.append(lipst)
            # gloss / clarity statics (mouth box, base-lip HF texture split at the base mouth line)
            zx0, zx1 = int(pts[:, 0].min() - 12), int(pts[:, 0].max() + 12)
            zy0, zy1 = int(pts[:, 1].min() - 10), int(pts[:, 1].max() + 30)
            zx0, zy0 = max(zx0, 0), max(zy0, 0)
            zx1, zy1 = min(zx1, Wc), min(zy1, Hc)
            ob = O[zy0:zy1, zx0:zx1]
            ol = cv2.cvtColor(np.clip(ob, 0, 255).astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
            mo = _lip_mask_cv(ol, np.ones(ob.shape[:2], bool))
            gst = dict(rect=(zy0, zy1, zx0, zx1), valid=False)
            if mo.sum() > 30:
                hf = (ob - cv2.GaussianBlur(ob, (0, 0), 1.0)) * mo[..., None]
                line = int(pts[12:20, 1].mean() - zy0)
                up, lo = hf.copy(), hf.copy()
                up[line:] = 0
                lo[:line] = 0
                rb = int(np.where(mo.any(1))[0].max())
                t = lambda a: torch.from_numpy(np.ascontiguousarray(a[..., ::-1].transpose(2, 0, 1))).to(device)
                gst.update(valid=True, up=t(up), lo=t(lo), rb=torch.tensor(float(rb), device=device))
            self.gloss.append(gst)
        self.O = torch.from_numpy(np.stack(O_l).transpose(0, 3, 1, 2).copy()).to(device, torch.float16)  # (N,3,Hc,Wc) RGB
        self.M = torch.from_numpy(np.stack(M_l)[:, None].copy()).to(device, torch.float16)  # (N,1,Hc,Wc)
        self.grain_t = torch.tensor(grain_t, device=device)
        # full frames RGB uint8 (N,H,W,3) for paste-back
        self.full = torch.from_numpy(np.stack([f[..., ::-1] for f in frames]).copy()).to(device)
        H, W = self.H, self.W
        self.paste = (max(0, ys), max(0, xs), min(H, ye), min(W, xe))  # sy1, sx1, sy2, sx2

    # ------------------------------------------------------------------------------------------- per-batch compose
    @torch.no_grad()
    def compose(self, faces, kidx, alpha=None, gen=None):
        # per-frame mouth boxes have different sizes: with cudnn.benchmark every new shape would be autotuned
        # (tens of ms, CPU spin-wait) -> use cuDNN heuristics for the small blur convolutions
        with torch.backends.cudnn.flags(enabled=True, benchmark=False, deterministic=False, allow_tf32=True):
            return self._compose(faces, kidx, alpha, gen)

    def _compose(self, faces, kidx, alpha=None, gen=None):
        """faces: (B,3,256,256) float RGB 0..255 (after temporal EMA). kidx: list of clip frame indices.
        alpha: (B,) speech activity 0..1 (idle bypass), None = 1. Returns (B,H,W,4) uint8 RGBA on the GPU."""
        o = self.o
        dev = self.dev
        B = faces.shape[0]
        ki = torch.as_tensor(kidx, device=dev, dtype=torch.long)
        O = self.O[ki].float()
        M = self.M[ki].float()
        G = O.clone()
        fy0, fy1, fx0, fx1 = self.fbox
        fh, fw = fy1 - fy0, fx1 - fx0
        down = fh < 256 or fw < 256
        G[:, :, fy0:fy1, fx0:fx1] = F.interpolate(faces, size=(fh, fw), mode="bicubic", align_corners=False,
                                                  antialias=down).clamp(0, 255)
        Ob = blur(O, 2.5)
        delta = (Ob - blur(G, 2.5)).abs().mean(1, keepdim=True)
        Mbin = (M > 0.3).float()
        if o.get("lf"):
            w = Mbin * torch.exp(-(delta / 8.0) ** 2)
            hs, wsm = self.Hc // 4, self.Wc // 4
            D = F.interpolate(O - G, size=(hs, wsm), mode="area")
            ww = F.interpolate(w, size=(hs, wsm), mode="area")
            num = blur(D * ww, 14 / 4)
            den = blur(ww, 14 / 4)
            corr = num / (den + 1e-4) * (den / 0.05).clamp(0, 1)
            G = G + F.interpolate(corr, size=(self.Hc, self.Wc), mode="bilinear", align_corners=False)
        texs = [None] * B
        for b in range(B):
            k = int(kidx[b])
            st = self.lip[k]
            if o.get("lipcolor") and st is not None:
                r0, r1, c0, c1 = st["rect"]
                gl = rgb2lab8(G[b, :, r0:r1, c0:c1].clamp(0, 255))
                mg = lip_mask(gl, st["zone"])
                n = mg.sum()
                valid = (n >= 30).float()
                nn = n.clamp(min=1.0)
                mu_g = (gl * mg).sum((1, 2), keepdim=True) / nn
                sd_g = torch.sqrt((((gl - mu_g) ** 2) * mg).sum((1, 2), keepdim=True) / nn) + 1e-3
                s = (st["sd_o"] / sd_g).clamp(0.7, 1.6)
                t = (gl - mu_g) * s + st["mu_o"]
                wgt = blur(mg[None], 1.5) * o["lipcolor"] * valid
                G[b, :, r0:r1, c0:c1] = lab82rgb((gl * (1 - wgt) + t * wgt).clamp(0, 255))
            gs = self.gloss[k]
            if o.get("clarity", 0) > 0 or o.get("gloss", 0) > 0:
                zy0, zy1, zx0, zx1 = gs["rect"]
                g = G[b, :, zy0:zy1, zx0:zx1]
                if o.get("clarity", 0) > 0:
                    g = g + o["clarity"] * (blur(g, 1.0) - blur(g, 3.0))
                if o.get("gloss", 0) > 0 and gs["valid"]:
                    gl = rgb2lab8(g.clamp(0, 255))
                    mg = lip_mask(gl, None)
                    valid = (mg.sum() > 30).float()
                    hh = mg.shape[0]
                    rows = mg.amax(1)
                    rg = (rows * torch.arange(hh, device=dev, dtype=torch.float32)).amax()
                    dy = (rg - gs["rb"]).clamp(-5, 25)
                    lo = shift_rows(gs["lo"], dy)
                    texs[b] = (gs["up"] + lo) * blur(mg[None], 1.0) * o["gloss"] * valid
                G[b, :, zy0:zy1, zx0:zx1] = g
        if o.get("detail", 0) > 0 or o.get("sharpen", 0) > 0 or o.get("grain"):
            hfO = O - blur(O, 1.2)
            hfG = G - blur(G, 1.2)
            ws = torch.exp(-(delta / 10.0) ** 2)
            if o.get("detail", 0) > 0:
                G = G + o["detail"] * ws * (hfO - hfG)
            if o.get("sharpen", 0) > 0:
                G = G + o["sharpen"] * (1 - ws) * hfG
            if o.get("grain"):
                cnt = (Mbin.sum((1, 2, 3)) * 3).clamp(min=1.0)
                mean = (hfG * Mbin).sum((1, 2, 3)) / cnt
                var = (((hfG - mean.view(B, 1, 1, 1)) ** 2) * Mbin).sum((1, 2, 3)) / cnt
                tgt = self.grain_t[ki]
                add = torch.sqrt((tgt ** 2 - var).clamp(min=0))
                add = torch.where(add > 0.3, add, torch.zeros_like(add))
                noise = torch.randn((B, 1, self.Hc, self.Wc), device=dev, generator=gen)
                G = G + (1 - ws) * noise * add.view(B, 1, 1, 1)
        for b in range(B):
            if texs[b] is not None:
                zy0, zy1, zx0, zx1 = self.gloss[int(kidx[b])]["rect"]
                wsz = torch.exp(-(delta[b, :, zy0:zy1, zx0:zx1] / 10.0) ** 2)
                G[b, :, zy0:zy1, zx0:zx1] += texs[b] * (1 - wsz)
        if alpha is not None:  # idle bypass: speech activity 0 -> untouched base pixels
            M = M * alpha.view(B, 1, 1, 1)
        bl = G * M + O * (1 - M)
        sy1, sx1, sy2, sx2 = self.paste
        xs, ys = self.mcrop[0], self.mcrop[1]
        out = torch.empty((B, self.H, self.W, 4), dtype=torch.uint8, device=dev)
        out[..., :3] = self.full[ki]
        out[..., 3] = 255
        reg = bl[:, :, sy1 - ys:sy2 - ys, sx1 - xs:sx2 - xs].round().clamp(0, 255).to(torch.uint8)
        out[:, sy1:sy2, sx1:sx2, :3] = reg.permute(0, 2, 3, 1)
        return out
