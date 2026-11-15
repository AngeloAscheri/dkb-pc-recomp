# 4GB VRAM & Laptop Optimization Guide

Tuning and telemetry for running Donkey Kong Bananza PC Recomp on budget discrete GPUs and gaming laptops with 4GB of VRAM (e.g., NVIDIA GeForce RTX 3050 Ti Laptop, GTX 1650/1050 Ti, 4GB desktop GPUs) paired with 6 to 8-core CPUs.

---

## The 4GB VRAM Bottleneck

On systems with 4GB of physical VRAM, Windows Desktop Window Manager (DWM) and background applications typically consume 400–800 MB, leaving only ~3.2 to 3.5 GB of usable video memory for the game.

### Benchmark & Telemetry Environment
* **Recomp Build:** Bananza 1.0.0 (build `0.3.0+a5796d9`)
* **CPU:** 11th Gen Intel Core i7-11800H @ 2.30 GHz (8 cores / 16 threads)
* **GPU:** NVIDIA GeForce RTX 3050 Ti Laptop GPU (4 GB GDDR6, 75W TGP)
* **Display Driver:** NVIDIA Game Ready Driver 572.70 / Vulkan 621.01 (WDDM 3.2)
* **OS:** Windows 11 Home 64-bit (Build 26100)
* **System RAM:** 16 GB DDR4-3200

### Observed Behavior (Default Configuration)
* **Initial baseline:** ~2.2 GB VRAM allocated, holding a stable 59.7 FPS.
* **VRAM creep over 10–12 minutes:** Without proactive texture eviction, resident textures accumulated past **3.48 GB**, hitting the engine's internal budget ceiling (`2929 of 3041 MiB in use`).
* **Symptom:** Emergency GPU pool releases caused severe 1.6-second freezes (`replay: 1648.2 ms`), progressively degrading gameplay framerate from 60 FPS down to 27 FPS over extended play sessions.

---

## Recommended `totk.ini` Configuration for 4GB VRAM

Add or uncomment the following settings in your configuration file (`totk.ini` / `dkb.ini`):

```ini
[frame rate]
fps_limit = 60
# Optional / Experimental: real-time frame step & cloth simulation (see Issue #201 for wall climbing above 60 fps).
# Keep commented for the safe baseline profile.
# realtime = 1
# cloth_realtime = 1
# highfps_fix = 1

[performance]
# Virtual core preemption: forces guest threads to yield when waiting, preventing thread starvation
async_preempt = 1

# Replay threading & GPU work submission pacing (eliminates 35-86ms replay queue freezes)
replay_async = 1
replay_threads = 6
split_draws = 16
split_us = 100
timer_1ms = 1

# Pipeline compilation hitch prevention
pipe_skip = fullscreen
lib_prebuild = 1
lib_prebuild_threads = 6
pipe_prebuild = 1
prewarm_xlate = 8

# Memory management & texture eviction for 4GB VRAM
astc = gpu
memory = auto
tex_alias = 1
tex_vram_pressure = 75
vk_subgroup_pin = 1

[display]
# Tear-free low-latency presentation on high-refresh (144Hz+) panels
vk_present = mailbox
window_mode = borderless
fullscreen = 0
assume_focus = 1
perf_overlay = fps
gpu_index = 0

# Native docked raster upscaled with FSR
output_res = 1920x1080
upscaler = fsr
sharpness = 40
frame_gen = off
```

---

## Key Tuning Insights

### 1. Stale Texture Eviction (`tex_vram_pressure = 75`)
* **What it does:** The default engine value in `runtime/cfg.c` is `90` (`0..100`, where `0` disables eviction). Lowering `tex_vram_pressure` to `75` instructs the texture cache to proactively release unreferenced textures whenever VRAM utilization exceeds 75% of the device-local budget.
* **Impact:** Caps active VRAM utilization around 2.3–2.6 GB, preventing GPU memory thrashing across the PCIe bus and eliminating the 1.6-second emergency pool garbage-collection hitches.

### 2. GPU Work Submission & Replay Worker Threads
* **`split_draws = 16`, `split_us = 100`:** Default is `64` draws / `1000` µs. These keys do not distribute work across CPU worker threads; rather, they submit GPU command batches in smaller chunks between passes so the GPU starts processing work earlier instead of waiting on a large monolithic command buffer.
* **`replay_threads = 6` (with `replay_async = 1`):** Assigns NVN submission replay to dedicated CPU worker threads (recommended: set to physical core count, e.g., 6 on 6-core / 8-core laptops), keeping submission off the critical guest thread.

### 3. Optional: Real-Time Frame Clock (`realtime = 1`, `cloth_realtime = 1`)
* **Status:** Marked optional / experimental on Bananza.
* **Caveat:** As tracked in [Issue #201](https://github.com/Lucas-blip-png/dkb-pc-recomp/issues/201), real-time clock steps can trigger animation anomalies and physics glitches during wall climbing at framerates above 60 FPS. Leave commented out unless specifically testing high-refresh pacing.

### 4. Presentation on 144Hz Displays (`vk_present = mailbox`)
* **What it does:** Uses Vulkan mailbox presentation (triple-buffered fast sync) instead of standard FIFO VSync.
* **Impact:** On 144Hz laptop screens, standard FIFO VSync causes micro-stutter when targeting 60 FPS due to non-integer refresh intervals (144 / 60 = 2.4). Mailbox delivers tear-free, perfectly paced 60 FPS frames.

### 5. Avoiding the Handheld 480p Downscale Bug
* **Finding:** Setting `output_res = 1280x720` forces Bananza into handheld rendering mode, which aggressively drops the internal 3D raster to **854x480 (standard definition)**. Applying AMD FSR with high sharpness to a 480p image results in extreme ringing, crushed contrast, and washed-out colors.
* **Fix:** Keep `output_res = 1920x1080`. The engine will render at its native 1600x900 docked size and scale cleanly to 1080p via FSR.

### 6. External Frame Generation Compatibility
* Setting `assume_focus = 1` and `window_mode = borderless` ensures external capture tools (such as Lossless Scaling or MFG Super Motion) can hook the borderless swapchain and double the stable 60 FPS base to **120 FPS** on 144Hz panels with zero focus-loss stutter.

---

## Hardware Caveats on Laptops
* **AC Power Required:** Running on battery power engages NVIDIA Battery Boost and hardware power caps (typically halving TGP from 75W to 35W and capping CPU power to 15W–25W), making locked 60 FPS impossible regardless of configuration.
* **Thermal Pacing:** Ensure the laptop is elevated or cooling profiles are set to *Performance* or *Turbo* to avoid the 86°C GPU thermal throttling threshold.
