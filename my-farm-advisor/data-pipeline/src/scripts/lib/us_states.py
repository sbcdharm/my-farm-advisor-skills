"""US state FIPS <-> postal-abbreviation helpers.

Single source of truth for state identity used by boundary ingestion, CDL
routing, and farm metadata. Before this module existed the CDL ingest fell back
to Iowa ("19") whenever a boundary carried only a postal abbreviation, which
silently routed other states to the wrong CDL raster.
"""

from __future__ import annotations

STATE_FIPS: dict[str, tuple[str, str]] = {
    "01": ("AL", "Alabama"),
    "02": ("AK", "Alaska"),
    "04": ("AZ", "Arizona"),
    "05": ("AR", "Arkansas"),
    "06": ("CA", "California"),
    "08": ("CO", "Colorado"),
    "09": ("CT", "Connecticut"),
    "10": ("DE", "Delaware"),
    "11": ("DC", "District of Columbia"),
    "12": ("FL", "Florida"),
    "13": ("GA", "Georgia"),
    "15": ("HI", "Hawaii"),
    "16": ("ID", "Idaho"),
    "17": ("IL", "Illinois"),
    "18": ("IN", "Indiana"),
    "19": ("IA", "Iowa"),
    "20": ("KS", "Kansas"),
    "21": ("KY", "Kentucky"),
    "22": ("LA", "Louisiana"),
    "23": ("ME", "Maine"),
    "24": ("MD", "Maryland"),
    "25": ("MA", "Massachusetts"),
    "26": ("MI", "Michigan"),
    "27": ("MN", "Minnesota"),
    "28": ("MS", "Mississippi"),
    "29": ("MO", "Missouri"),
    "30": ("MT", "Montana"),
    "31": ("NE", "Nebraska"),
    "32": ("NV", "Nevada"),
    "33": ("NH", "New Hampshire"),
    "34": ("NJ", "New Jersey"),
    "35": ("NM", "New Mexico"),
    "36": ("NY", "New York"),
    "37": ("NC", "North Carolina"),
    "38": ("ND", "North Dakota"),
    "39": ("OH", "Ohio"),
    "40": ("OK", "Oklahoma"),
    "41": ("OR", "Oregon"),
    "42": ("PA", "Pennsylvania"),
    "44": ("RI", "Rhode Island"),
    "45": ("SC", "South Carolina"),
    "46": ("SD", "South Dakota"),
    "47": ("TN", "Tennessee"),
    "48": ("TX", "Texas"),
    "49": ("UT", "Utah"),
    "50": ("VT", "Vermont"),
    "51": ("VA", "Virginia"),
    "53": ("WA", "Washington"),
    "54": ("WV", "West Virginia"),
    "55": ("WI", "Wisconsin"),
    "56": ("WY", "Wyoming"),
}

ABBR_TO_FIPS: dict[str, str] = {
    abbr: fips for fips, (abbr, _name) in STATE_FIPS.items()
}


def normalize_state_fips(value: object) -> str | None:
    """Resolve a state selector to a zero-padded two-digit FIPS code.

    Accepts a FIPS code (``"17"``, ``17``), a postal abbreviation with any
    casing (``"il"``), or a full state name (``"Illinois"``). Returns ``None``
    when the value is empty or not a recognized US state.
    """
    if value is None:
        return None
    key = str(value).strip()
    if not key:
        return None
    if key.isdigit():
        fips = key.zfill(2)
        return fips if fips in STATE_FIPS else None
    abbr = key.upper()
    if abbr in ABBR_TO_FIPS:
        return ABBR_TO_FIPS[abbr]
    lower = key.lower()
    for fips, (_abbr, name) in STATE_FIPS.items():
        if name.lower() == lower:
            return fips
    return None


def state_abbr(value: object) -> str | None:
    """Resolve a state selector to its two-letter postal abbreviation."""
    fips = normalize_state_fips(value)
    return STATE_FIPS[fips][0] if fips else None
