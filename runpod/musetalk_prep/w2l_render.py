# Offline wav2lip256 render using LiveTalking's wav2lip avatar format (comparison only).
# Usage: python w2l_render.py <avatar_dir> <wav> <out.mp4> [--keep keep.png]
import os, sys, glob, pickle, time, json, subprocess, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import cv2
import torch
import mtlib  # cwd -> LiveTalking
from avatars.wav2lip.models import Wav2Lip
from avatars.wav2lip import audio

ap = argparse.ArgumentParser()
ap.add_argument("adir"); ap.add_argument("wav"); ap.add_argument("out")
ap.add_argument("--keep", default=None)
ap.add_argument("--bs", type=int, default=16)
args = ap.parse_args()

dev = "cuda"
ck = torch.load("./models/wav2lip.pth", map_location=dev)
sd = {k.replace("module.", ""): v for k, v in ck["state_dict"].items()}
model = Wav2Lip(); model.load_state_dict(sd); model = model.to(dev).eval()
frames = [cv2.imread(p) for p in sorted(glob.glob(f"{args.adir}/full_imgs/*.png"))]
faces = [cv2.imread(p) for p in sorted(glob.glob(f"{args.adir}/face_imgs/*.png"))]
coords = pickle.load(open(f"{args.adir}/coords.pkl", "rb"))
keep = cv2.imread(args.keep, cv2.IMREAD_GRAYSCALE).astype(np.float32)[..., None] / 255.0 if args.keep else None
N = len(frames)
wav = mtlib.read_wav16k(args.wav)
pad = np.zeros(8000, np.float32)
wav = np.concatenate([pad, wav, pad])
mel = audio.melspectrogram(wav)
nf = int(np.ceil(len(wav) / 16000 * 25))
chunks = []
for i in range(nf):
    s = int(i * 80 / 25)
    c = mel[:, s:s + 16] if s + 16 <= mel.shape[1] else mel[:, -16:]
    chunks.append(c)
out = []
t_inf = 0
with torch.no_grad():
    for b in range(0, nf, args.bs):
        ids = list(range(b, min(nf, b + args.bs)))
        fidx = [mtlib.mirror_index(N, i) for i in ids]
        img = np.asarray([faces[k] for k in fidx])
        msk = img.copy(); msk[:, img.shape[1] // 2:] = 0
        x = np.concatenate((msk, img), axis=3) / 255.
        m = np.asarray([chunks[i] for i in ids])[..., None]
        x = torch.FloatTensor(np.transpose(x, (0, 3, 1, 2))).to(dev)
        m = torch.FloatTensor(np.transpose(m, (0, 3, 1, 2))).to(dev)
        torch.cuda.synchronize(); t = time.time()
        pred = model(m, x)
        torch.cuda.synchronize(); t_inf += time.time() - t
        pred = pred.cpu().numpy().transpose(0, 2, 3, 1) * 255.
        for k, p in zip(fidx, pred):
            y1, y2, x1, x2 = coords[k]
            f = frames[k].copy()
            f[y1:y2, x1:x2] = cv2.resize(p.astype(np.uint8), (x2 - x1, y2 - y1))
            if keep is not None:
                f = (f * (1 - keep) + frames[k] * keep).round().astype(np.uint8)
            out.append(f)
h, w = out[0].shape[:2]
import soundfile as sf
tmp = args.out + ".tmp.wav"; sf.write(tmp, wav, 16000)
p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", "25",
                      "-i", "-", "-i", tmp, "-c:v", "libx264", "-crf", "17", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", args.out],
                     stdin=subprocess.PIPE)
for f in out:
    p.stdin.write(np.ascontiguousarray(f).tobytes())
p.stdin.close(); p.wait(); os.remove(tmp)
print(json.dumps({"frames": nf, "infer_s": t_inf, "fps": nf / t_inf}))
