# Reference machine

Performance numbers reported in this repo are measured on a documented reference
machine. This file will be populated once the reference desktop is set up.

## Hardware (placeholder)

- GPU: NVIDIA RTX 4060 Ti, 16GB VRAM
- CPU: TBD
- RAM: TBD
- OS: TBD
- NVIDIA driver / CUDA: TBD

## Model serving (placeholder)

- Server: LM Studio or Ollama (TBD; both will be documented if both work)
- Reference model: Qwen 2.5-Coder-32B as fallback / Qwen 3.6-35B-A3B as primary candidate
- Quantization: TBD
- Endpoint: `http://<reference-host>:1234/v1` (LM Studio default) or `http://<reference-host>:11434/v1` (Ollama)

## Why this matters

Per MBT-3 in the level-set doc: performance numbers are reported as both an
absolute number on this named reference machine and as a percentage of the
base model's latency. The percentage form is portable across hardware; the
absolute form lets readers calibrate whether the percentage is on a fast or
slow base.

This file gets filled in when the desktop is provisioned. Until then, demos
that report measured performance are deferred.
