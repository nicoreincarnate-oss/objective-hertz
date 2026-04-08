#!/usr/bin/env python3
"""show_cache_stats — CLI for inspecting semantic cache performance.

Examples:
    python -m scripts.show_cache_stats
    python -m scripts.show_cache_stats --by-operation
    python -m scripts.show_cache_stats --top-savings
"""

from __future__ import annotations

import argparse
import asyncio
import sys


async def main_async(args: argparse.Namespace) -> int:
    try:
        from shared.semantic_cache import SemanticCache, CACHEABLE_OPERATIONS
        from shared.config import config
        import redis.asyncio as aioredis
    except ImportError as exc:
        print(f"Cannot import dependencies: {exc}", file=sys.stderr)
        return 2

    redis_client = aioredis.from_url(
        f"redis://{getattr(config, 'redis_host', 'localhost')}:6379"
    )
    cache = SemanticCache(redis_client=redis_client)
    snapshot = await cache.stats_snapshot()

    print("\n=== Semantic Cache Statistics ===\n")
    print(f"  Hits:                     {snapshot['hits']}")
    print(f"  Misses:                   {snapshot['misses']}")
    print(f"  Bypasses (forbidden):     {snapshot['bypasses']}")
    print(f"  Hit rate:                 {snapshot['hit_rate']:.1%}")
    print(f"  Estimated cost saved:     ${snapshot['estimated_cost_saved_usd']:.2f}")
    print(f"  Bytes used:               {snapshot['bytes_used']:,}")

    if args.by_operation:
        print("\n=== Cacheable Operations ===\n")
        for op in sorted(CACHEABLE_OPERATIONS):
            print(f"  - {op}")

    await redis_client.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect semantic cache stats")
    parser.add_argument("--by-operation", action="store_true",
                        help="List cacheable operations")
    parser.add_argument("--top-savings", action="store_true",
                        help="Show top operations by cost saved")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
