# LiveKit subscriber recorder for face-engine tests: records the publisher's video + audio track.
# Writes <out>.timeline.json (receive times, mouth-openness proxy per frame, continuous audio RMS on 10 ms bins)
# and with --video <out>.mp4 (CFR 25 fps grid on the receive clock + the received audio placed on the same clock,
# i.e. the A/V alignment as the subscriber got it).
# Usage: python lk_record.py <token.json> <out_prefix> <max_seconds> [--video] [--mouth x1,y1,x2,y2] [--rm-token]
#        [--identity-prefix face-]   (prints JSON lines: participant joins, first frames)
import asyncio, json, sys, time, os, subprocess, wave, argparse, shutil
import numpy as np
from livekit import rtc

ap = argparse.ArgumentParser()
ap.add_argument("token")
ap.add_argument("out")
ap.add_argument("max_seconds", type=float)
ap.add_argument("--video", action="store_true")
ap.add_argument("--mouth", default="362,448,468,516")
ap.add_argument("--rm-token", action="store_true")
ap.add_argument("--identity-prefix", default="face-")
ap.add_argument("--idle-exit", type=float, default=6.0, help="stop when the publisher has left for this long")
args = ap.parse_args()
MX1, MY1, MX2, MY2 = [int(v) for v in args.mouth.split(",")]
SR = 24000


def say(**kw):
    print(json.dumps(kw), flush=True)


async def main():
    cfg = json.load(open(args.token))
    if args.rm_token:
        os.remove(args.token)  # single use
    room = rtc.Room()
    vt, vm, vts = [], [], []
    chunks = []  # (t_recv, int16 array)
    latest = {"f": None, "wh": None}
    state = {"left": None, "pub": None}
    tasks = []

    def mouth(arr):
        c = arr[MY1:MY2, MX1:MX2, :3].astype(np.float32)
        y = 0.299 * c[..., 0] + 0.587 * c[..., 1] + 0.114 * c[..., 2]
        return float((y < 90).mean())

    async def read_video(track):
        async for ev in rtc.VideoStream(track, format=rtc.VideoBufferType.RGBA):
            t = time.monotonic()
            f = ev.frame
            arr = np.frombuffer(f.data, np.uint8).reshape(f.height, f.width, 4)
            if latest["wh"] is None:
                latest["wh"] = (f.width, f.height)
            if (f.width, f.height) != latest["wh"]:  # e.g. teardown frames: keep the recording consistent
                state["odd_frames"] = state.get("odd_frames", 0) + 1
                continue
            vt.append(t)
            vm.append(mouth(arr))
            vts.append(ev.timestamp_us)
            if len(vt) == 1:
                say(ev="first_video", w=f.width, h=f.height, t=t)
            if args.video:
                latest["f"] = arr.copy()

    async def read_audio(track):
        async for ev in rtc.AudioStream(track, sample_rate=SR, num_channels=1):
            t = time.monotonic()
            x = np.frombuffer(ev.frame.data, np.int16).copy()
            chunks.append((t, x))
            if len(chunks) == 1:
                say(ev="first_audio", t=t)

    @room.on("track_subscribed")
    def on_sub(track, pub, part):
        say(ev="subscribed", identity=part.identity, kind=int(track.kind))
        if track.kind == rtc.TrackKind.KIND_VIDEO:
            tasks.append(asyncio.ensure_future(read_video(track)))
        else:
            tasks.append(asyncio.ensure_future(read_audio(track)))

    @room.on("participant_connected")
    def on_join(p):
        say(ev="participant", identity=p.identity)
        if p.identity.startswith(args.identity_prefix):
            state["pub"] = p.identity
            state["left"] = None

    @room.on("participant_disconnected")
    def on_left(p):
        say(ev="participant_left", identity=p.identity)
        if p.identity == state["pub"]:
            state["left"] = time.monotonic()

    await room.connect(cfg["url"], cfg["token"], rtc.RoomOptions(auto_subscribe=True))
    for p in room.remote_participants.values():
        say(ev="participant", identity=p.identity)
        if p.identity.startswith(args.identity_prefix):
            state["pub"] = p.identity
    say(ev="connected", room=room.name, t=time.monotonic())
    T0 = time.monotonic()

    ff = None
    grid = {"t0": None, "n": 0}
    loop = asyncio.get_running_loop()

    async def writer():
        nonlocal ff
        while latest["f"] is None:
            await asyncio.sleep(0.01)
        w, h = latest["wh"]
        ff = subprocess.Popen([shutil.which("ffmpeg") or "ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo",
                               "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-r", "25", "-i", "-", "-c:v", "libx264",
                               "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", args.out + ".video.mp4"],
                              stdin=subprocess.PIPE)
        grid["t0"] = time.monotonic()
        while True:
            target = grid["t0"] + grid["n"] * 0.04
            d = target - time.monotonic()
            if d > 0:
                await asyncio.sleep(d)
            fr = latest["f"]
            await loop.run_in_executor(None, ff.stdin.write, fr.tobytes())
            grid["n"] += 1

    if args.video:
        tasks.append(asyncio.ensure_future(writer()))
    while time.monotonic() - T0 < args.max_seconds:
        await asyncio.sleep(0.2)
        if state["left"] is not None and time.monotonic() - state["left"] > args.idle_exit:
            break
    for t in tasks:
        t.cancel()
    await room.disconnect()
    # continuous audio: anchor the sample clock to the receive clock (median removes delivery jitter)
    res = {"room": room.name, "video_t": vt, "video_mouth": vm, "video_ts_us": vts, "mouth_rect": [MX1, MY1, MX2, MY2]}
    if chunks:
        a = np.concatenate([c for _, c in chunks])
        ends = np.cumsum([len(c) for _, c in chunks]) / SR
        anchor = float(np.median(np.array([t for t, _ in chunks]) - ends))  # receive time of sample 0
        res["audio_t0"] = anchor
        res["audio_samples"] = int(len(a))
        nb = len(a) // 240
        x = a[:nb * 240].astype(np.float32).reshape(nb, 240) / 32768.0
        res["audio_rms10"] = np.sqrt((x * x).mean(1) + 1e-12).round(5).tolist()
        with wave.open(args.out + ".wav", "wb") as wv:
            wv.setnchannels(1); wv.setsampwidth(2); wv.setframerate(SR)
            wv.writeframes(a.tobytes())
    if ff is not None:
        ff.stdin.close()
        ff.wait()
        res["grid_t0"] = grid["t0"]
        res["grid_frames"] = grid["n"]
        if chunks:  # mux: shift audio so both use the receive clock
            off = res["audio_t0"] - grid["t0"]
            if off >= 0:
                aargs = ["-itsoffset", f"{off:.3f}", "-i", args.out + ".wav"]
            else:
                aargs = ["-ss", f"{-off:.3f}", "-i", args.out + ".wav"]
            subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-y", "-loglevel", "error", "-i", args.out + ".video.mp4",
                            *aargs, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                            args.out + ".mp4"], check=False)
            res["mux_audio_offset_s"] = off
    json.dump(res, open(args.out + ".timeline.json", "w"))
    say(ev="done", frames=len(vt), audio_s=round(res.get("audio_samples", 0) / SR, 2), secs=round(time.monotonic() - T0, 1))


asyncio.run(main())
