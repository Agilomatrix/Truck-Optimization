"""
Dispatch planning – connects Part Runner Classification to the Truck Loading Optimization.

Data flow
---------
Current Shipment (Part Number, Shipment Quantity)
   + Part Master (Part Number -> Packaging ID, Qty per Packaging)
   -> packs required = ceil(shipment qty / qty per pack)  -> packaging quantities
   -> Optimizer (dimensions, gross weight, pallets, payload, truck cost)  -> trucks & cost

IMPORTANT: the runner category is NEVER an input to the truck calculation. It is attached to the
results afterwards as planning context only (see `build_part_table`).
"""
from __future__ import annotations

import io
import math
from dataclasses import replace
from io import BytesIO
from typing import Dict, List, Optional, Tuple

import pandas as pd

import part_runner as pr
from loading_engine import OptimizationResult, Option
from models import Packaging
from report import comparison_df, loading_details_df, summary_rows, truck_wise_df

SHEETS = {
    "history": ["dispatch history", "dispatch"],
    "master": ["part master", "master"],
    "packaging": ["packaging master", "packaging data", "packaging"],
    "shipment": ["current shipment", "shipment"],
}

_SHIP_ALIASES = {
    "Part Number": pr._ALIASES["Part Number"],
    "Shipment Quantity": ["shipment quantity", "shipment qty", "current shipment quantity", "order quantity",
                          "order qty", "quantity", "qty"],
}

PLAN_NOTES = {
    "Fast Runner": "Regular monthly dispatch – candidate for recurring truck planning.",
    "High Runner": "Frequent dispatch – plan regular trips and consolidate shipments.",
    "Medium Runner": "Periodic dispatch – plan periodic trips.",
    "Slow Runner": "Occasional dispatch – look for consolidation with other shipments.",
    "Stranger / One-Time": "One-time part – transport planned on the actual order quantity.",
    "Non-Moving": "No dispatch in the period – excluded from active dispatch planning.",
}


# --------------------------------------------------------------------------- #
# Reading inputs
# --------------------------------------------------------------------------- #

def read_source(upload, names: List[str]) -> pd.DataFrame:
    """CSV, or the sheet named like `names` (else the first sheet) of an Excel file."""
    raw = upload.getvalue()
    if upload.name.lower().endswith(".csv"):
        return pd.read_csv(io.BytesIO(raw))
    xl = pd.ExcelFile(io.BytesIO(raw))
    sheet = next((s for s in xl.sheet_names if pr._norm(s) in names), xl.sheet_names[0])
    return xl.parse(sheet)


def read_sheet(upload, names: List[str]) -> Optional[pd.DataFrame]:
    """Sheet named like `names` from a planning workbook, or None if the sheet is absent."""
    xl = pd.ExcelFile(io.BytesIO(upload.getvalue()))
    sheet = next((s for s in xl.sheet_names if pr._norm(s) in names), None)
    return xl.parse(sheet) if sheet else None


