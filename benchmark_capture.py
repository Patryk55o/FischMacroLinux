#!/usr/bin/env python3
"""
Standalone diagnostic: measures how long a single screenshot capture of a
small region takes on this system, independent of the game or the macro's
detection logic. Use this to find out whether screen-capture latency is
the actual bottleneck when --pulse-ms doesn't seem to speed anything up.

Usage:
    python3 benchmark_capture.py
    python3 benchmark_capture.py --width 1000 --height 40 --iterations 200

If the average is well under your --pulse-ms value (e.g. under 20ms for a
100ms pulse), capture isn't the bottleneck and something else is
dominating the loop. If it's comparable to or bigger than --pulse-ms
(e.g. 80-150ms+), that's your answer: the screenshot call itself is what's
limiting cycle speed, most likely because the X11 MIT-SHM (shared memory)
extension isn't available/being used, which makes screenshot capture much
slower on some setups (common over SSH X11 forwarding, some VMs/containers,
or certain display drivers).
"""

import argparse
import statistics
import sys
import time

try:
    import mss
    import numpy as np
except ImportError:
    sys.exit("Missing dependency. Install with: pip install mss numpy")


def main():
    parser = argparse.ArgumentParser(description="Benchmark screenshot capture latency")
    parser.add_argument("--width", type=int, default=1000, help="Capture region width (default: 1000, roughly matching the minigame bar region)")
    parser.add_argument("--height", type=int, default=40, help="Capture region height (default: 40)")
    parser.add_argument("--iterations", type=int, default=100, help="Number of captures to time (default: 100)")
    parser.add_argument("--left", type=int, default=100, help="Region left offset (default: 100)")
    parser.add_argument("--top", type=int, default=100, help="Region top offset (default: 100)")
    args = parser.parse_args()

    region = {"left": args.left, "top": args.top, "width": args.width, "height": args.height}

    with mss.mss() as sct:
        # Warm-up (first grab is often slower due to backend setup)
        sct.grab(region)

        durations_ms = []
        for _ in range(args.iterations):
            t0 = time.perf_counter()
            raw = sct.grab(region)
            arr = np.array(raw)
            _ = arr[:, :, [2, 1, 0]].astype(np.int16)  # mirror what the macro actually does
            durations_ms.append((time.perf_counter() - t0) * 1000)

    print(f"Region: {args.width}x{args.height} px, {args.iterations} captures")
    print(f"  min:    {min(durations_ms):.1f} ms")
    print(f"  max:    {max(durations_ms):.1f} ms")
    print(f"  mean:   {statistics.mean(durations_ms):.1f} ms")
    print(f"  median: {statistics.median(durations_ms):.1f} ms")
    print()
    mean = statistics.mean(durations_ms)
    if mean < 20:
        print("Capture is fast. If --pulse-ms still has no visible effect in-game,")
        print("the bottleneck is elsewhere (game-side input latency, rendering, etc.)")
    elif mean < 60:
        print("Capture is moderate. It'll eat into low --pulse-ms values")
        print("(e.g. requesting 20-30ms pulses won't really be achievable).")
    else:
        print("Capture is SLOW relative to typical pulse targets like 50-100ms.")
        print("This is very likely why lowering --pulse-ms has no visible effect -")
        print("the screenshot call itself dominates the loop. Common cause: the")
        print("X11 MIT-SHM (shared memory) extension isn't available/in use.")
        print("Check with: xdpyinfo -queryExtensions | grep -i shm")


if __name__ == "__main__":
    main()
