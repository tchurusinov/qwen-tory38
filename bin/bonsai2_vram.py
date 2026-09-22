#!/usr/bin/env python3
"""bonsai2_vram.py — MAP 1.6 / S1.4: the VRAM accounting formula as pure
functions (the S1.7 "unit half": "the VRAM formula as a pure function
(given weights/kv constants -> N, including N=0 and N>8 clamp cases)").

S1.4 (locked; re-anchored 2026-09-21 at MAP 1.7 from the measured
q4_0-KV footprint on this box):
    instance_footprint_GB = weights_GB + kv_GB
    kv_GB = slots * ctx_size * per_slot_per_token_GB
    per_slot_per_token_GB = 0.000003277
      # measured q4_0 KV on this box (MAP 1.7 @gpu): ~6.87 GB KV at
      # 8 slots x 262144 ctx. S1.3 pins --cache-type-k/v q4_0; the
      # earlier 0.00000176 (3.7 GB KV) was the FP16/community-bench
      # anchor and under-predicted the full-ctx footprint.
    Sanity: 7.21 + 6.87 = 14.08 GB formula footprint ~ 14.44 GB
    (13.45 GiB) MEASURED total incl. ~0.36 GB CUDA ctx/runtime
    overhead (MAP 1.7 @gpu, this box).
    Per-instance budget in install: 14.78 GB (14.08 + 5% margin).
    N = floor(budget_for_bonsai / 14.78), clamped to [0, 8].
    S0 worked example: floor(37.2 / 14.78) = 2 (the re-anchor dropped
    the default from 3; the original 11.5 GB budget assumed the
    3.7 GB FP16-era KV anchor).

The three functions here are the spec's formula, one-to-one:

  * `instance_footprint_gb(weights_gb, slots, ctx_size, ...)` -> GB
  * `instance_count(budget_gb, footprint_gb)` -> N in [0, 8]
  * `subagent_budget_gb(total_gb, main_stack_gb, os_reserved_gb)` -> GB
    (the S0/S2.3 planning-tier input: total − main − reserved)

All pure, stdlib-only, total on their declared domains (float in,
float out; `instance_count` is total on all floats: no ZeroDivision,
negative budgets clamp to 0, over-budget clamps to 8).

Usage:
  python3 bin/bonsai2_vram.py footprint --weights-gb 7.21 --slots 8 --ctx 262144
  python3 bin/bonsai2_vram.py count --budget-gb 37.2 --footprint-gb 14.78
  python3 bin/bonsai2_vram.py budget --total-gb 121.6 --main-gb 76.4
"""

from __future__ import annotations

import argparse
import sys

EXIT_OK = 0
EXIT_CONFIG = 2

# S1.4 constants (the spec is the source; asserted against the spec's
# worked examples in tests/test_vram.py).
PER_SLOT_PER_TOKEN_GB = (
    0.000003277  # S1.4 re-anchor (MAP 1.7): measured q4_0 KV on this box
)
N_MIN = 0  # S1.4 clamp
N_MAX = 8  # S1.4 clamp: 8 instances = 64 slots total, the demo ceiling
DEFAULT_BUDGET_GB = (
    14.78  # S1.4 re-anchor: 14.08 GB measured q4_0 footprint + 5% margin
)
DEFAULT_OS_RESERVED_GB = 8.0  # S0: the reserved pool in the S0 table
DEFAULT_TOTAL_GB = 121.6  # S0: this box's /proc/meminfo pool (GiB)
DEFAULT_MAIN_STACK_GB = 76.4  # S0: the main stack's fresh-load footprint
DEFAULT_WEIGHTS_GB = 7.21  # S1.1: the default quant (PQ2_0)
DEFAULT_FALLBACK_WEIGHTS_GB = 5.95  # S1.1: the fallback quant (PTQ1_0)
DEFAULT_SLOTS = 8  # S1.3: --parallel 8
DEFAULT_CTX = 262144  # S1.3: --ctx-size 262144


def instance_footprint_gb(
    weights_gb: float,
    slots: int,
    ctx_size: int,
    per_slot_per_token_gb: float = PER_SLOT_PER_TOKEN_GB,
) -> float:
    """S1.4: `instance_footprint_GB = weights_GB + kv_GB`.

    `kv_GB = slots * ctx_size * per_slot_per_token_GB`. The constant is
    the measured q4_0-KV value on this box (MAP 1.7): ~6.87 GB KV at
    8 slots x 262144 ctx -> 8 x 262144 x 0.000003277 = 6.87 GB (S1.4
    re-anchor; S1.3 pins --cache-type-k/v q4_0).
    """
    if slots < 0 or ctx_size < 0:
        raise ValueError(
            f"ERR_CONFIG: slots/ctx_size must be >= 0: slots={slots}, ctx_size={ctx_size}"
        )
    if per_slot_per_token_gb < 0:
        raise ValueError(
            f"ERR_CONFIG: per_slot_per_token_gb must be >= 0: {per_slot_per_token_gb!r}"
        )
    kv_gb = slots * ctx_size * per_slot_per_token_gb
    return weights_gb + kv_gb


