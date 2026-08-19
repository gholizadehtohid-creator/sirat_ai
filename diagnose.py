"""Diagnose API connectivity.

    python diagnose.py

Checks every host the app depends on and says, per host, whether the failure
is something you can fix locally or needs a proxy.
"""

import settings
from providers import available_providers, get_provider
from providers.transport import probe

TARGETS = [
    ("TSETMC daily history",
     f"{settings.TSETMC_BASE_URL}/api/ClosingPrice/"
     f"GetClosingPriceDailyList/46348559193224090/5"),
    ("TSETMC server time",
     f"{settings.TSETMC_BASE_URL}/api/StaticData/GetTime"),
    ("TSETMC legacy",
     f"{settings.TSETMC_LEGACY_BASE_URL}/tsev2/data/TseClient2.aspx"),
    ("Instrument universe", settings.UNIVERSE_URL),
]


def main():

    print("=" * 78)
    print("SIRAT AI - CONNECTIVITY DIAGNOSIS")
    print("=" * 78)

    print("\nEffective settings:")
    for key, value in settings.describe().items():
        print(f"  {key:<24} {value}")

    print("\nEndpoint probes:")
    print(f"  {'WHAT':<24}{'HOST':<28}{'TCP ms':>8}  {'HTTP':<6} RESULT")
    print("  " + "-" * 90)

    notes = []
    reachable = 0

    for label, url in TARGETS:
        row = probe(url)
        status = row["error"] or "OK"
        if not row["error"]:
            reachable += 1
        print(
            f"  {label:<24}{row['host']:<28}"
            f"{str(row['tcp_ms'] if row['tcp_ms'] is not None else '-'):>8}  "
            f"{str(row['status'] or '-'):<6} {status}"
        )
        if row.get("note"):
            notes.append((row["host"], row["note"]))

    for host, note in notes:
        print(f"\n  {host}:\n    {note}")

    print("\nProvider health:")
    for row in available_providers():
        mark = "OK " if row["available"] else "DOWN"
        print(f"  [{mark}] {row['name']:<14} {', '.join(row['capabilities'])}")
        if row.get("error"):
            print(f"         {row['error']}")

    print(f"\nActive provider: {get_provider().name}")

    print("\n" + "=" * 78)
    if reachable == len(TARGETS):
        print("All endpoints reachable.")
    elif any(t[0] == "Instrument universe" for t in TARGETS) and reachable:
        print("Partial connectivity.")
        print()
        print("  The instrument universe is served from GitHub, so if that line")
        print("  says OK you can still load all ~818 listed shares with:")
        print("      run.bat setup")
        print()
        print("  EPS / P/E / market-cap fundamentals need tsetmc itself. To get")
        print("  those, set a proxy that reaches it and retry:")
        print("      set SIRAT_PROXY_URL=http://host:port")
        print("      python seed_import.py --snapshot")
    else:
        print("No endpoints reachable. Check the machine's internet connection.")
    print("=" * 78)


if __name__ == "__main__":
    main()