def read_shipment(df: pd.DataFrame) -> Tuple[Optional[pd.DataFrame], List[Dict], List[Dict]]:
    """Current shipment: Part Number + Shipment Quantity (in parts). Returns (df, errors, warnings)."""
    errors: List[Dict] = []
    warns: List[Dict] = []
    actual = {pr._norm(c): c for c in df.columns}
    cols = {}
    for std, aliases in _SHIP_ALIASES.items():
        for a in aliases:
            if a in actual:
                cols[std] = actual[a]
                break
    missing = [k for k in _SHIP_ALIASES if k not in cols]
    if missing:
        errors.append({"Row": "-", "Part Number": "-",
                       "Issue": f"Current Shipment is missing column(s): {', '.join(missing)}."})
        return None, errors, warns
    rows = []
    for idx, r in df.iterrows():
        part, raw = pr.clean_part(r[cols["Part Number"]]), r[cols["Shipment Quantity"]]
        if not part and pr._blank(raw):
            continue
        row = int(idx) + 2
        if not part:
            errors.append({"Row": row, "Part Number": "(blank)", "Issue": "Part Number is missing."})
            continue
        q = pd.to_numeric(str(raw).replace(",", "").strip(), errors="coerce") if not pr._blank(raw) else float("nan")
        if pd.isna(q):
            errors.append({"Row": row, "Part Number": part, "Issue": "Shipment Quantity is missing or not a number."})
        elif q <= 0 or abs(q - round(q)) > 1e-9:
            errors.append({"Row": row, "Part Number": part, "Issue": "Shipment Quantity must be a positive whole number."})
        else:
            rows.append((part, int(round(q))))
    if errors:
        return None, errors, warns
    if not rows:
        errors.append({"Row": "-", "Part Number": "-", "Issue": "Current Shipment has no rows."})
        return None, errors, warns
    s = pd.DataFrame(rows, columns=["Part Number", "Shipment Quantity"])
    s["key"] = s["Part Number"].str.upper()
    if s["key"].duplicated().any():
        warns.append({"Row": "-", "Part Number": ", ".join(s.loc[s["key"].duplicated(), "Part Number"].unique()),
                      "Issue": "Part appears more than once in the Current Shipment – quantities were added together."})
    s = s.groupby("key", as_index=False).agg({"Part Number": "first", "Shipment Quantity": "sum"})
    return s, errors, warns


# --------------------------------------------------------------------------- #
# Shipment -> packaging quantities
# --------------------------------------------------------------------------- #

def build_shipment_plan(ship: pd.DataFrame, master: Optional[pd.DataFrame], pkgs: List[Packaging]
                        ) -> Tuple[List[Packaging], Optional[pd.DataFrame], List[Dict], List[Dict]]:
    """Translate part quantities into packaging quantities. Returns (packagings, part rows, errors, warnings)."""
    errors: List[Dict] = []
    warns: List[Dict] = []
    if master is None:
        errors.append({"Row": "-", "Part Number": "-",
                       "Issue": "A Part Master (with Packaging ID and Qty per Packaging) is required to convert "
                                "the Current Shipment into packaging quantities."})
        return [], None, errors, warns
    by_id = {p.pid.upper(): p for p in pkgs}
    mi = master.set_index("key")
    rows, packs_by_pkg = [], {}
    for _, r in ship.iterrows():
        k, part, q = r["key"], r["Part Number"], int(r["Shipment Quantity"])
        if k not in mi.index:
            errors.append({"Row": "-", "Part Number": part, "Issue": "Part is not in the Part Master."})
            continue
        m = mi.loc[k]
        pid = str(m["Packaging ID"]).strip()
        qpp = m["Qty per Packaging"]
        if not pid:
            errors.append({"Row": "-", "Part Number": part, "Issue": "Part Master has no Packaging ID for this part."})
        elif pid.upper() not in by_id:
            errors.append({"Row": "-", "Part Number": part,
                           "Issue": f"Packaging ID '{pid}' is not in the Packaging Master."})
        elif pd.isna(qpp) or qpp <= 0:
            errors.append({"Row": "-", "Part Number": part,
                           "Issue": "Part Master has no valid 'Qty per Packaging' (parts per pack) for this part."})
        else:
            packs = int(math.ceil(q / float(qpp)))
            canon = by_id[pid.upper()].pid
            packs_by_pkg[canon] = packs_by_pkg.get(canon, 0) + packs
            rows.append({"key": k, "Part Number": part, "Shipment Quantity": q, "Packaging ID": canon,
                         "Qty per Packaging": float(qpp), "Packs Required": packs})
    if errors:
        return [], None, errors, warns
    derived = [replace(p, qty=packs_by_pkg[p.pid]) for p in pkgs if p.pid in packs_by_pkg]
    warns.append({"Row": "-", "Part Number": "-",
                  "Issue": "Packs = shipment quantity ÷ Qty per Packaging, rounded up. Every pack is assumed to carry "
                           "its full gross weight (conservative for a part-filled last pack)."})
    return derived, pd.DataFrame(rows), errors, warns


