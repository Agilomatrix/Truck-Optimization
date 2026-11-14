"""
Truck loading calculation engine.

Method (heuristic – a FEASIBLE loading recommendation, not a guaranteed global optimum)
------------------------------------------------------------------------------------
1. Pallet layers: a recursive guillotine / block heuristic packs boxes on the pallet
   footprint in 2D (both 90° rotations when allowed, never overhanging the pallet).
   Layers are limited by Max Stack Level, truck/door height and payload.
2. Truck floor: all "load units" (loose boxes, or pallets) of ALL packaging types are
   packed together, row by row along the truck length. Units are placed in
   single-type vertical stacks (limited by stack level and truck height); a row's depth
   is set by its lead unit and remaining width is filled with other units that fit.
   Payload is checked on every placement.
3. Fleet: trucks are filled one by one until the whole quantity is loaded. Several
   packing orders are tried and the cheapest result is kept. Alternatives: single truck
   type fleets, and "right-sized" mixed fleets (e.g. 2 x Large + 1 x Medium).
4. Every load plan is independently re-verified (bounds, stack height, payload).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from models import Packaging
from truck_config import Truck

# =========================================================================== #
# 2D rectangle packing (pallet layers)
# =========================================================================== #


@lru_cache(maxsize=None)
def _solve(A: int, B: int, a: int, b: int, rot: bool, depth: int):
    best_cnt, best_plan = 0, None
    orients = [(a, b)] + ([(b, a)] if rot and a != b else [])
    m = min(a, b)
    for p, q in orients:
        if p > A or q > B:
            continue
        nx, ny = A // p, B // q
        ks = range(1, nx + 1) if nx <= 40 else (nx,)
        for k in ks:
            cnt = k * ny
            if depth > 0:
                ra = A - k * p
                if ra >= m:
                    cnt += _solve(ra, B, a, b, rot, depth - 1)[0]
                tb = B - ny * q
                if tb >= m:
                    cnt += _solve(k * p, tb, a, b, rot, depth - 1)[0]
            if cnt > best_cnt:
                best_cnt, best_plan = cnt, (p, q, k, ny)
    return best_cnt, best_plan


def _layout(A, B, a, b, rot, depth, ox, oy, out):
    cnt, plan = _solve(A, B, a, b, rot, depth)
    if not plan:
        return
    p, q, k, ny = plan
    for i in range(k):
        for j in range(ny):
            out.append((ox + i * p, oy + j * q, p, q))
    if depth > 0:
        m = min(a, b)
        if A - k * p >= m:
            _layout(A - k * p, B, a, b, rot, depth - 1, ox + k * p, oy, out)
        if B - ny * q >= m:
            _layout(k * p, B - ny * q, a, b, rot, depth - 1, ox, oy + ny * q, out)


def count_2d(A: int, B: int, a: int, b: int, rot: bool) -> int:
    c1 = _solve(A, B, a, b, rot, 2)[0]
    c2 = _solve(B, A, a, b, rot, 2)[0] if rot else 0
    return max(c1, c2)


def pack_2d(A: int, B: int, a: int, b: int, rot: bool) -> List[Tuple[int, int, int, int]]:
    """Rectangles (x, y, w, h) of a-by-b items inside an A-by-B area (no overhang)."""
    c1 = _solve(A, B, a, b, rot, 2)[0]
    c2 = _solve(B, A, a, b, rot, 2)[0] if rot else 0
    out: List[Tuple[int, int, int, int]] = []
    if c2 > c1:
        tmp: List[Tuple[int, int, int, int]] = []
        _layout(B, A, a, b, rot, 2, 0, 0, tmp)
        out = [(y, x, h, w) for (x, y, w, h) in tmp]
    else:
        _layout(A, B, a, b, rot, 2, 0, 0, out)
    return out


# =========================================================================== #
# Data structures
# =========================================================================== #

@dataclass
class Group:
    """A set of identical load units (a loose box, a full pallet or a partial pallet)."""
    pid: str
    method: str          # "Direct" | "Pallet"
    kind: str            # "box" | "pallet" | "partial pallet"
    a: int               # footprint dimension (nominal length)
    b: int               # footprint dimension (nominal width)
    h: int               # unit height
    weight: float        # kg per unit (incl. pallet)
    boxes: int           # packaging units per load unit
    count: int           # number of load units
    max_levels: int      # units that may be stacked vertically
    rot: bool            # 90° rotation on the floor allowed
    gid: int = -1


@dataclass
class PkgPlan:
    ok: bool
    reason: str = ""
    groups: List[Group] = field(default_factory=list)
    info: Dict = field(default_factory=dict)


@dataclass
class TruckLoad:
    truck: Truck
    blocks: List[Dict]
    weight: float
    volume_mm3: float
    floor_area_mm2: float
    length_used: int
    boxes_by_pid: Dict[str, int]
    pallets_by_pid: Dict[str, int]
    info: Dict[str, Dict] = field(default_factory=dict)

    @property
    def boxes(self) -> int:
        return sum(self.boxes_by_pid.values())

    @property
    def pallets(self) -> int:
        return sum(self.pallets_by_pid.values())

    @property
    def space_util(self) -> float:
        return self.volume_mm3 / (self.truck.length_mm * self.truck.width_mm * self.truck.height_mm)

    @property
    def weight_util(self) -> float:
        return self.weight / self.truck.payload_kg

    @property
    def floor_util(self) -> float:
        return self.floor_area_mm2 / (self.truck.length_mm * self.truck.width_mm)

    @property
    def remaining_payload(self) -> float:
        return self.truck.payload_kg - self.weight

    @property
    def remaining_volume_m3(self) -> float:
        return self.truck.internal_volume_m3 - self.volume_mm3 / 1e9


@dataclass
class Option:
    scenario: str
    label: str
    composition: Dict[str, int]
    feasible: bool
    reason: str = ""
    loads: List[TruckLoad] = field(default_factory=list)
    methods: Dict[str, str] = field(default_factory=dict)
    cost: float = math.inf
    n_trucks: int = 0
    boxes: int = 0
    space_util: float = 0.0
    weight_util: float = 0.0
    floor_util: float = 0.0


@dataclass
class OptimizationResult:
    options: List[Option]
    recommended: Optional[Option]
    pkgs: List[Packaging]
    total_qty: int
    total_cargo_weight: float
    notes: List[str]
    warnings: List[str]


ORDER_KEYS = {
    "area": lambda g: (-(g.a * g.b), -g.weight),
    "weight": lambda g: (-g.weight, -(g.a * g.b)),
    "volume": lambda g: (-(g.a * g.b * g.h), -g.weight),
}


def _orients(g: Group):
    return [(g.a, g.b), (g.b, g.a)] if g.rot and g.a != g.b else [(g.a, g.b)]


def _variants(pkg: Packaging):
    """(a, b, h, rot) box orientations permitted by the orientation restriction."""
    L, W, H = pkg.length, pkg.width, pkg.height
    if pkg.orient == "Fixed":
        return [(L, W, H, False)]
    if pkg.orient == "Upright":
        return [(L, W, H, True)]
    out = []
    for v in [(L, W, H, True), (L, H, W, True), (W, H, L, True)]:
        if v not in out:
            out.append(v)
    return out


# =========================================================================== #
# Optimizer
# =========================================================================== #

class Optimizer:
    def __init__(self, pkgs: List[Packaging], trucks: List[Truck], settings: Dict):
        self.pkgs = pkgs
        self.trucks = sorted(trucks, key=lambda t: (t.cost, t.internal_volume_m3))
        self.s = settings
        self._choice: Dict[tuple, tuple] = {}

    # ------------------------------------------------------------------ geometry
    @staticmethod
    def _dims(t: Truck):
        return t.usable_length, t.usable_width, t.usable_height

    def _unit_fit(self, a, b, h, rot, t: Truck) -> str:
        UL, UW, UH = self._dims(t)
        if h > UH:
            return f"height {h} mm exceeds usable truck height {UH} mm"
        if not ((a <= UL and b <= UW) or (rot and b <= UL and a <= UW)):
            return f"footprint {a}×{b} mm does not fit the usable floor {UL}×{UW} mm"
        across = min(a, b) if rot else b
        if t.door_width_mm and across > t.door_width_mm:
            return f"width {across} mm exceeds door opening {t.door_width_mm} mm"
        if t.door_height_mm and h > t.door_height_mm:
            return f"height {h} mm exceeds door opening {t.door_height_mm} mm"
        return ""

    # ------------------------------------------------------------ package plans
    def plan_package(self, pkg: Packaging, qty: int, method: str, t: Truck) -> PkgPlan:
        UL, UW, UH = self._dims(t)
        if method == "Direct":
            if pkg.gross > t.payload_kg:
                return PkgPlan(False, f"one unit ({pkg.gross} kg) exceeds payload {t.payload_kg:.0f} kg")
            cands, reasons = [], []
            for v in _variants(pkg):
                why = self._unit_fit(*v, t)
                (reasons if why else cands).append(why or v)
            if not cands:
                return PkgPlan(False, reasons[0])
            key = (pkg.pid, "D", t.name)
            choice = self._choice.get(key)
            if choice is None:
                if len(cands) == 1:
                    choice = cands[0]
                else:
                    best, best_boxes = None, -1
                    for v in cands:
                        g = self._direct_group(pkg, qty, v, UH)
                        load = self.pack_truck([g], t, "area")
                        if load.boxes > best_boxes:
                            best, best_boxes = v, load.boxes
                    choice = best
                self._choice[key] = choice
            g = self._direct_group(pkg, qty, choice, UH)
            a, b, h, rot = choice
            info = {"method": "Direct", "orientation": f"{a}×{b} footprint, {h} mm high",
                    "levels": g.max_levels, "arrangement": f"loose boxes, up to {g.max_levels} high"}
            return PkgPlan(True, "", [g], info)

        # ---------------------------------------------------------------- pallet
        if not pkg.has_pallet:
            return PkgPlan(False, "pallet data missing")
        PL, PW, PH, PWT = pkg.pallet_length, pkg.pallet_width, pkg.pallet_height, pkg.pallet_weight
        why = self._unit_fit(PL, PW, PH, True, t)
        if why:
            return PkgPlan(False, "pallet: " + why)
        maxH = UH if not t.door_height_mm else min(UH, t.door_height_mm)
        cap_boxes = int((t.payload_kg - PWT) // pkg.gross)
        best, best_key = None, None
        for (a, b, h, rot) in _variants(pkg):
            per_layer = count_2d(PL, PW, a, b, rot)
            if per_layer == 0:
                continue
            layers = min((maxH - PH) // h, pkg.max_stack if pkg.stackable else 1)
            if layers < 1:
                continue
            bpp = min(per_layer * layers, cap_boxes)
            if bpp < 1:
                continue
            key = (bpp, -(PH + math.ceil(bpp / per_layer) * h))
            if best_key is None or key > best_key:
                best_key, best = key, (a, b, h, rot, per_layer, bpp)
        if best is None:
            return PkgPlan(False, "box does not fit on the pallet (footprint, height or weight limit)")
        a, b, h, rot, per_layer, bpp = best
        layers_eff = math.ceil(bpp / per_layer)
        hu = PH + layers_eff * h
        wu = bpp * pkg.gross + PWT
        levels = 1
        if self.s.get("max_pallet_stack_levels", 1) > 1 and pkg.stackable:
            levels = max(1, min(self.s["max_pallet_stack_levels"], pkg.max_stack, UH // hu))
        n_full, r = divmod(qty, bpp)
        groups = []
        if n_full:
            groups.append(Group(pkg.pid, "Pallet", "pallet", PL, PW, hu, wu, bpp, n_full, levels, True))
        if r:
            hr = PH + math.ceil(r / per_layer) * h
            groups.append(Group(pkg.pid, "Pallet", "partial pallet", PL, PW, hr, r * pkg.gross + PWT,
                                r, 1, 1, True))
        info = {"method": "Pallet", "orientation": f"{a}×{b} footprint, {h} mm high",
                "per_layer": per_layer, "layers": layers_eff, "boxes_per_pallet": bpp,
                "pallet": (PL, PW, PH), "box_ab": (a, b), "rot": rot,
                "layer_rects": pack_2d(PL, PW, a, b, rot),
                "arrangement": f"{per_layer}/layer × {layers_eff} layers on {PL}×{PW} mm pallet"
                               + (f", pallets stacked {levels} high" if levels > 1 else "")}
        return PkgPlan(True, "", groups, info)

    @staticmethod
    def _direct_group(pkg: Packaging, qty: int, v, UH: int) -> Group:
        a, b, h, rot = v
        levels = max(1, min(pkg.max_stack, UH // h)) if pkg.stackable else 1
        return Group(pkg.pid, "Direct", "box", a, b, h, pkg.gross, 1, qty, levels, rot)

    # ------------------------------------------------------------ truck packing
    def pack_truck(self, groups: List[Group], t: Truck, order: str = "area") -> TruckLoad:
        gs = sorted(groups, key=ORDER_KEYS[order])
        UL, UW, UH = self._dims(t)
        rem = [g.count for g in gs]
        x, wt = 0, 0.0
        blocks: List[Dict] = []
        while True:
            for i, g in enumerate(gs):           # weight-blocked groups never come back
                if rem[i] > 0 and wt + g.weight > t.payload_kg + 1e-9:
                    rem[i] = -rem[i]
            leads = [i for i in range(len(gs)) if rem[i] > 0]
            if not leads:
                break
            chosen = None
            for lead in leads:
                best = None
                for o in _orients(gs[lead]):
                    row = self._row(gs, rem, lead, o, x, wt, UL, UW, t.payload_kg)
                    if row and (best is None or row["score"] > best["score"]):
                        best = row
                if best:
                    chosen = best
                    break
            if not chosen:
                break
            blocks.extend(chosen["blocks"])
            rem, wt, x = chosen["rem"], chosen["wt"], x + chosen["depth"]
        return self._summarize(gs, blocks, t, wt, x)

    @staticmethod
    def _row(gs, rem, lead, o, x, wt, UL, UW, payload):
        d, ac = o
        if x + d > UL or ac > UW:
            return None
        rem = rem[:]
        left, blocks, vol, placed = UW, [], 0.0, 0
        order = [lead] + [i for i in range(len(gs)) if i != lead] + [lead]
        for pos, i in enumerate(order):
            g = gs[i]
            if rem[i] <= 0:
                continue
            if pos == 0:
                dd, aa = o
            else:
                opts = [(p, q) for p, q in _orients(g) if p <= d and q <= left]
                if not opts:
                    continue
                dd, aa = sorted(opts, key=lambda t: (-t[0], t[1]))[0]
            slots = left // aa
            if slots <= 0:
                continue
            cap = int((payload - wt + 1e-9) // g.weight) if g.weight > 0 else rem[i]
            if cap <= 0:
                continue
            lv = g.max_levels
            u = min(rem[i], cap, slots * lv)
            full, part = divmod(u, lv)
            if full:
                blocks.append(dict(i=i, pid=g.pid, x=x, y=UW - left, depth=dd, across=aa,
                                   n=full, levels=lv, units=full * lv, h=g.h))
                left -= full * aa
            if part:
                blocks.append(dict(i=i, pid=g.pid, x=x, y=UW - left, depth=dd, across=aa,
                                   n=1, levels=part, units=part, h=g.h))
                left -= aa
            rem[i] -= u
            wt += u * g.weight
            vol += u * g.a * g.b * g.h
            placed += u
        if placed == 0:
            return None
        return {"blocks": blocks, "depth": d, "rem": rem, "wt": wt, "score": vol / (d * UW)}

    @staticmethod
    def _summarize(gs, blocks, t, wt, x) -> TruckLoad:
        boxes, pallets, vol, area = {}, {}, 0.0, 0.0
        for b in blocks:
            g = gs[b["i"]]
            b["method"], b["kind"] = g.method, g.kind
            boxes[g.pid] = boxes.get(g.pid, 0) + b["units"] * g.boxes
            if g.method == "Pallet":
                pallets[g.pid] = pallets.get(g.pid, 0) + b["units"]
            vol += b["units"] * g.a * g.b * g.h
            area += b["n"] * g.a * g.b
        return TruckLoad(t, blocks, wt, vol, area, x, boxes, pallets)

    # -------------------------------------------------------------- fleet logic
    def _pack_remaining(self, rem: Dict[str, int], methods: Dict[str, str], t: Truck, order: str):
        groups, infos = [], {}
        for pkg in self.pkgs:
            q = rem[pkg.pid]
            if q <= 0:
                continue
            plan = self.plan_package(pkg, q, methods[pkg.pid], t)
            if not plan.ok:
                return None, f"{pkg.pid}: {plan.reason}"
            for g in plan.groups:
                groups.append(g)
            infos[pkg.pid] = plan.info
        load = self.pack_truck(groups, t, order)
        load.info = infos
        if load.boxes == 0:
            return None, "no unit could be placed (payload or space too small)"
        return load, ""

    def _run_fleet(self, methods, order, primary: Truck, downsize: bool):
        rem = {p.pid: p.qty for p in self.pkgs}
        loads: List[TruckLoad] = []
        maxT = self.s.get("max_trucks_per_option", 300)
        while any(v > 0 for v in rem.values()):
            if len(loads) >= maxT:
                return None, f"needs more than {maxT} trucks"
            chosen = None
            if downsize:
                tot_w = sum(rem[p.pid] * p.gross for p in self.pkgs)
                for t in self.trucks:
                    if t.cost > primary.cost:
                        break
                    if tot_w > t.payload_kg:
                        continue
                    load, _ = self._pack_remaining(rem, methods, t, order)
                    if load and all(rem[p] - load.boxes_by_pid.get(p, 0) == 0 for p in rem):
                        chosen = load
                        break
            if chosen is None:
                chosen, err = self._pack_remaining(rem, methods, primary, order)
                if chosen is None:
                    return None, err
            loads.append(chosen)
            for pid, n in chosen.boxes_by_pid.items():
                rem[pid] -= n
        return loads, ""

    def _make_option(self, scenario, methods, loads, label=None) -> Option:
        comp: Dict[str, int] = {}
        for l in loads:
            comp[l.truck.name] = comp.get(l.truck.name, 0) + 1
        tcount = sum(comp.values())
        cap_w = sum(l.truck.payload_kg for l in loads)
        cap_v = sum(l.truck.length_mm * l.truck.width_mm * l.truck.height_mm for l in loads)
        cap_f = sum(l.truck.length_mm * l.truck.width_mm for l in loads)
        opt = Option(
            scenario=scenario,
            label=label or " + ".join(f"{n} × {k}" if len(comp) > 1 else k for k, n in comp.items()),
            composition=comp, feasible=True, loads=loads, methods=methods,
            cost=sum(l.truck.cost for l in loads), n_trucks=tcount,
            boxes=sum(l.boxes for l in loads),
            space_util=sum(l.volume_mm3 for l in loads) / cap_v,
            weight_util=sum(l.weight for l in loads) / cap_w,
            floor_util=sum(l.floor_area_mm2 for l in loads) / cap_f,
        )
        by_name = {t.name: t for t in self.trucks}
        for name, n in comp.items():
            av = by_name[name].available
            if av is not None and n > av:
                opt.feasible = False
                opt.reason = f"needs {n} × {name} but only {av} available"
        return opt

    def _best_fleet(self, scenario, methods, primary, downsize, label=None) -> Option:
        best, first_err = None, ""
        for order in ORDER_KEYS:
            loads, err = self._run_fleet(methods, order, primary, downsize)
            if loads is None:
                first_err = first_err or err
                continue
            opt = self._make_option(scenario, methods, loads, label)
            key = (opt.cost, opt.n_trucks, -opt.space_util)
            if best is None or key < best[0]:
                best = (key, opt)
        if best is None:
            return Option(scenario, label or primary.name, {primary.name: 0}, False, first_err, methods=methods)
        return best[1]

    # ---------------------------------------------------------------- scenarios
    def build_scenarios(self):
        a, b, notes = {}, {}, []
        for p in self.pkgs:
            if p.pallet_req == "No":
                a[p.pid] = b[p.pid] = "Direct"
            elif p.pallet_req == "Yes":
                a[p.pid] = b[p.pid] = "Pallet"
            else:
                a[p.pid] = "Direct"
                b[p.pid] = "Pallet" if p.has_pallet else "Direct"
        def lab(m):
            s = set(m.values())
            return "Direct Loading" if s == {"Direct"} else "Pallet Loading" if s == {"Pallet"} else "Mixed (Direct + Pallet)"
        scen = [(lab(a), a)]
        if b != a:
            la, lb = lab(a), lab(b)
            if la == lb:
                scen = [(la + " – optional items direct", a), (lb + " – optional items pallet", b)]
            else:
                scen.append((lb, b))
        return scen

    def optimize(self) -> OptimizationResult:
        options: List[Option] = []
        for scenario, methods in self.build_scenarios():
            for t in self.trucks:
                options.append(self._best_fleet(scenario, methods, t, False))
            for t in self.trucks:
                opt = self._best_fleet(scenario, methods, t, True)
                if opt.feasible is not None and opt.loads and len(opt.composition) > 1:
                    options.append(opt)
        feas = [o for o in options if o.feasible]
        rec = min(feas, key=lambda o: (o.cost, o.n_trucks, -o.space_util)) if feas else None
        notes = [
            "RESULT TYPE: feasible loading recommendation from a heuristic (row/stack packing). "
            "It is not a guaranteed global optimum.",
            "Truck dimensions, payloads, door openings and costs come from truck_config.py and are "
            "ILLUSTRATIVE – verify against actual vehicles before operational use.",
            "NOT VERIFIED – axle load distribution / centre of gravity (no axle or weight-distribution "
            "data is used).",
            "NOT VERIFIED – crush strength of packaging, pallet load rating and product fragility. "
            "Only the 'Max Stack Level', 'Stackable' and 'Orientation Restriction' inputs are enforced.",
            "Each stack and each pallet contains a single packaging type; different packaging types are "
            "combined by sharing truck floor rows, weight and height limits (no independent summation).",
            "Pallets are not stacked unless 'max_pallet_stack_levels' > 1 in truck_config.py. "
            "Boxes never overhang the pallet.",
            "Door check: smallest footprint side and unit height must pass the door opening.",
            "Securing, dunnage and loading sequence (unloading order) are not modelled.",
        ]
        warn = []
        for p in self.pkgs:
            if p.pallet_req == "Optional" and not p.has_pallet:
                warn.append(f"{p.pid}: pallet optional but pallet data incomplete – direct loading only.")
        return OptimizationResult(options, rec, self.pkgs, sum(p.qty for p in self.pkgs),
                                  sum(p.qty * p.gross for p in self.pkgs), notes, warn)


# =========================================================================== #
# Independent verification of a load plan
# =========================================================================== #

def verify_load(load: TruckLoad, pkgs: Optional[List[Packaging]] = None) -> List[str]:
    """Re-check bounds, stack height, row overlap and payload. Returns a list of problems."""
    t = load.truck
    UL, UW, UH = t.usable_length, t.usable_width, t.usable_height
    probs: List[str] = []
    rows: Dict[int, List[Dict]] = {}
    for b in load.blocks:
        rows.setdefault(b["x"], []).append(b)
        if b["x"] + b["depth"] > UL:
            probs.append(f"block at x={b['x']} exceeds usable length")
        if b["y"] + b["n"] * b["across"] > UW:
            probs.append(f"block at x={b['x']} exceeds usable width")
        if b["levels"] * b["h"] > UH:
            probs.append(f"stack at x={b['x']} exceeds usable height ({b['levels'] * b['h']} > {UH})")
    prev_end = 0
    for x in sorted(rows):
        if x < prev_end:
            probs.append(f"row at x={x} overlaps previous row")
        prev_end = x + max(b["depth"] for b in rows[x])
        used = sum(b["n"] * b["across"] for b in rows[x])
        if used > UW:
            probs.append(f"row at x={x} width {used} > {UW}")
        spans = sorted((b["y"], b["y"] + b["n"] * b["across"]) for b in rows[x])
        for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
            if s2 < e1:
                probs.append(f"blocks overlap in row at x={x}")
    if load.weight > t.payload_kg + 1e-6:
        probs.append(f"payload exceeded: {load.weight:.1f} > {t.payload_kg:.1f} kg")
    if pkgs:
        for p in pkgs:
            if p.stackable is False:
                for b in load.blocks:
                    if b["pid"] == p.pid and b["method"] == "Direct" and b["levels"] > 1:
                        probs.append(f"{p.pid} is not stackable but stacked {b['levels']} high")
            else:
                for b in load.blocks:
                    if b["pid"] == p.pid and b["method"] == "Direct" and b["levels"] > p.max_stack:
                        probs.append(f"{p.pid} stacked {b['levels']} > max {p.max_stack}")
    return probs
