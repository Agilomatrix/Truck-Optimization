"""
Internal truck specification database.

EDIT THIS FILE to change truck types, dimensions, payloads, costs or global
settings. No change to the calculation logic is needed.

IMPORTANT: the values below are ILLUSTRATIVE SAMPLES ONLY. Verify real internal
dimensions, door openings, payloads and axle limits against the actual vehicles
(or the transporter's documents) before operational use.

Field notes
-----------
name              : Truck type label shown in the app.
length_mm / width_mm / height_mm : Internal cargo-space dimensions.
payload_kg        : Maximum payload (cargo + pallets).
cost_per_truck    : Transportation (+ loading) cost per truck trip in INR.
door_width_mm / door_height_mm : Door opening. Use None if unknown (check skipped).
clearance_mm      : TOTAL space deducted on each axis (loading clearance, wall
                    irregularities, wheel arches, securing gap, ...).
max_axle_load_kg  : Optional. Stored for reference; axle load distribution is NOT
                    calculated by the engine (it is reported as "not verified").
available         : Number of vehicles available. None = unlimited.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

# --------------------------------------------------------------------------- #
# Global engine settings
# --------------------------------------------------------------------------- #
SETTINGS = {
    # How many loaded pallets may be stacked on top of each other in a truck.
    # 1 = no pallet double-stacking (safe default). Only applies if the packaging
    # is marked Stackable = Yes.
    "max_pallet_stack_levels": 1,
    # Safety stop for runaway quantities.
    "max_trucks_per_option": 300,
    # Allowed difference (kg) between Gross and Net + Packaging weight.
    "gross_weight_tolerance_kg": 0.05,
}

# --------------------------------------------------------------------------- #
# Truck specifications (ILLUSTRATIVE)
# --------------------------------------------------------------------------- #
TRUCK_SPECS = [
    {
        "name": "Small",
        "length_mm": 3048, "width_mm": 1829, "height_mm": 1829,
        "payload_kg": 1500, "cost_per_truck": 5000,
        "door_width_mm": 1700, "door_height_mm": 1700,
        "clearance_mm": {"length": 50, "width": 50, "height": 50},
        "max_axle_load_kg": None, "available": None,
    },
    {
        "name": "Medium",
        "length_mm": 4267, "width_mm": 2134, "height_mm": 2134,
        "payload_kg": 4000, "cost_per_truck": 8000,
        "door_width_mm": 2000, "door_height_mm": 2000,
        "clearance_mm": {"length": 50, "width": 50, "height": 50},
        "max_axle_load_kg": None, "available": None,
    },
    {
        "name": "Large",
        "length_mm": 6096, "width_mm": 2438, "height_mm": 2438,
        "payload_kg": 9000, "cost_per_truck": 12000,
        "door_width_mm": 2300, "door_height_mm": 2300,
        "clearance_mm": {"length": 50, "width": 50, "height": 50},
        "max_axle_load_kg": None, "available": None,
    },
    {
        "name": "Extra Large",
        "length_mm": 9754, "width_mm": 2438, "height_mm": 2438,
        "payload_kg": 15000, "cost_per_truck": 18000,
        "door_width_mm": 2300, "door_height_mm": 2300,
        "clearance_mm": {"length": 50, "width": 50, "height": 50},
        "max_axle_load_kg": None, "available": None,
    },
]


@dataclass(frozen=True)
class Truck:
    name: str
    length_mm: int
    width_mm: int
    height_mm: int
    payload_kg: float
    cost: float
    door_width_mm: Optional[int]
    door_height_mm: Optional[int]
    clearance_length_mm: int
    clearance_width_mm: int
    clearance_height_mm: int
    max_axle_load_kg: Optional[float]
    available: Optional[int]

    @property
    def usable_length(self) -> int:
        return int(self.length_mm - self.clearance_length_mm)

    @property
    def usable_width(self) -> int:
        return int(self.width_mm - self.clearance_width_mm)

    @property
    def usable_height(self) -> int:
        return int(self.height_mm - self.clearance_height_mm)

    @property
    def internal_volume_m3(self) -> float:
        return self.length_mm * self.width_mm * self.height_mm / 1e9


def load_trucks() -> List[Truck]:
    """Validate TRUCK_SPECS and return Truck objects (raises ValueError if bad)."""
    trucks: List[Truck] = []
    seen = set()
    for spec in TRUCK_SPECS:
        name = str(spec.get("name", "")).strip()
        if not name or name in seen:
            raise ValueError(f"truck_config: missing or duplicate truck name: '{name}'")
        seen.add(name)
        for key in ("length_mm", "width_mm", "height_mm", "payload_kg", "cost_per_truck"):
            if not isinstance(spec.get(key), (int, float)) or spec[key] <= 0:
                raise ValueError(f"truck_config: '{name}' has invalid '{key}'")
        clr = spec.get("clearance_mm") or {}
        t = Truck(
            name=name,
            length_mm=int(spec["length_mm"]), width_mm=int(spec["width_mm"]),
            height_mm=int(spec["height_mm"]),
            payload_kg=float(spec["payload_kg"]), cost=float(spec["cost_per_truck"]),
            door_width_mm=spec.get("door_width_mm"), door_height_mm=spec.get("door_height_mm"),
            clearance_length_mm=int(clr.get("length", 0)),
            clearance_width_mm=int(clr.get("width", 0)),
            clearance_height_mm=int(clr.get("height", 0)),
            max_axle_load_kg=spec.get("max_axle_load_kg"),
            available=spec.get("available"),
        )
        if min(t.usable_length, t.usable_width, t.usable_height) <= 0:
            raise ValueError(f"truck_config: clearance leaves no usable space in '{name}'")
        trucks.append(t)
    if not trucks:
        raise ValueError("truck_config: no trucks defined")
    return trucks
