# System requirements

What it takes to run Donkey Kong Bananza through DKB Recomp, from runs we measured. The heaviest scene we test is the -200 recording (update 3.2.0, a player's save, a fixed input recording, the shader cache warm). Most of the game runs lighter than that.

The processor decides the frame rate far more than the GPU. The game's own threads and our command replay both run on the CPU. A GPU with 8 GB stays at 60 fps at every preset in that scene.

## Summary

| | Minimum | Recommended |
|---|---|---|
| OS | Windows 10/11 x64, or Linux x64 (preview) | Windows 10/11 x64 |
| CPU | 4 cores / 8 threads with AVX2 (x86-64-v3) | 8 cores / 16 threads |
| RAM | 16 GB plus a large page file | 32 GB |
| GPU | Vulkan 1.3, 4 GB VRAM ([4 GB guide](4gb-vram-optimization.md)) | Vulkan 1.3, 8 GB VRAM; NVIDIA RTX for DLSS |
| Disk | About 15 GB on an SSD for an installed copy (one install measured 14 GB), plus room for the dump while setup runs | NVMe SSD |
| Expected | 30 to 45 fps in heavy scenes at the Minimum or Low preset | 60 fps at the Medium or High preset |

A CPU without AVX2 runs with the `noavx2` build, slower. AMD GPUs work on the proprietary driver and on RADV. Some device-lost crashes on AMD are still open; see the issues labelled `platform: amd`.

## Measured runs

### Processor

These runs used an RTX 5060 Ti (8 GB) and a Ryzen 7 5700X, limited to N logical processors with the CPU affinity mask. They used the -200 recording at a preset, and the time is the second minute, after the area has loaded.

| Logical processors | Preset | Mean fps | 1% low | Frames over 25 ms |
|---|---|---|---|---|
| 16 | 1 to 4 | 59.1 to 60.0 | 42 to 49 | 0 to 27 |
| 16 | 5 | 57.6 | 29.9 | 91 |
| 6 | 2 | 48.4 | 28.2 | 655 |
| 6 | 3 | 46.6 | 27.2 | 662 |
| 4 | 1 | 40.4 | 20.1 | 1104 |

A lower preset barely helps a CPU-bound machine. With 6 logical processors, preset 2 and preset 3 are within 2 fps of each other. The first minute, which loads the area, is about 2 to 7 fps lower in every row.

### Graphics card

These runs used the same RTX 5060 Ti locked at 900 MHz, about a third of its boost clock, standing in for a weaker card. They used 16 logical processors. The figures come from [dkb-lowend.md](dkb-lowend.md), "Weak GPU".

| Output | `scene_res` | Mean fps (minute 1 / 2) | Game GPU time per frame |
|---|---|---|---|
| 1920x1080 | auto | 55.8 / 58.7 | 9.3 to 9.7 ms |
| 1920x1080 | full | 54.3 / 56.5 | 11.6 to 11.8 ms |
| 2560x1440 | auto | 52.6 / 56.8 | 11.7 to 12.0 ms |
| 2560x1440 | full | about 50.8 / 50.2 | 14.8 to 15.3 ms |

### Players' machines

Players' reports in the issues also show:
- An RTX 3050 Ti Laptop (4 GB) with an i7-11800H holds a steady 59.7 fps on 1.0.0 ([4 GB guide](4gb-vram-optimization.md)).
- An RX 7900 XTX goes from 41 to 54.5 fps at 4K once the replay threads are on (#141).
- An RX 7800 XT holds 60 fps on floor 200 with 8 replay threads (#418).

Reports from other machines are welcome. Please open an issue with the CPU, GPU, RAM, preset or settings, scene and fps.

## Presets

`preset = recommended` in `dkb.ini`, or Preset in the F1 menu, picks a level from the video memory, RAM and logical processors at start. The levels are listed in [dkb-testing.md](dkb-testing.md) under `preset`. A preset only fills in defaults, so any option you set yourself wins. On the machine above (8 GB VRAM, 16 GB RAM, 16 logical processors), `recommended` picks level 3.
