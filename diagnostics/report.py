"""
Diagnostics Report — Formats and displays test results.
"""

import time
from datetime import datetime


def print_banner():
    """Print the diagnostics banner."""
    print()
    print("  ╔═══════════════════════════════════════════════╗")
    print("  ║     R E F I N E D   E N G I N E               ║")
    print("  ║        Diagnostics Runner                      ║")
    print("  ╚═══════════════════════════════════════════════╝")
    print()


def print_section(title: str):
    """Print a section divider."""
    padding = max(0, 48 - len(title))
    print(f"\n  ── {title} {'─' * padding}")


def print_result(result: dict):
    """Print a single test result."""
    icon = "✅" if result["passed"] else "❌"
    name = result["name"]
    detail = result.get("detail", "")

    if detail:
        print(f"    {icon} {name}")
        print(f"        → {detail}")
    else:
        print(f"    {icon} {name}")


def print_verdict(all_results: list[dict], elapsed: float):
    """Print final verdict summary."""
    total = len(all_results)
    passed = sum(1 for r in all_results if r["passed"])
    failed = total - passed

    print()
    print("  ══ Verdict ════════════════════════════════════════")
    print()

    # Group by suite prefix
    suites: dict[str, list[dict]] = {}
    for r in all_results:
        prefix = r["name"].split(":")[0].strip()
        suites.setdefault(prefix, []).append(r)

    for suite_name, results in suites.items():
        suite_passed = sum(1 for r in results if r["passed"])
        suite_total = len(results)
        icon = "✅" if suite_passed == suite_total else "❌"
        print(f"    {icon} {suite_name}: {suite_passed}/{suite_total}")

    print()
    print(f"    Total: {passed}/{total} passed ({failed} failed)")
    print(f"    Time:  {elapsed:.1f}s")
    print(f"    Run:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    if failed == 0:
        print("  🟢 ALL DIAGNOSTICS PASSED")
    elif failed <= 2:
        print("  🟡 PARTIAL — Minor issues detected")
    else:
        print("  🔴 DIAGNOSTICS FAILED — Review errors above")

    print()
    return failed == 0


def format_json_report(all_results: list[dict], elapsed: float) -> dict:
    """Return results as a structured dict (for programmatic use)."""
    total = len(all_results)
    passed = sum(1 for r in all_results if r["passed"])

    return {
        "timestamp": datetime.now().isoformat(),
        "elapsed_seconds": round(elapsed, 2),
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "all_passed": passed == total,
        "results": all_results,
    }
