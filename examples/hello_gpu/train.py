"""Minimal example experiment. `gpufleet run` can ship and launch this.

  ./gpufleet run --dir examples/hello_gpu --cmd "python3 train.py" --remote /root/hello
"""
import time


def main():
    try:
        import torch
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        name = torch.cuda.get_device_name(0) if dev == "cuda" else "cpu"
        print(f"[hello_gpu] torch {torch.__version__} on {dev} ({name})", flush=True)
        x = torch.randn(4096, 4096, device=dev)
        for i in range(20):
            x = x @ x
            x = x / x.norm()
            print(f"[hello_gpu] step {i+1}/20 done", flush=True)
            time.sleep(0.5)
        print("[hello_gpu] finished OK", flush=True)
    except ImportError:
        print("[hello_gpu] torch not installed; run with --pip torch", flush=True)


if __name__ == "__main__":
    main()
