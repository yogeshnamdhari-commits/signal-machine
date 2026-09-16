"""
Pipeline Trace Analysis — Parse engine logs to extract real production traces.

Usage:
    python3 _trace_analysis.py
    
    # Analyze the last 1000 lines of engine logs
    python3 _trace_analysis.py --lines 1000
    
    # Analyze a specific symbol
    python3 _trace_analysis.py --symbol ETHUSDT
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

LOG_FILE = Path("packages/ai-engine/data/logs/engine.log")

# Pattern for our trace logs
TRACE_PATTERNS = {
    "scan": re.compile(r"SCAN_TRACE sym=(\S+) state=(\S+) regime=(\S+)"),
    "pullback_trace": re.compile(r"WAITING_PULLBACK_TRACE sym=(\S+) pullback=(\S+)"),
    "pullback_detected": re.compile(r"PULLBACK_TRACE sym=(\S+) PULLBACK_DETECTED"),
    "candle_trace": re.compile(r"CANDLE_TRACE sym=(\S+) pattern_found=(\S+)"),
    "transition": re.compile(r"TRANSITION_TRACE sym=(\S+) → (\S+)"),
    "volume_trace": re.compile(r"VOLUME_TRACE sym=(\S+)"),
    "confidence_trace": re.compile(r"CONFIDENCE_TRACE sym=(\S+)"),
    "timeout": re.compile(r"TIMEOUT sym=(\S+) state=(\S+) duration=(\S+)s"),
}


def parse_logs(log_file: Path, n_lines: int = 1000) -> dict:
    """Parse the last N lines of engine logs for trace events."""
    if not log_file.exists():
        print(f"Log file not found: {log_file}")
        return {}
    
    # Read last N lines
    with open(log_file, "r") as f:
        lines = f.readlines()[-n_lines:]
    
    # Parse trace events
    events = defaultdict(list)
    for line in lines:
        for name, pattern in TRACE_PATTERNS.items():
            match = pattern.search(line)
            if match:
                events[name].append(match.groups())
                break
    
    return events


def analyze_symbol(events: dict, symbol: str) -> None:
    """Analyze pipeline trace for a specific symbol."""
    print(f"\n{'='*60}")
    print(f"  PIPELINE TRACE: {symbol}")
    print(f"{'='*60}")
    
    # Filter events for this symbol
    symbol_events = []
    for name, matches in events.items():
        for match in matches:
            if match[0] == symbol:
                symbol_events.append((name, match))
    
    if not symbol_events:
        print(f"  No trace events found for {symbol}")
        return
    
    # Print events in order
    for name, match in symbol_events:
        if name == "scan":
            print(f"  {name}: state={match[1]} regime={match[2]}")
        elif name == "pullback_trace":
            print(f"  {name}: pullback={match[1]}")
        elif name == "pullback_detected":
            print(f"  {name}: PULLBACK DETECTED")
        elif name == "candle_trace":
            print(f"  {name}: pattern_found={match[1]}")
        elif name == "transition":
            print(f"  {name}: → {match[1]}")
        elif name == "timeout":
            print(f"  {name}: duration={match[1]}s")
        else:
            print(f"  {name}: {match}")


def analyze_all(events: dict) -> None:
    """Analyze pipeline traces for all symbols."""
    print(f"\n{'='*60}")
    print(f"  PIPELINE TRACE SUMMARY")
    print(f"{'='*60}")
    
    # Count events by type
    for name, matches in sorted(events.items()):
        print(f"  {name}: {len(matches)} events")
    
    # Find symbols with the most events
    symbol_counts = defaultdict(int)
    for name, matches in events.items():
        for match in matches:
            symbol_counts[match[0]] += 1
    
    print(f"\n  Top symbols by trace events:")
    for sym, count in sorted(symbol_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"    {sym}: {count} events")


def main():
    n_lines = 1000
    symbol = None
    
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--lines" and i + 1 < len(sys.argv) - 1:
            n_lines = int(sys.argv[i + 2])
        elif arg == "--symbol" and i + 1 < len(sys.argv) - 1:
            symbol = sys.argv[i + 2]
    
    print(f"Analyzing last {n_lines} lines of {LOG_FILE}")
    
    events = parse_logs(LOG_FILE, n_lines)
    
    if not events:
        print("No trace events found. Make sure the scanner is running with trace logging enabled.")
        return
    
    if symbol:
        analyze_symbol(events, symbol)
    else:
        analyze_all(events)
        # Also show a sample symbol trace
        for name, matches in events.items():
            if matches:
                sample_sym = matches[0][0]
                analyze_symbol(events, sample_sym)
                break


if __name__ == "__main__":
    main()
