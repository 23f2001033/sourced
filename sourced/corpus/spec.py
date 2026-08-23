"""Value pools and MPN grammars for the synthetic corpus.

Kept separate from the generator so the pipeline never imports generation
logic, and so the label-producing constants are inspectable in one place.
"""
from __future__ import annotations

MANUFACTURERS = [
    # (canonical name, mpn style, series pool)
    ("TE Connectivity", "te", ["1546302", "1734035", "2013499", "0350945", "1892765"]),
    ("Molex", "molex", ["43045", "22035", "51021", "39289", "05054"]),
    ("JST", "jst", ["XH", "PH", "VH", "EH", "ZH"]),
    ("Phoenix Contact", "phoenix", ["MSTB", "MKDS", "PT", "FKC", "SPT"]),
    ("Amphenol", "amphenol", ["G881", "10114", "20021", "77313", "54602"]),
]

PITCHES = [1.25, 2.00, 2.54, 3.96, 5.08]
PITCH_INCH_DISPLAY = {2.54: "0.100", 3.96: "0.156", 5.08: "0.200"}

VOLTAGE_BY_PITCH = {1.25: [50, 125], 2.00: [125, 250], 2.54: [125, 250],
                    3.96: [250, 300, 600], 5.08: [300, 600]}
CURRENT_BY_PITCH = {1.25: 1.0, 2.00: 2.0, 2.54: 3.0, 3.96: 7.0, 5.08: 10.0}

POLE_COUNTS = [2, 3, 4, 5, 6, 8, 10, 12, 16, 20]
TEMP_MINS = [-55, -40, -25]
TEMP_MAXS = [85, 105, 125]
HOUSING_MATERIALS = ["nylon_66", "pbt", "lcp", "pa6t"]
FLAMMABILITY = ["ul94_v0", "ul94_v0", "ul94_v0", "ul94_v1", "ul94_hb"]
PLATINGS = ["gold", "tin", "tin_lead"]
GOLD_COMPATIBLE_MATERIALS = ["phosphor_bronze", "beryllium_copper"]
ALL_MATERIALS = ["phosphor_bronze", "beryllium_copper", "brass"]
MOUNTINGS = ["through_hole", "surface_mount", "panel_mount"]
ORIENTATIONS = ["vertical", "right_angle"]
TERMINATIONS = ["solder", "crimp", "idc"]
COLOURS = ["black", "grey", "white", "natural"]
CONTACT_RESISTANCES = [10.0, 20.0, 30.0]

# display forms — the source expresses values in whatever unit it likes
DISPLAY = {
    "nylon_66": "Nylon 66", "pbt": "PBT", "lcp": "LCP", "pa6t": "PA6T",
    "ul94_v0": "UL94 V-0", "ul94_v1": "UL94 V-1", "ul94_hb": "UL94 HB",
    "gold": "Gold", "tin": "Tin", "tin_lead": "Tin-Lead",
    "phosphor_bronze": "Phosphor Bronze", "beryllium_copper": "Beryllium Copper",
    "brass": "Brass", "stainless_steel": "Stainless Steel",
    "through_hole": "Through Hole", "surface_mount": "Surface Mount",
    "panel_mount": "Panel Mount", "cable_mount": "Cable Mount",
    "vertical": "Vertical", "right_angle": "Right Angle",
    "solder": "Solder", "crimp": "Crimp", "idc": "IDC", "screw": "Screw",
    "black": "Black", "grey": "Grey", "white": "White", "natural": "Natural",
}

ORIENT_ABBR = {"vertical": "VERT", "right_angle": "R/A"}
MOUNT_ABBR = {"through_hole": "TH", "surface_mount": "SMD", "panel_mount": "PNL"}
PLATING_ABBR = {"gold": "GOLD", "tin": "TIN", "tin_lead": "SN/PB"}


def make_mpn(style: str, series: str, poles: int, variant: int, orient: str,
             plating: str) -> str:
    """Manufacturer-specific MPN grammar. Each encodes pole count positionally,
    which is what makes MPN structure decoding learnable."""
    o = "V" if orient == "vertical" else "R"
    p = {"gold": "G", "tin": "T", "tin_lead": "S"}[plating]
    if style == "te":
        return f"{series}-{poles}{o}{p}"
    if style == "molex":
        return f"{series}-{poles:02d}{variant:02d}{o}"
    if style == "jst":
        return f"B{poles}B-{series}-A-{p}{o}"
    if style == "phoenix":
        return f"{series} 1,5/{poles}-{o}-{variant:02d}"
    return f"{series}-{poles:02d}{p}{o}"
