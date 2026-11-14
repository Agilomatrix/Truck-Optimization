from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Packaging:
    pid: str
    ptype: str
    length: int          # mm
    width: int           # mm
    height: int          # mm
    net: float           # kg
    pack: float          # kg
    gross: float         # kg
    qty: int
    stackable: bool
    max_stack: int       # max boxes high (1 if not stackable)
    pallet_req: str      # "Yes" | "No" | "Optional"
    pallet_type: str
    pallet_length: int   # mm (0 if n/a)
    pallet_width: int
    pallet_height: int
    pallet_weight: float
    orient: str          # "Fixed" | "Upright" | "Any"
    has_pallet: bool     # complete pallet data available
