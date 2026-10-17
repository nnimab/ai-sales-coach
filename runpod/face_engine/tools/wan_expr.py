# Wan2.2 TI2V-5B (diffusers) image-to-video: expression idle clips for MuseTalk (same first frame as the neutral clip).
# Usage: python wan_expr.py <out_dir> <expr:seed,expr:seed,...> [steps]
# Latents are saved before decoding (resumable); decode runs after freeing the transformer (container RAM limit 50 GB).
import sys, time, json, os, gc
import numpy as np
import torch
from PIL import Image
from diffusers import WanImageToVideoPipeline, AutoencoderKLWan
from diffusers.utils import export_to_video

OUTD = sys.argv[1]
JOBS = [(j.split(":")[0], int(j.split(":")[1])) for j in sys.argv[2].split(",")]
STEPS = int(sys.argv[3]) if len(sys.argv) > 3 else 50
W, H = 704, 896
NF = 121  # 24 fps native -> ~5 s
os.makedirs(OUTD, exist_ok=True)

PROMPTS = {
    "worried": "她神情担忧：眉头轻轻皱起，眉心微蹙，眼神略显不安，轻微呼吸起伏；中途缓慢眨一次眼；头部几乎不动，结尾回到最初位置。嘴唇始终闭合，不说话。手保持不动。固定机位。",
    "happy": "她神情愉快：嘴角微微上扬，抿着嘴温柔微笑，眼睛带笑意，轻微呼吸起伏；中途缓慢眨一次眼；头部几乎不动，结尾回到最初位置。嘴唇始终闭合，不露牙齿，不说话。手保持不动。固定机位。",
    "annoyed": "她神情不耐烦：眉头压低，微微皱眉，嘴唇抿紧，眼神略带不满，轻微呼吸起伏；中途缓慢眨一次眼；头部几乎不动，结尾回到最初位置。嘴唇始终闭合，不说话。手保持不动。固定机位。",
}
NEG = ("色调艳丽，过曝，细节模糊不清，字幕，风格，作品，画作，画面，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，"
       "多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，杂乱的背景，三条腿，背景人很多，倒着走，"
       "说话，张嘴，嘴唇移动，露出牙齿，大笑，镜头移动，手部移动")

t0 = time.time()
mid = "/root/wan"
vae = AutoencoderKLWan.from_pretrained(mid, subfolder="vae", torch_dtype=torch.float32)
todo = [(e, s) for e, s in JOBS if not os.path.exists(f"{OUTD}/{e}_s{s}_latents.pt")]
if todo:
    pipe = WanImageToVideoPipeline.from_pretrained(mid, vae=vae, torch_dtype=torch.bfloat16)
    pipe.enable_model_cpu_offload()
    print(f"loaded in {time.time()-t0:.0f}s", flush=True)
    img = Image.open("/workspace/wan_out/input_704x896.png").convert("RGB")  # same crop/first frame as neutral idle
    assert img.size == (W, H)
    for e, s in todo:
        g = torch.Generator("cuda").manual_seed(s)
        t1 = time.time()

        def cb(p, i, t, kw):
            if i % 10 == 0:
                print(f"{e} seed {s} step {i} {time.time()-t1:.0f}s", flush=True)
            return kw
        lat = pipe(image=img, prompt=PROMPTS[e], negative_prompt=NEG, height=H, width=W, num_frames=NF,
                   guidance_scale=5.0, num_inference_steps=STEPS, generator=g, output_type="latent",
                   callback_on_step_end=cb).frames
        torch.save(lat.cpu(), f"{OUTD}/{e}_s{s}_latents.pt")
        json.dump({"expr": e, "seed": s, "steps": STEPS, "gen_s": time.time() - t1, "w": W, "h": H, "nf": NF,
                   "vram_peak_gb": torch.cuda.max_memory_allocated() / 1e9, "prompt": PROMPTS[e], "neg": NEG},
                  open(f"{OUTD}/{e}_s{s}_meta.json", "w"), ensure_ascii=False, indent=1)
        print(f"{e} seed {s} latents saved, gen {time.time()-t1:.0f}s", flush=True)
    pipe.remove_all_hooks()
    del pipe
    gc.collect(); torch.cuda.empty_cache()

vae = vae.to("cuda")
from diffusers.video_processor import VideoProcessor
vp = VideoProcessor(vae_scale_factor=16)
for e, s in JOBS:
    outp = f"{OUTD}/{e}_s{s}_24fps.mp4"
    if os.path.exists(outp):
        continue
    t1 = time.time()
    lat = torch.load(f"{OUTD}/{e}_s{s}_latents.pt").to("cuda", torch.float32)
    mean = torch.tensor(vae.config.latents_mean).view(1, vae.config.z_dim, 1, 1, 1).to(lat)
    std = 1.0 / torch.tensor(vae.config.latents_std).view(1, vae.config.z_dim, 1, 1, 1).to(lat)
    lat = lat / std + mean
    with torch.no_grad():
        video = vae.decode(lat, return_dict=False)[0]
    frames = vp.postprocess_video(video, output_type="np")[0]
    export_to_video(frames, outp, fps=24)
    print(f"{e} seed {s} decoded in {time.time()-t1:.0f}s", flush=True)
    del lat, video
    torch.cuda.empty_cache()
print(f"DONE total {time.time()-t0:.0f}s", flush=True)
