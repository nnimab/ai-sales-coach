# Wan2.2 TI2V-5B (diffusers) image-to-video: idle base clip for MuseTalk.
# Usage: python wan_idle.py <out_dir> <seed1,seed2,...> [steps]
# Latents are saved before decoding (resumable); decode runs after freeing the transformer (container RAM limit 50 GB).
import sys, time, json, os, gc
import numpy as np
import torch
from PIL import Image
from diffusers import WanImageToVideoPipeline, AutoencoderKLWan
from diffusers.utils import export_to_video

OUTD = sys.argv[1]
SEEDS = [int(s) for s in sys.argv[2].split(",")]
STEPS = int(sys.argv[3]) if len(sys.argv) > 3 else 50
W, H = 704, 896
NF = 121  # 24 fps native -> ~5 s
os.makedirs(OUTD, exist_ok=True)

PROMPT = "她保持静止，轻微呼吸起伏；中途缓慢眨一次眼；结尾头部回到最初位置。嘴唇始终闭合。固定机位。"
# Wan default negative prompt minus the 'static' terms (we WANT a still clip), plus talking/mouth terms.
NEG = ("色调艳丽，过曝，细节模糊不清，字幕，风格，作品，画作，画面，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，"
       "多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，杂乱的背景，三条腿，背景人很多，倒着走，"
       "说话，张嘴，嘴唇移动，露出牙齿，镜头移动")

t0 = time.time()
mid = "/root/wan"
vae = AutoencoderKLWan.from_pretrained(mid, subfolder="vae", torch_dtype=torch.float32)
todo = [s for s in SEEDS if not os.path.exists(f"{OUTD}/wang_s{s}_latents.pt")]
if todo:
    pipe = WanImageToVideoPipeline.from_pretrained(mid, vae=vae, torch_dtype=torch.bfloat16)
    pipe.enable_model_cpu_offload()
    print(f"loaded in {time.time()-t0:.0f}s", flush=True)
    img = Image.open("/workspace/faces/wang/wang.webp").convert("RGB")
    iw, ih = img.size
    target = W / H
    if iw / ih < target:  # too tall -> trim top/bottom equally
        nh = round(iw / target); top = (ih - nh) // 2
        img = img.crop((0, top, iw, top + nh))
    else:
        nw = round(ih * target); left = (iw - nw) // 2
        img = img.crop((left, 0, left + nw, ih))
    img = img.resize((W, H), Image.LANCZOS)
    img.save(f"{OUTD}/input_704x896.png")
    for s in todo:
        g = torch.Generator("cuda").manual_seed(s)
        t1 = time.time()
        def cb(p, i, t, kw):
            if i % 10 == 0:
                print(f"seed {s} step {i} {time.time()-t1:.0f}s", flush=True)
            return kw
        lat = pipe(image=img, prompt=PROMPT, negative_prompt=NEG, height=H, width=W, num_frames=NF,
                   guidance_scale=5.0, num_inference_steps=STEPS, generator=g, output_type="latent",
                   callback_on_step_end=cb).frames
        torch.save(lat.cpu(), f"{OUTD}/wang_s{s}_latents.pt")
        json.dump({"seed": s, "steps": STEPS, "gen_s": time.time() - t1, "w": W, "h": H, "nf": NF,
                   "vram_peak_gb": torch.cuda.max_memory_allocated() / 1e9, "prompt": PROMPT, "neg": NEG},
                  open(f"{OUTD}/wang_s{s}_meta.json", "w"), ensure_ascii=False, indent=1)
        print(f"seed {s} latents saved, gen {time.time()-t1:.0f}s", flush=True)
    pipe.remove_all_hooks()
    del pipe
    gc.collect(); torch.cuda.empty_cache()

vae = vae.to("cuda")
from diffusers.video_processor import VideoProcessor
vp = VideoProcessor(vae_scale_factor=16)
for s in SEEDS:
    outp = f"{OUTD}/wang_s{s}_24fps.mp4"
    if os.path.exists(outp):
        continue
    t1 = time.time()
    lat = torch.load(f"{OUTD}/wang_s{s}_latents.pt").to("cuda", torch.float32)
    mean = torch.tensor(vae.config.latents_mean).view(1, vae.config.z_dim, 1, 1, 1).to(lat)
    std = 1.0 / torch.tensor(vae.config.latents_std).view(1, vae.config.z_dim, 1, 1, 1).to(lat)
    lat = lat / std + mean
    with torch.no_grad():
        video = vae.decode(lat, return_dict=False)[0]
    frames = vp.postprocess_video(video, output_type="np")[0]
    export_to_video(frames, outp, fps=24)
    print(f"seed {s} decoded in {time.time()-t1:.0f}s, vram peak {torch.cuda.max_memory_allocated()/1e9:.1f} GB", flush=True)
print(f"DONE total {time.time()-t0:.0f}s", flush=True)