# --------------------------------------------------------------------------- #
# Results table (runner category is attached AFTER the truck calculation)
# --------------------------------------------------------------------------- #

def allocate_costs(opt: Option) -> Tuple[Dict[str, float], Dict[str, List[str]]]:
    """Share each truck's cost between the packagings on it by the volume they occupy."""
    cost: Dict[str, float] = {}
    trucks: Dict[str, List[str]] = {}
    for i, l in enumerate(opt.loads, 1):
        vol: Dict[str, float] = {}
        for b in l.blocks:
            vol[b["pid"]] = vol.get(b["pid"], 0.0) + b["units"] * b["depth"] * b["across"] * b["h"]
        tot = sum(vol.values()) or 1.0
        for pid, v in vol.items():
            cost[pid] = cost.get(pid, 0.0) + l.truck.cost * v / tot
            trucks.setdefault(pid, []).append(f"#{i} {l.truck.name}")
    return cost, trucks


def planning_note(cat: str, ship_qty: int, n_shipped_parts: int) -> str:
    if ship_qty > 0:
        if cat == "Non-Moving":
            return "New shipment entered for a Non-Moving part – included in the truck plan; confirm the quantity."
        if cat == "Stranger / One-Time":
            return "In current shipment. One-time part – transport planned on the actual order quantity."
        s = "In current shipment. " + PLAN_NOTES[cat]
        if cat in ("High Runner", "Medium Runner", "Slow Runner") and n_shipped_parts > 1:
            s += f" Consolidated with {n_shipped_parts - 1} other part(s) in this shipment."
        return s
    if cat == "Non-Moving":
        return "Excluded from active dispatch planning (no shipment entered)."
    return "Not in current shipment. " + PLAN_NOTES[cat]


def build_part_table(runner: pr.RunnerResult, part_ship: Optional[pd.DataFrame], opt: Optional[Option],
                     master: Optional[pd.DataFrame], all_pkgs: List[Packaging]) -> pd.DataFrame:
    t = runner.parts.copy()
    if "Part Description" not in t:
        t.insert(1, "Part Description", "")
    t = t.rename(columns={"Dispatch Months": "Active Dispatch Months"})
    t["Avg Qty per Active Month"] = (t["Total Dispatch Quantity"] /
                                     t["Active Dispatch Months"].where(t["Active Dispatch Months"] > 0)).round(1)
    t["key"] = t["Part Number"].str.upper()

    # packaging & pallet requirement for every part that has a master link
    pk = {p.pid.upper(): p for p in all_pkgs}
    if master is not None:
        mm = master.set_index("key")
        pid = t["key"].map(lambda k: str(mm.loc[k, "Packaging ID"]).strip() if k in mm.index else "")
        t["Packaging ID"] = pid.map(lambda x: pk[x.upper()].pid if x.upper() in pk else x)
        t["Qty per Packaging"] = t["key"].map(lambda k: mm.loc[k, "Qty per Packaging"] if k in mm.index else float("nan"))
    else:
        t["Packaging ID"], t["Qty per Packaging"] = "", float("nan")
    t["Pallet Required"] = t["Packaging ID"].map(lambda x: pk[x.upper()].pallet_req if x and x.upper() in pk else "")

    ship = part_ship.set_index("key") if part_ship is not None else None
    t["Current Shipment Qty"] = t["key"].map(lambda k: int(ship.loc[k, "Shipment Quantity"]) if ship is not None and k in ship.index else 0)
    t["Packs Required"] = t["key"].map(lambda k: int(ship.loc[k, "Packs Required"]) if ship is not None and k in ship.index else None)

    t["Loading Method"], t["Truck(s) Carrying"], t["Allocated Transport Cost (₹)"] = "", "", None
    if opt is not None and opt.feasible and ship is not None:
        cost_pid, trucks_pid = allocate_costs(opt)
        tot_packs = part_ship.groupby("Packaging ID")["Packs Required"].sum()
        for i, r in t.iterrows():
            if r["Current Shipment Qty"] > 0:
                p = r["Packaging ID"]
                t.at[i, "Loading Method"] = opt.methods.get(p, "")
                t.at[i, "Truck(s) Carrying"] = ", ".join(trucks_pid.get(p, []))
                t.at[i, "Allocated Transport Cost (₹)"] = round(cost_pid.get(p, 0.0) * r["Packs Required"] / tot_packs[p])
    n_ship = int((t["Current Shipment Qty"] > 0).sum())
    t["Planning Note"] = [planning_note(c, q, n_ship) for c, q in zip(t["Runner Category"], t["Current Shipment Qty"])]
    cols = ["Part Number", "Part Description", "Active Dispatch Months", "Total Dispatch Quantity", "Runner Category",
            "Percentage Contribution (%)", "Avg Qty per Active Month", "Current Shipment Qty", "Packaging ID",
            "Qty per Packaging", "Packs Required", "Pallet Required", "Loading Method", "Truck(s) Carrying",
            "Allocated Transport Cost (₹)", "Planning Note"]
    return t[cols].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Excel report (classification + truck optimization)