def instance_count(budget_gb: float, footprint_gb: float, n_max: int = N_MAX) -> int:
    """S1.4: `N = floor(budget / per_instance_budget_gb)`, clamped to [0, 8].

    Total on all floats: `budget_gb <= 0` or `footprint_gb <= 0` -> 0
    (no budget, or a non-positive footprint can never be allocated — a
    ZeroDivisionError would be the spec's "N=0 case" misfiring as an
    exception). `budget_gb >= n_max * footprint_gb` -> n_max (the N>8
    clamp case). `footprint_gb` is the per-instance BUDGET (S1.4's
    14.78 GB = footprint + 5% margin), not the raw footprint — the spec
    divides the pool by the budget.
    """
    if budget_gb <= 0 or footprint_gb <= 0:
        return N_MIN
    n = int(budget_gb // footprint_gb)  # floor for non-negative floats
    if n < N_MIN:
        return N_MIN
    if n > n_max:
        return n_max
    return n


def subagent_budget_gb(
    total_gb: float,
    main_stack_gb: float,
    os_reserved_gb: float = DEFAULT_OS_RESERVED_GB,
) -> float:
    """S0/S2.3 planning-tier input: total − main_stack − os_reserved.

    The S0 worked example: 121.6 − 76.4 − 8.0 = 37.2 GB (the pool the
    S1.4 `instance_count` then divides by the 14.78 GB per-instance
    budget). Negative results are allowed (the S2.3 `max(0, ...)` clamp
    is `instance_count`'s job — the budget itself may be negative when
    the main stack + reserved already consume the pool; S2.3's
    `max(0, min(N, floor(...)))` turns that into main-only).
    """
    return total_gb - main_stack_gb - os_reserved_gb


def _fmt(x: float) -> str:
    return f"{x:.4f}".rstrip("0").rstrip(".") if x != int(x) else str(int(x))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="S1.4 VRAM formula (MAP 1.6).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_f = sub.add_parser("footprint", help="instance_footprint_GB (S1.4)")
    p_f.add_argument(
        "--weights-gb",
        type=float,
        default=DEFAULT_WEIGHTS_GB,
        help=f"weights GB (default {DEFAULT_WEIGHTS_GB} = PQ2_0, S1.1)",
    )
    p_f.add_argument(
        "--slots",
        type=int,
        default=DEFAULT_SLOTS,
        help=f"slots = max_num_seqs (default {DEFAULT_SLOTS}, S1.3)",
    )
    p_f.add_argument(
        "--ctx",
        type=int,
        default=DEFAULT_CTX,
        help=f"ctx_size (default {DEFAULT_CTX}, S1.3)",
    )
    p_f.add_argument(
        "--per-slot-per-token-gb",
        type=float,
        default=PER_SLOT_PER_TOKEN_GB,
        help=f"S1.4 constant (default {PER_SLOT_PER_TOKEN_GB})",
    )

    p_c = sub.add_parser(
        "count", help="N = floor(budget/footprint) clamped [0,8] (S1.4)"
    )
    p_c.add_argument(
        "--budget-gb",
        type=float,
        default=None,
        help="the subagent pool budget GB (default: computed "
        "from the S0 table: 121.6 - 76.4 - 8.0 = 37.2)",
    )
    p_c.add_argument(
        "--footprint-gb",
        type=float,
        default=DEFAULT_BUDGET_GB,
        help=f"per-instance BUDGET GB (default {DEFAULT_BUDGET_GB}, S1.4)",
    )

    p_b = sub.add_parser(
        "budget", help="subagent_budget_gb = total - main - reserved (S0)"
    )
    p_b.add_argument(
        "--total-gb",
        type=float,
        default=DEFAULT_TOTAL_GB,
        help=f"pool total GB (default {DEFAULT_TOTAL_GB}, S0)",
    )
    p_b.add_argument(
        "--main-gb",
        type=float,
        default=DEFAULT_MAIN_STACK_GB,
        help=f"main stack fresh-load footprint GB (default {DEFAULT_MAIN_STACK_GB}, S0)",
    )
    p_b.add_argument(
        "--reserved-gb",
        type=float,
        default=DEFAULT_OS_RESERVED_GB,
        help=f"OS reservation GB (default {DEFAULT_OS_RESERVED_GB}, S0)",
    )

    args = ap.parse_args(argv)
    try:
        if args.cmd == "footprint":
            print(
                _fmt(
                    instance_footprint_gb(
                        args.weights_gb,
                        args.slots,
                        args.ctx,
                        args.per_slot_per_token_gb,
                    )
                )
            )
        elif args.cmd == "count":
            budget = args.budget_gb
            if budget is None:
                budget = subagent_budget_gb(
                    DEFAULT_TOTAL_GB, DEFAULT_MAIN_STACK_GB, DEFAULT_OS_RESERVED_GB
                )
            print(instance_count(budget, args.footprint_gb))
        elif args.cmd == "budget":
            print(
                _fmt(subagent_budget_gb(args.total_gb, args.main_gb, args.reserved_gb))
            )
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_CONFIG
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
