"""Integration tests: runner classification <-> truck optimization.  Run: python test_dispatch_plan.py"""
import io

import pandas as pd

import dispatch_plan as dp
import part_runner as pr
from loading_engine import Optimizer, verify_load
from truck_config import SETTINGS, load_trucks
from validators import validate_dataframe

TRUCKS = load_trucks()
END = pd.Period("2026-10", "M")


class Up:  # minimal stand-in for a Streamlit UploadedFile
    def __init__(self, data, name="x.xlsx"):
        self.name, self._d = name, data

    def getvalue(self):
        return self._d


def load_sample():
    wb = Up(dp.build_planning_workbook_bytes())
    g = lambda k: dp.read_sheet(wb, dp.SHEETS[k])
    clean, errs, _ = pr.validate_dispatch(g("history"))
    assert not errs, errs
    master, msgs = pr.read_master(g("master"))
    pkgs, perrs, _ = validate_dataframe(g("packaging"), quantity_optional=True)
    assert not perrs, perrs
    ship, serrs, _ = dp.read_shipment(g("shipment"))
    assert not serrs, serrs
    return clean, master, pkgs, ship


def test_sample_end_to_end():
    clean, master, pkgs, ship = load_sample()
    runner = pr.classify(clean, END, master)
    derived, part_ship, errs, _ = dp.build_shipment_plan(ship, master, pkgs)
    assert not errs, errs
    qty = {p.pid: p.qty for p in derived}
    assert qty == {"PK-A": 100, "PK-B": 77, "PK-C": 75}, qty            # ceil(qty / per-pack), summed per packaging
    res = Optimizer(derived, TRUCKS, SETTINGS).optimize()
    assert res.recommended and all(not verify_load(l, derived) for l in res.recommended.loads)
    t = dp.build_part_table(runner, part_ship, res.recommended, master, pkgs)
    assert len(t) == runner.unique_parts == 14                          # incl. 2 master-only parts
    r = t.set_index("Part Number")
    assert r.loc["NM-6002", "Runner Category"] == r.loc["NM-6003", "Runner Category"] == "Non-Moving"
    assert r.loc["FR-1001", "Runner Category"] == "Fast Runner" and r.loc["FR-1001", "Packs Required"] == 60
    assert "New shipment entered for a Non-Moving part" in r.loc["NM-6001", "Planning Note"]
    assert "Excluded from active dispatch planning" in r.loc["NM-6002", "Planning Note"]
    assert r.loc["ST-5001", "Current Shipment Qty"] == 0                # not shipped -> not in truck plan
    shipped = r[r["Current Shipment Qty"] > 0]
    assert abs(shipped["Allocated Transport Cost (₹)"].sum() - res.recommended.cost) <= len(shipped)  # rounding only
    assert (shipped["Truck(s) Carrying"] != "").all()
    rep = dp.build_integrated_report(runner, t, res)
    x = pd.ExcelFile(io.BytesIO(rep))
    assert x.sheet_names == ["Runner Summary", "Part Planning", "Truck Summary", "Truck Comparison",
                             "Loading Details", "Monthly Dispatch Matrix"], x.sheet_names
    print("end-to-end ok →", res.recommended.scenario, res.recommended.label, f"₹{res.recommended.cost:,.0f}")
    print(t[["Part Number", "Runner Category", "Current Shipment Qty", "Packs Required", "Truck(s) Carrying",
             "Allocated Transport Cost (₹)"]].head(8).to_string(index=False))


def test_runner_never_drives_truck_choice():
    clean, master, pkgs, ship = load_sample()
    derived, _, _, _ = dp.build_shipment_plan(ship, master, pkgs)
    base = Optimizer(derived, TRUCKS, SETTINGS).optimize().recommended
    # change the dispatch history completely (different categories) – truck result must be identical
    c2 = clean[clean["Part Number"].isin(["FR-1002"])].copy()
    r1, r2 = pr.classify(clean, END, master), pr.classify(c2, END, master)
    assert r1.summary["Number of Parts"].tolist() != r2.summary["Number of Parts"].tolist()
    again = Optimizer(derived, TRUCKS, SETTINGS).optimize().recommended
    assert (base.label, base.n_trucks, base.cost, base.scenario) == (again.label, again.n_trucks, again.cost, again.scenario)
    # the optimizer does not even receive the category
    assert not any(hasattr(p, "category") or hasattr(p, "runner") for p in derived)
    print("truck result independent of runner category ok")


def test_link_errors():
    clean, master, pkgs, _ = load_sample()
    ship = pd.DataFrame({"Part Number": ["FR-1001", "UNKNOWN", "NM-6001"], "Shipment Quantity": [10, 5, 7]})
    s, e, _ = dp.read_shipment(ship)
    assert not e
    m2 = master.copy()
    m2.loc[m2["Part Number"] == "NM-6001", "Packaging ID"] = "PK-ZZ"
    _, _, errs, _ = dp.build_shipment_plan(s, m2, pkgs)
    msgs = " | ".join(x["Issue"] for x in errs)
    assert "not in the Part Master" in msgs and "'PK-ZZ' is not in the Packaging Master" in msgs, msgs
    _, errs, _ = dp.read_shipment(pd.DataFrame({"Part Number": ["A", "B", ""], "Shipment Quantity": [-1, "x", 4]}))
    assert errs is not None and len(errs) == 3
    _, _, errs, _ = dp.build_shipment_plan(s, None, pkgs)
    assert errs and "Part Master" in errs[0]["Issue"]
    print("link / validation errors ok")


def test_duplicate_shipment_rows_and_legacy_mode():
    s, e, w = dp.read_shipment(pd.DataFrame({"Part Number": ["a-1", "A-1"], "Shipment Quantity": [3, 4]}))
    assert not e and len(s) == 1 and s.loc[0, "Shipment Quantity"] == 7 and w
    # legacy: packaging file with its own Quantity still works and needs no shipment
    df = pd.read_excel(io.BytesIO(__import__("sample_data").build_template_bytes()), sheet_name="Packaging Data")
    pkgs, errs, _ = validate_dataframe(df)
    assert not errs and [p.qty for p in pkgs] == [100, 50, 75]
    _, errs, _ = validate_dataframe(df.drop(columns=["Quantity"]))
    assert errs and "Quantity" in errs[0]["Column"]                     # strict unless a shipment supplies it
    print("duplicates / legacy packaging-quantity mode ok")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("\nALL INTEGRATION TESTS PASSED")
