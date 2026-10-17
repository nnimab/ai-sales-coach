# Build an extra blend-mask variant for a prepared avatar.
# Usage: python mt_masks.py <avatar_dir> <left_cheek_width> <right_cheek_width> [upper_boundary_ratio=0.5] [mode=jaw]
import os, sys, glob, pickle
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv2
import mtlib
from avatars.musetalk.utils.face_parsing import FaceParsing
from avatars.musetalk.utils.blending import get_image_prepare_material

adir, lw, rw = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
ubr = float(sys.argv[4]) if len(sys.argv) > 4 else 0.5
mode = sys.argv[5] if len(sys.argv) > 5 else "jaw"
name = f"m{lw}_{rw}" + ("" if ubr == 0.5 else f"_u{round(ubr*100)}") + ("" if mode == "jaw" else f"_{mode}")
coords = pickle.load(open(f"{adir}/coords.pkl", "rb"))
fp = FaceParsing(left_cheek_width=lw, right_cheek_width=rw)
os.makedirs(f"{adir}/mask_{name}", exist_ok=True)
mc = []
for i, p in enumerate(sorted(glob.glob(f"{adir}/full_imgs/*.png"))):
    f = cv2.imread(p)
    m, cb = get_image_prepare_material(f, coords[i], fp=fp, mode=mode, upper_boundary_ratio=ubr)
    cv2.imwrite(f"{adir}/mask_{name}/{i:08d}.png", m)
    mc.append(cb)
pickle.dump(mc, open(f"{adir}/mask_coords_{name}.pkl", "wb"))
print("wrote mask set", name, len(mc))