# --------------------------------------------------------------------------- #

def build_integrated_report(runner: pr.RunnerResult, part_table: pd.DataFrame,
                            opt_res: Optional[OptimizationResult]) -> bytes:
    buf = BytesIO()
    header_rows: Dict[str, List[Tuple[int, int]]] = {}
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        info = pd.DataFrame([
            ("Analysis period", f"{runner.start} to {runner.end} ({pr.PERIOD_MONTHS} months)"),
            ("Unique part numbers", runner.unique_parts),
            ("Total dispatch quantity", runner.total_qty),
            ("Classification basis", "Distinct months with a dispatch (quantity and transaction count not used)"),
            ("Percentage Contribution", "Part's share of total dispatch quantity in the period"),
            ("Truck planning", "Calculated from shipment quantity, packaging, weight, pallets, truck specs and cost – "
                               "NOT from the runner category"),
        ], columns=["Item", "Value"])
        info.to_excel(xw, sheet_name="Runner Summary", index=False)
        runner.summary.to_excel(xw, sheet_name="Runner Summary", index=False, startrow=len(info) + 3)
        header_rows["Runner Summary"] = [(1, 2), (len(info) + 4, runner.summary.shape[1])]
        part_table.to_excel(xw, sheet_name="Part Planning", index=False)
        header_rows["Part Planning"] = [(1, part_table.shape[1])]
        if opt_res is not None:
            summ = pd.DataFrame(summary_rows(opt_res), columns=["Item", "Value"])
            summ.to_excel(xw, sheet_name="Truck Summary", index=False)
            header_rows["Truck Summary"] = [(1, 2)]
            comp = comparison_df(opt_res)
            comp.to_excel(xw, sheet_name="Truck Comparison", index=False)
            header_rows["Truck Comparison"] = [(1, comp.shape[1])]
            if opt_res.recommended:
                det = loading_details_df(opt_res.recommended, opt_res.pkgs)
                tw = truck_wise_df(opt_res.recommended)
                det.to_excel(xw, sheet_name="Loading Details", index=False, startrow=1)
                tw.to_excel(xw, sheet_name="Loading Details", index=False, startrow=len(det) + 5)
                header_rows["Loading Details"] = [(2, det.shape[1]), (len(det) + 6, tw.shape[1])]
        runner.matrix.to_excel(xw, sheet_name="Monthly Dispatch Matrix", index=False)
        header_rows["Monthly Dispatch Matrix"] = [(1, runner.matrix.shape[1])]
        wb = xw.book
        for name, hdrs in header_rows.items():
            for row, n in hdrs:
                pr._hdr(wb[name], row, n)
        ws = wb["Runner Summary"]
        ws.cell(len(info) + 3, 1, "Category summary").font = pr.Font(bold=True, color=pr.TEAL, size=12)
        for n in runner.notes:
            ws.cell(ws.max_row + 2, 1, n)
        if opt_res is not None:
            ws = wb["Truck Summary"]
            base = ws.max_row + 2
            ws.cell(base, 1, "Notes and unverified constraints").font = pr.Font(bold=True, color=pr.TEAL)
            for i, n in enumerate(opt_res.notes + opt_res.warnings, 1):
                ws.cell(base + i, 1, n)
            if "Loading Details" in wb.sheetnames:
                ld = wb["Loading Details"]
                ld.cell(1, 1, "Packaging-wise loading (recommended option)").font = pr.Font(bold=True, color=pr.TEAL, size=12)
                ld.cell(len(det) + 5, 1, "Truck-wise loading (recommended option)").font = pr.Font(bold=True, color=pr.TEAL, size=12)
        for w in wb.worksheets:
            for col in range(1, w.max_column + 1):
                letter = pr.get_column_letter(col)
                width = max((len(str(c.value)) for c in w[letter][:60] if c.value is not None
                             and not (w.title in ("Truck Summary", "Runner Summary") and col == 1 and c.row > 12)),
                            default=8)
                w.column_dimensions[letter].width = min(max(width + 2, 11), 60)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Sample workbook (all four inputs in one file)
