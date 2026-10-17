# Small MuseTalk 1.5 helper lib built on LiveTalking's port (Apache-2.0, https://github.com/lipku/LiveTalking).
# Run with cwd=/workspace/LiveTalking so ./models/... resolves.
import os, sys, time, pickle, glob
# The pod's cgroup CPU quota is ~7.65 vCPUs although nproc says 96: default 96-thread pools get throttled
# in 100 ms CFS periods (latency spikes). Keep CPU thread pools small.
for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, "4")
import numpy as np
import cv2
import torch

LT = "/workspace/LiveTalking"
if LT not in sys.path:
    sys.path.insert(0, LT)
os.chdir(LT)

import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from avatars.musetalk.models.vae import VAE  # noqa: E402
from avatars.musetalk.models.unet import PositionalEncoding  # noqa: E402
from avatars.musetalk.whisper.audio2feature import Audio2Feature  # noqa: E402

DEV = torch.device("cuda")
torch.set_num_threads(int(os.environ.get("OMP_NUM_THREADS", "4")))
cv2.setNumThreads(4)


def load_vae():
    vae = VAE(model_path="./models/sd-vae")
    vae.vae = vae.vae.half().to(DEV)
    return vae


def load_models():
    """Same weights/precision as LiveTalking's musetalk load_model(), but the fp32 UNet is halved on CPU first
    (avoids a 3.4 GB fp32 copy on the GPU)."""
    from diffusers import UNet2DConditionModel
    vae = load_vae()
    cfg = json.load(open("./models/musetalkV15/musetalk.json"))
    m = UNet2DConditionModel(**cfg)
    m.load_state_dict(torch.load("./models/musetalkV15/unet.pth", map_location="cpu"))
    m = m.half().to(DEV).eval()
    unet = SimpleNamespace(model=m, device=DEV)
    pe = PositionalEncoding(d_model=384).half().to(DEV)
    ts = torch.tensor([0], device=DEV)
    ap = Audio2Feature(model_path="./models/whisper")
    return vae, unet, pe, ts, ap


def read_wav16k(path):
    import soundfile as sf, resampy
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    x = x.mean(1)
    if sr != 16000:
        x = resampy.resample(x, sr, 16000)
    return x.astype(np.float32)


@torch.no_grad()
def whisper_feats(ap, wav16k):
    """(T50, 5, 384) whisper-tiny encoder hidden states at 50 Hz (20 ms). Input <= 30 s."""
    f = ap.audio2feat(wav16k)  # (1500, 5, 384) for a 30 s padded window
    n = int(np.ceil(len(wav16k) / 320))
    return f[:n]


def frame_chunk(feat, i, fps=25, win=(2, 3)):
    """MuseTalk v1.5 window for video frame i: features [2i-4, 2i+6) (= -80 ms .. +120 ms), clamped."""
    m = 50 // fps
    c = i * m
    idx = np.clip(np.arange(c - win[0] * m, c + win[1] * m), 0, len(feat) - 1)
    return feat[idx].reshape(-1, 384)  # (50, 384)


@torch.no_grad()
def infer(models, latents, chunks):
    """latents: list of (1,8,32,32) tensors; chunks: list of (50,384). Returns BGR uint8 (B,256,256,3)."""
    vae, unet, pe, ts, _ = models
    lat = torch.cat(latents, 0).to(DEV, dtype=unet.model.dtype)
    a = torch.from_numpy(np.stack(chunks)).to(DEV, dtype=unet.model.dtype)
    a = pe(a)
    pred = unet.model(lat, ts, encoder_hidden_states=a).sample
    return vae.decode_latents(pred)


def mirror_index(size, index):
    turn, res = divmod(index, size)
    return res if turn % 2 == 0 else size - res - 1


def load_avatar(adir, mask_name="m90_90"):
    frames = [cv2.imread(p) for p in sorted(glob.glob(f"{adir}/full_imgs/*.png"))]
    coords = pickle.load(open(f"{adir}/coords.pkl", "rb"))
    latents = torch.load(f"{adir}/latents.pt")
    masks = [cv2.imread(p, cv2.IMREAD_GRAYSCALE) for p in sorted(glob.glob(f"{adir}/mask_{mask_name}/*.png"))]
    mcoords = pickle.load(open(f"{adir}/mask_coords_{mask_name}.pkl", "rb"))
    return dict(frames=frames, coords=coords, latents=latents, masks=masks, mcoords=mcoords)


def crop_pad(img, box):
    """Crop img (H,W[,C]) with box possibly outside bounds (zero pad)."""
    x1, y1, x2, y2 = box
    H, W = img.shape[:2]
    out = np.zeros((y2 - y1, x2 - x1) + img.shape[2:], img.dtype)
    sx1, sy1, sx2, sy2 = max(0, x1), max(0, y1), min(W, x2), min(H, y2)
    if sx2 > sx1 and sy2 > sy1:
        out[sy1 - y1:sy2 - y1, sx1 - x1:sx2 - x1] = img[sy1:sy2, sx1:sx2]
    return out


def paste_back(frame, face256, bbox, mask, mcrop, keep=None):
    """Blend generated 256x256 face into the frame. mask: uint8 (crop-box sized). keep: optional float [0,1]
    full-frame mask of pixels to always keep from the original frame (e.g. the hand)."""
    x1, y1, x2, y2 = bbox
    xs, ys, xe, ye = mcrop
    out = frame.copy()
    face = cv2.resize(face256, (x2 - x1, y2 - y1), interpolation=cv2.INTER_LINEAR)
    large = crop_pad(frame, mcrop).astype(np.float32)
    gen = large.copy()
    gen[y1 - ys:y2 - ys, x1 - xs:x2 - xs] = face
    m = mask.astype(np.float32) / 255.0
    if keep is not None:
        m = m * (1.0 - crop_pad(keep, mcrop))
    blended = gen * m[..., None] + large * (1 - m[..., None])
    H, W = frame.shape[:2]
    sx1, sy1, sx2, sy2 = max(0, xs), max(0, ys), min(W, xe), min(H, ye)
    out[sy1:sy2, sx1:sx2] = blended[sy1 - ys:sy2 - ys, sx1 - xs:sx2 - xs].round().clip(0, 255).astype(np.uint8)
    return out
