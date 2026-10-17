"""Live test of DittoStream: a 25 Hz consumer thread calls get_frame() (like the engine), the main thread pushes the
Leda audio in 100 ms chunks in REAL TIME with silence before/after. Records tick timing, repeated frames (drops),
push->emit latency, writes an H.264+AAC mp4 of exactly what the consumer received (frames + their audio slices).
Usage: /workspace/ditto_live/run_py.sh /workspace/ditto_live/test_stream.py [--out x.mp4] [--set key=value ...]
       [--burst 1 (push the whole utterance at once, like a fast TTS)] [--flatout 6 (max-throughput test, s)]
       [--interrupt 1 (second utterance interrupted after 1.5 s)]
"""
import os, sys, time, json, threading, queue, subprocess, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import soundfile as sf

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="/workspace/ditto_live/out/live_test.mp4")
ap.add_argument("--audio", default="/workspace/audio/leda.wav")
ap.add_argument("--pre", type=float, default=2.0)
ap.add_argument("--post", type=float, default=2.0)
ap.add_argument("--chunk", type=float, default=0.1)
ap.add_argument("--burst", type=int, default=0)
ap.add_argument("--interrupt", type=int, default=0)
ap.add_argument("--flatout", type=float, default=0)
ap.add_argument("--set", nargs="*", default=[])
args = ap.parse_args()

tuning = {}
for kv in args.set:
    k, v = kv.split("=", 1)
    try:
        v = json.loads(v)
    except Exception:
        pass
    tuning[k] = v

FF = os.environ.get("FFMPEG", "/workspace/face_engine/bin/ffmpeg")
WORK = "/root/ditto_live_work"
os.makedirs(WORK, exist_ok=True)
os.makedirs(os.path.dirname(args.out), exist_ok=True)

