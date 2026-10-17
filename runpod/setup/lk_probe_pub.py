# LiveKit outbound probe: join room, publish 640x360 test pattern + 440 Hz tone, report selected ICE path.
# Usage: python lk_probe_pub.py <token.json> <seconds> [relay] [--dump]
import asyncio, json, sys, time, math, socket, struct, os, logging
import numpy as np
from livekit import rtc
from google.protobuf.json_format import MessageToDict

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
cfg = json.load(open(sys.argv[1]))
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 20
RELAY = "relay" in sys.argv[3:]


def stun_probe(host, port):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        tid = os.urandom(12)
        s.sendto(struct.pack("!HHI", 1, 0, 0x2112A442) + tid, (host, port))
        data, addr = s.recvfrom(2048)
        return f"UDP reply {len(data)}B from {addr}"
    except Exception as e:
        return f"no UDP reply ({e.__class__.__name__})"


def report(stats_list, t):
    d = [MessageToDict(s, preserving_proto_field_name=True) for s in stats_list]
    byid = {}
    for y in d:
        for key in ("local_candidate", "remote_candidate"):
            if key in y:
                byid[y.get("rtc", {}).get("id")] = y[key].get("candidate", y[key])
    for x in d:
        if "candidate_pair" in x:
            cp = x["candidate_pair"].get("candidate_pair", x["candidate_pair"])
            if cp.get("nominated"):
                lc = byid.get(cp.get("local_candidate_id"))
                rc = byid.get(cp.get("remote_candidate_id"))
                print(f"[t={t:.0f}s] NOMINATED pair state={cp.get('state')} rtt={cp.get('current_round_trip_time')} sent={cp.get('bytes_sent')}", flush=True)
                if lc:
                    print("   local :", {k: lc.get(k) for k in ("candidate_type", "protocol", "relay_protocol", "url", "port")}, flush=True)
                if rc:
                    print("   remote:", {k: rc.get(k) for k in ("candidate_type", "protocol", "address", "port")}, flush=True)
    for x in d:
        if "outbound_rtp" in x:
            o = x["outbound_rtp"]
            print("   outbound:", o.get("stream", {}).get("kind"), "bytes", o.get("sent", {}).get("bytes_sent"),
                  "fps", o.get("outbound", {}).get("frames_per_second"), flush=True)
    return d


async def main():
    print("STUN google:", stun_probe("stun.l.google.com", 19302), flush=True)
    print("STUN cloudflare:", stun_probe("stun.cloudflare.com", 3478), flush=True)
    room = rtc.Room()
    if RELAY:
        opts = rtc.RoomOptions(auto_subscribe=False, rtc_config=rtc.RtcConfiguration(
            ice_transport_type=rtc.IceTransportType.TRANSPORT_RELAY))
    else:
        opts = rtc.RoomOptions(auto_subscribe=False)
    t0 = time.time()
    await room.connect(cfg["url"], cfg["token"], options=opts)
    print(f"CONNECTED in {time.time()-t0:.2f}s room={room.name} relay_forced={RELAY}", flush=True)
    W, H = 640, 360
    vsrc = rtc.VideoSource(W, H)
    vtrack = rtc.LocalVideoTrack.create_video_track("pattern", vsrc)
    await room.local_participant.publish_track(vtrack, rtc.TrackPublishOptions(
        source=rtc.TrackSource.SOURCE_CAMERA,
        video_encoding=rtc.VideoEncoding(max_framerate=25, max_bitrate=1_000_000)))
    asrc = rtc.AudioSource(48000, 1, queue_size_ms=200)
    atrack = rtc.LocalAudioTrack.create_audio_track("tone", asrc)
    await room.local_participant.publish_track(atrack, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
    print("PUBLISHED video+audio", flush=True)
    bars = np.zeros((H, W, 4), np.uint8)
    cols = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255), (0, 0, 0)]
    for i, c in enumerate(cols):
        bars[:, i * W // 8:(i + 1) * W // 8, :3] = c
    bars[..., 3] = 255
    t1 = time.time()

    async def audio_loop():
        n = 0
        while time.time() - t1 < DUR:
            t = (np.arange(480) + n) / 48000
            pcm = (0.2 * 32767 * np.sin(2 * math.pi * 440 * t)).astype(np.int16)
            n += 480
            await asrc.capture_frame(rtc.AudioFrame(pcm.tobytes(), 48000, 1, 480))

    at = asyncio.create_task(audio_loop())
    k = 0
    next_stats = t1 + 5
    while time.time() - t1 < DUR:
        f = bars.copy()
        x = int((k * 8) % W)
        f[:, max(0, x - 4):x + 4, :3] = 128
        vsrc.capture_frame(rtc.VideoFrame(W, H, rtc.VideoBufferType.RGBA, f.tobytes()))
        k += 1
        if time.time() > next_stats:
            next_stats += 5
            st = await room.get_rtc_stats()
            d = report(st.publisher_stats, time.time() - t1)
        await asyncio.sleep(0.04)
    await at
    if "--dump" in sys.argv:
        st = await room.get_rtc_stats()
        json.dump([MessageToDict(s, preserving_proto_field_name=True) for s in st.publisher_stats],
                  open("/workspace/lk_pub_stats.json", "w"), indent=1)
    await room.disconnect()
    print("DISCONNECTED", flush=True)


asyncio.run(main())
