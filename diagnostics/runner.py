"""
Diagnostics Runner — Orchestrates all test suites and produces a report.

Usage:
    cd refined_engine
    python -m diagnostics.runner              # Run all suites
    python -m diagnostics.runner rest ticker   # Run specific suites
    python -m diagnostics.runner --list        # List available suites
"""

import sys
import time
from pathlib import Path

# Ensure refined_engine is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (
    API_KEY,
    API_SECRET,
    REST_BASE,
    WS_URL,
    NETWORK,
    IS_TESTNET,
    validate_credentials,
    print_config,
)
from diagnostics.report import print_banner, print_section, print_result, print_verdict

# ── Available Suites ─────────────────────────────────────────────────────────

SUITE_MAP = {
    "rest": ("REST API", "diagnostics.suites.test_rest"),
    "auth": ("Authentication", "diagnostics.suites.test_auth"),
    "account": ("Account", "diagnostics.suites.test_account"),
    "ticker": ("Ticker", "diagnostics.suites.test_ticker"),
    "candles": ("Candles", "diagnostics.suites.test_candles"),
    "orderbook": ("Orderbook", "diagnostics.suites.test_orderbook"),
    "ws": ("WebSocket", "diagnostics.suites.test_ws"),
}

# Default run order
DEFAULT_ORDER = ["rest", "ticker", "candles", "orderbook", "auth", "account", "ws"]


def build_config() -> dict:
    """Build the config dict passed to each suite."""
    return {
        "rest_base": REST_BASE,
        "ws_url": WS_URL,
        "api_key": API_KEY,
        "api_secret": API_SECRET,
        "network": NETWORK,
        "is_testnet": IS_TESTNET,
    }


def run_suite(suite_key: str, config: dict) -> list[dict]:
    """Dynamically import and run a test suite."""
    if suite_key not in SUITE_MAP:
        return [{"name": f"Unknown suite: {suite_key}", "passed": False, "detail": "Not found"}]

    label, module_path = SUITE_MAP[suite_key]
    print_section(label)

    try:
        import importlib
        module = importlib.import_module(module_path)
        results = module.run(config)

        for r in results:
            print_result(r)

        return results

    except Exception as e:
        result = {"name": f"{label}: Import/Run Error", "passed": False, "detail": str(e)}
        print_result(result)
        return [result]


def main():
    args = sys.argv[1:]

    # --list flag
    if "--list" in args:
        print("\nAvailable diagnostic suites:")
        for key, (label, _) in SUITE_MAP.items():
            print(f"  {key:<12} {label}")
        print()
        return

    # Banner + config
    print_banner()
    has_creds = validate_credentials()
    print_config()

    # Determine which suites to run
    if args:
        suites_to_run = [s for s in args if s in SUITE_MAP]
        unknown = [s for s in args if s not in SUITE_MAP]
        if unknown:
            print(f"  ⚠ Unknown suites: {', '.join(unknown)}")
    else:
        suites_to_run = DEFAULT_ORDER

    # Skip auth-required suites if no credentials
    if not has_creds:
        auth_suites = {"auth", "account"}
        skipped = [s for s in suites_to_run if s in auth_suites]
        if skipped:
            print(f"  ⚠ Skipping auth-required suites (no credentials): {', '.join(skipped)}")
        suites_to_run = [s for s in suites_to_run if s not in auth_suites]

    # Run
    config = build_config()
    all_results = []
    start = time.time()

    for suite_key in suites_to_run:
        results = run_suite(suite_key, config)
        all_results.extend(results)

    elapsed = time.time() - start

    # Verdict
    all_passed = print_verdict(all_results, elapsed)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
