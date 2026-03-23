"""Constants for pyopnsense."""

from typing import Any

from dateutil.tz import gettz

VERSION = "v1.0.2"

# Default timeout, in seconds, for API requests.
DEFAULT_REQUEST_TIMEOUT_SECONDS = 60
# Shared cache time-to-live, in seconds, for endpoint availability state.
DEFAULT_CACHE_TTL_SECONDS = 6 * 60 * 60

# Mapping of ambiguous timezone abbreviations to explicit IANA timezones.
AMBIGUOUS_TZINFOS: dict[str, Any] = {
    "ACST": gettz("Australia/Darwin"),  # Australian Central Standard Time
    "ACT": gettz("America/Rio_Branco"),  # Acre Time (Brazil)
    "ADT": gettz("America/Halifax"),  # Atlantic Daylight Time (Caribbean/Canada)
    "AEST": gettz("Australia/Sydney"),  # Australian Eastern Standard Time
    "AEDT": gettz("Australia/Sydney"),  # Australian Eastern Daylight Time
    "AST": gettz("America/Halifax"),  # Atlantic Standard Time (Caribbean/Canada)
    "AWST": gettz("Australia/Perth"),  # Australian Western Standard Time
    "BST": gettz("Europe/London"),  # British Summer Time
    "CET": gettz("Europe/Paris"),  # Central European Time
    "CEST": gettz("Europe/Paris"),  # Central European Summer Time
    "CDT": gettz("America/Chicago"),  # Central Daylight Time (North America)
    "CST": gettz("America/Chicago"),  # Central Standard Time (North America)
    "EET": gettz("Europe/Bucharest"),  # Eastern European Time
    "EEST": gettz("Europe/Bucharest"),  # Eastern European Summer Time
    "EDT": gettz("America/New_York"),  # Eastern Daylight Time (North America)
    "EST": gettz("America/New_York"),  # Eastern Standard Time (North America)
    "HST": gettz("Pacific/Honolulu"),  # Hawaii-Aleutian Standard Time
    "IST": gettz("Asia/Kolkata"),  # Indian Standard Time
    "MDT": gettz("America/Denver"),  # Mountain Daylight Time (North America)
    "MST": gettz("America/Denver"),  # Mountain Standard Time (North America)
    "NZDT": gettz("Pacific/Auckland"),  # New Zealand Daylight Time
    "NZST": gettz("Pacific/Auckland"),  # New Zealand Standard Time
    "PDT": gettz("America/Los_Angeles"),  # Pacific Daylight Time (North America)
    "PST": gettz("America/Los_Angeles"),  # Pacific Standard Time (North America)
}
