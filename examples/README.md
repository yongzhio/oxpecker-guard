# OPG example gallery

Each subdirectory contains one demonstration of a deterministic guard or
composition. See the individual README files for what each demo shows.

## Shared Ollama configuration

All current demos target Ollama serving Qwen3 9B with an extended (64K)
context window. The `qwen3-9b-65k.Modelfile` in this directory creates
that tag once:

    ollama pull qwen3.5:9b
    ollama create qwen3.5:9b-65k -f examples/qwen3-9b-65k.Modelfile

After this one-time setup, all demos run against `qwen3.5:9b-65k`.

The extended context window avoids KV cache clearing failures observed
on some Ollama builds when running demos with substantial system prompts
or document inputs. KV cache for 64K tokens uses ~6 GiB of VRAM on top
of the ~5.6 GiB model weights; total ~12 GiB, fits a 16 GiB GPU.

See the individual demo READMEs for hardware fallback options (smaller
models with reduced context, etc.).
