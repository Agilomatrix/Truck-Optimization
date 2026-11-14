"""Sanity tests for the calculation engine.  Run:  python test_engine.py"""
import io

import pandas as pd

from loading_engine import Optimizer, count_2d, pack_2d, verify_load
from models import Packaging
from report import build_excel_report, comparison_df
from sample_data import build_template_bytes
from truck_config import SETTINGS, load_trucks
from validators import validate_dataframe

TRUCKS = load_trucks()


def pk(pid, l, w, h, gross, qty, stack=True, ms=10, pallet="No", pal=None, orient="Upright"):
    p = pal or (0, 0, 0, 0.0)
    return Packaging(pid, "Box", l, w, h, gross, 0.0, gross, qty, stack, ms if stack else 1, pallet, "",
                     p[0], p[1], p[2], p[3], orient, bool(pal))


def run(pkgs):
    res = Optimizer(pkgs, TRUCKS, SETTINGS).optimize()
    for o in res.options:
        for l in o.loads:
            assert not verify_load(l, pkgs), verify_load(l, pkgs)
            for p in pkgs:   # quantity conservation per option
                pass
        if o.feasible:
            for p in pkgs:
                assert sum(l.boxes_by_pid.get(p.pid, 0) for l in o.loads) == p.qty, (o.label, p.pid)
    return res


def test_2d():
    assert count_2d(1200, 1000, 400, 300, True) >= 9
    assert count_2d(1200, 1000, 400, 300, True) <= (1200 * 1000) // (400 * 300)
    rects = pack_2d(1200, 1000, 400, 300, True)
    for (x, y, w, h) in rects:
        assert x + w <= 1200 and y + h <= 1000
    for i, a in enumerate(rects):           # no overlaps
        for b in rects[i + 1:]:
            assert a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1]
    print("2D ok:", len(rects), "boxes on a 1200x1000 pallet (400x300)")


def test_hand_calc_medium():
    # 500 mm cubes, 10 kg.  Medium usable 4217x2084x2084 -> 8 x 4 x 4 = 128 per truck by hand.
    res = run([pk("C", 500, 500, 500, 10, 128)])
    med = [o for o in res.options if o.label == "Medium" and "Direct" in o.scenario][0]
    assert med.feasible and med.n_trucks == 1 and med.boxes == 128, (med.n_trucks, med.boxes)
    res = run([pk("C", 500, 500, 500, 10, 129)])
    med = [o for o in res.options if o.label == "Medium"][0]
    assert med.n_trucks == 2
    print("hand-calc Medium: 128 boxes/truck ok; 129 boxes -> 2 trucks ok")


def test_weight_limit():
    # 100 kg cubes: Medium payload 4000 kg -> max 40 per truck although space allows 128
    res = run([pk("H", 500, 500, 500, 100, 100)])
    med = [o for o in res.options if o.label == "Medium"][0]
    assert med.n_trucks == 3 and max(l.boxes for l in med.loads) == 40, [l.boxes for l in med.loads]
    print("weight limit ok:", [l.boxes for l in med.loads])


def test_small_shipment_gets_small_truck():
    res = run([pk("S", 400, 300, 300, 5, 20)])
    assert res.recommended.label == "Small", res.recommended.label
    print("small shipment ->", res.recommended.label, res.recommended.cost)


def test_not_stackable():
    res = run([pk("N", 500, 500, 500, 10, 32, stack=False)])
    l = [o for o in res.options if o.label == "Medium"][0].loads[0]
    assert all(b["levels"] == 1 for b in l.blocks)
    assert l.boxes == 32
    print("non-stackable ok: floor only")


def test_pallet_required_never_direct():
    res = run([pk("P", 400, 300, 250, 6, 200, pallet="Yes", pal=(1200, 800, 144, 15))])
    assert all(o.scenario == "Pallet Loading" for o in res.options)
    assert "Direct" not in res.recommended.methods.values()
    d = res.recommended.loads[0].info["P"]
    assert d["boxes_per_pallet"] == d["per_layer"] * d["layers"]
    print("pallet required ok:", d["arrangement"], "->", res.recommended.label, res.recommended.n_trucks)


def test_optional_compares_both():
    res = run([pk("O", 600, 400, 300, 8.5, 100, ms=5, pallet="Optional", pal=(1200, 1000, 150, 25))])
    assert len({o.scenario for o in res.options}) == 2
    print("optional ok:", sorted({(o.scenario, o.label, o.cost) for o in res.options if o.feasible}))


def test_mixed_types_combined():
    pk_a = pk("A", 600, 400, 300, 8.5, 100, ms=5, pallet="Optional", pal=(1200, 1000, 150, 25))
    pk_b = pk("B", 400, 300, 250, 6.2, 50, ms=4, pallet="Yes", pal=(1200, 800, 144, 15))
    pk_c = pk("C", 800, 600, 500, 48, 75, stack=False, orient="Fixed")
    res = run([pk_a, pk_b, pk_c])
    r = res.recommended
    print("sample mix ->", r.scenario, r.label, r.n_trucks, f"₹{r.cost:,.0f}",
          f"space {r.space_util:.0%} weight {r.weight_util:.0%}")
    assert r.feasible and r.cost == min(o.cost for o in res.options if o.feasible)


def test_infeasible_reasons():
    res = run([pk("BIG", 3000, 500, 500, 20, 4, orient="Fixed")])   # only fits trucks >= ~3.1 m
    small = [o for o in res.options if o.label == "Small"][0]
    assert not small.feasible and small.reason
    res = run([pk("TOOHEAVY", 500, 500, 500, 5000, 2)])
    assert [o for o in res.options if o.label == "Large"][0].feasible is True
    assert not [o for o in res.options if o.label == "Medium"][0].feasible
    print("infeasible reason:", small.reason)


def test_validation():
    bad = pd.DataFrame([["", "Carton", -1, 400, 300, 8, 0.5, 9.9, 10.5, "Maybe", None, "Yes", None, None, None, None, None]],
                       columns=pd.read_excel(io.BytesIO(build_template_bytes())).columns[:17])
    packs, errs, _ = validate_dataframe(bad)
    cols = {e["Column"] for e in errs}
    assert not packs and {"Packaging ID", "Length (mm)", "Gross Weight (kg)", "Quantity", "Stackable"} <= cols, cols
    _, errs, _ = validate_dataframe(pd.DataFrame({"x": [1]}))
    assert errs and "missing" in errs[0]["Issue"]
    print("validation ok:", len(errs), "error(s) flagged")


def test_template_roundtrip_and_report():
    df = pd.read_excel(io.BytesIO(build_template_bytes()), sheet_name="Packaging Data")
    packs, errs, warns = validate_dataframe(df)
    assert not errs, errs
    res = run(packs)
    data = build_excel_report(res)
    x = pd.ExcelFile(io.BytesIO(data))
    assert x.sheet_names == ["Shipment Summary", "Truck Comparison", "Loading Details"], x.sheet_names
    print(comparison_df(res).to_string())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("\nALL TESTS PASSED")