x, sr = sf.read(args.audio, dtype="int16", always_2d=True)
x = x[:, 0]
if sr != 24000:
    from scipy.signal import resample_poly
    from math import gcd
    g = gcd(sr, 24000)
    x = np.clip(resample_poly(x.astype(np.float32), 24000 // g, sr // g), -32768, 32767).astype(np.int16)
utt = x.tobytes()
n_utt = len(x)

from ditto_stream import DittoStream, SPT
t_i = time.perf_counter()
s = DittoStream(**tuning)
s.start()
print(f"init {s.init_s:.1f}s ready {s.ready_s:.1f}s start {time.perf_counter() - t_i:.1f}s ahead {s.ahead} latency_s {s.latency_s:.2f}", flush=True)
W, H = s.W, s.H
vid_tmp = f"{WORK}/v.mp4"
ff = subprocess.Popen([FF, "-nostdin", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                       "-r", "25", "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                       vid_tmp], stdin=subprocess.PIPE)
wq = queue.Queue()


def writer():
    while True:
        f = wq.get()
        if f is None:
            break
        ff.stdin.write(f)


wt = threading.Thread(target=writer, daemon=True)
wt.start()
rec = []
audio_out = []
stop = threading.Event()


def consumer():
    t0 = time.perf_counter()
    i = 0
    while not stop.is_set():
        tgt = t0 + i * 0.04
        while True:
            d = tgt - time.perf_counter()
            if d <= 0:
                break
            time.sleep(min(d, 0.005))
        tc = time.perf_counter()
        rgb, pcm, uid, last = s.get_frame(1.0)
        tr = time.perf_counter()
        wq.put(rgb.tobytes())
        audio_out.append(pcm)
        rec.append((tc, tr, uid, bool(last), len(pcm), id(rgb)))
        i += 1


ct = threading.Thread(target=consumer, daemon=True)
ct.start()
time.sleep(args.pre)
push_t = []
t_s = time.perf_counter()
step = int(args.chunk * 24000) * 2
if args.burst:
    push_t.append((0, time.perf_counter()))
    s.push_audio(utt, "leda")
else:
    for j, off in enumerate(range(0, len(utt), step)):
        tgt = t_s + j * args.chunk
        while time.perf_counter() < tgt:
            time.sleep(0.002)
        push_t.append((off // 2, time.perf_counter()))
        s.push_audio(utt[off:off + step], "leda")
s.end_utterance("leda")
t_end_push = time.perf_counter()
time.sleep(max(0.0, n_utt / 24000 - (t_end_push - t_s)) + s.latency_s + 0.5)
if args.interrupt:
    s.push_audio(utt, "leda2")
    s.end_utterance("leda2")
    time.sleep(1.5 + s.latency_s)
    t_int = time.perf_counter()
    s.interrupt()
    time.sleep(1.0)
time.sleep(args.post)
stop.set()
ct.join()
st_main = s.stats()

flat = None
if args.flatout > 0:  # max throughput: consumer pulls as fast as possible, waits for every frame
    s.cfg["max_frame_wait"] = 1.0
    r0 = s.n_rendered
    tf = time.perf_counter()
    n = 0
    while time.perf_counter() - tf < args.flatout:
        s.get_frame(1.0)
        n += 1
    dt = time.perf_counter() - tf
    flat = {"ticks_per_s": round(n / dt, 2), "rendered_per_s": round((s.n_rendered - r0) / dt, 2)}
    print("FLATOUT", flat, flush=True)
s.close()
wq.put(None)
wt.join()
ff.stdin.close()
ff.wait()

# ---- analysis
tc = np.array([r[0] for r in rec])
tr = np.array([r[1] for r in rec])
uids = [r[2] for r in rec]
call_ms = (tr - tc) * 1000
ivl = np.diff(tr) * 1000
ids = [r[5] for r in rec]
pcm_all = np.frombuffer(b"".join(audio_out), np.int16)
res = {"ticks": len(rec), "consumer_fps": round((len(rec) - 1) / (tr[-1] - tr[0]), 3),
       "get_frame_ms_p50": round(float(np.median(call_ms)), 2), "get_frame_ms_p99": round(float(np.percentile(call_ms, 99)), 2),
       "get_frame_ms_max": round(float(call_ms.max()), 2),
       "tick_interval_ms_p1_p99": [round(float(np.percentile(ivl, 1)), 1), round(float(np.percentile(ivl, 99)), 1)],
       "stream_stats": st_main, "tuning": tuning, "burst": args.burst, "flatout": flat}
lt = [i for i, u in enumerate(uids) if u == "leda"]
if lt:
    first = lt[0]
    leda_pcm = pcm_all[first * SPT:(lt[-1] + 1) * SPT]
    # underrun gaps inside the utterance (all-zero ticks with uid leda before the last one)
    gaps = sum(1 for i in lt[:-1] if not np.any(pcm_all[i * SPT:(i + 1) * SPT]))
    ok = np.array_equal(leda_pcm[:n_utt], x[:len(leda_pcm[:n_utt])])
    lats = []
    for off, tp in push_t:
        k = first + off // SPT
        if k < len(rec):
            lats.append(rec[k][1] + (off % SPT) / 24000 - tp)
    lats = np.array(lats)
    res.update({"leda_ticks": len(lt), "leda_underrun_gap_ticks": gaps, "leda_audio_bit_exact": bool(ok),
                "last_flag_ticks": [i for i, r in enumerate(rec) if r[3]],
                "first_speech_tick_s_after_first_push": round(rec[first][1] - push_t[0][1], 3),
                "push_to_emit_latency_s_p50": round(float(np.median(lats)), 3),
                "push_to_emit_latency_s_min": round(float(lats.min()), 3),
                "push_to_emit_latency_s_max": round(float(lats.max()), 3)})
rep = sum(1 for a, b in zip(ids[:-1], ids[1:]) if a == b)
res["consumer_saw_repeated_frames"] = rep
wav = f"{WORK}/a.wav"
sf.write(wav, pcm_all, 24000, subtype="PCM_16")
subprocess.run([FF, "-nostdin", "-loglevel", "error", "-y", "-i", vid_tmp, "-i", wav, "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", args.out], check=True)
json.dump(res, open(args.out + ".json", "w"), indent=1)
print("RESULT", json.dumps(res), flush=True)
sys.stdout.flush()
os._exit(0)
