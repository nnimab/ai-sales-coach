# Contact sheet of crops from a video. Usage:
# python sheet.py <video> <out.jpg> <x1,y1,x2,y2> <start:end:step> [cols=6] [scale=1.0]
import sys
import numpy as np
import cv2

v, out, box, rng = sys.argv[1:5]
cols = int(sys.argv[5]) if len(sys.argv) > 5 else 6
sc = float(sys.argv[6]) if len(sys.argv) > 6 else 1.0
x1, y1, x2, y2 = [int(t) for t in box.split(",")]
a, b, c = [int(t) for t in rng.split(":")]
want = set(range(a, b, c))
cap = cv2.VideoCapture(v)
tiles, i = [], 0
while True:
    ok, f = cap.read()
    if not ok or i >= b:
        break
    if i in want:
        t = f[y1:y2, x1:x2].copy()
        if sc != 1.0:
            t = cv2.resize(t, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
        cv2.putText(t, str(i), (5, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
        tiles.append(t)
    i += 1
while len(tiles) % cols:
    tiles.append(np.zeros_like(tiles[0]))
rows = [np.concatenate(tiles[r:r + cols], 1) for r in range(0, len(tiles), cols)]
cv2.imwrite(out, np.concatenate(rows, 0), [cv2.IMWRITE_JPEG_QUALITY, 90])
print("sheet", out, len(tiles))
