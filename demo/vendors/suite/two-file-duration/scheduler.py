"""When each job next runs."""
from duration import parse_duration


def next_runs(start: int, jobs: dict) -> dict:
    """{job: start + its interval in seconds}; intervals are duration strings."""
    return {name: start + parse_duration(every) for name, every in jobs.items()}


def runs_per_day(every: str) -> int:
    seconds = parse_duration(every)
    return 86400 // seconds if seconds else 0