# --------------------------------------------------------------------------- #

def build_planning_workbook_bytes() -> bytes:
    from sample_data import COLUMNS, SAMPLE_ROWS
    hist = pd.read_excel(BytesIO(pr.build_sample_dispatch_bytes()))
    master = pd.DataFrame([
        ["FR-1001", "Brake bracket", "PK-A", 10], ["FR-1002", "Wheel hub", "PK-C", 1],
        ["HR-2001", "Clutch plate", "PK-B", 20], ["HR-2002", "Gear shaft", "PK-A", 5],
        ["MR-3001", "Oil seal", "PK-B", 50], ["MR-3002", "Gasket", "PK-B", 50],
        ["MR-3003", "Bushing", "PK-A", 20], ["SR-4001", "Spring", "PK-B", 100],
        ["SR-4002", "Clip", "PK-B", 200], ["ST-5001", "Special bracket", "PK-C", 5],
        ["ST-5002", "Cover plate", "PK-A", 10], ["NM-6001", "Obsolete washer", "PK-B", 100],
        ["NM-6002", "Old gasket set (never dispatched)", "PK-A", 10],
        ["NM-6003", "Legacy clip (never dispatched)", "PK-B", 100],
    ], columns=["Part Number", "Part Description", "Packaging ID", "Qty per Packaging"])
    pack = pd.DataFrame(SAMPLE_ROWS, columns=COLUMNS).drop(columns=["Quantity"])
    ship = pd.DataFrame([["FR-1001", 600], ["HR-2002", 200], ["HR-2001", 1000], ["SR-4001", 2500],
                         ["FR-1002", 75], ["NM-6001", 200]], columns=["Part Number", "Shipment Quantity"])
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl", datetime_format="DD-MM-YYYY") as xw:
        for name, d in (("Dispatch History", hist), ("Part Master", master), ("Packaging Master", pack),
                        ("Current Shipment", ship)):
            d.to_excel(xw, sheet_name=name, index=False)
            ws = xw.book[name]
            pr._hdr(ws, 1, d.shape[1])
            for i in range(1, d.shape[1] + 1):
                L = pr.get_column_letter(i)
                ws.column_dimensions[L].width = min(max(len(str(d.columns[i - 1])) + 3,
                                                        max(len(str(v)) for v in d.iloc[:, i - 1]) + 2), 40)
    return buf.getvalue()


if __name__ == "__main__":
    with open("sample_planning_workbook.xlsx", "wb") as f:
        f.write(build_planning_workbook_bytes())
    print("Wrote sample_planning_workbook.xlsx")
