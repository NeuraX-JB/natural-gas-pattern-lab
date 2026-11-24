"""Use the host timezone database or the bundled New York TZif on Windows."""
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    NEW_YORK = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:
    with (Path(__file__).parent / "tz/New_York.tzif").open("rb") as f:
        NEW_YORK = ZoneInfo.from_file(f, key="America/New_York")
