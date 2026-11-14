"""Result tables (used by the dashboard) and the Excel report."""
from __future__ import annotations

from io import BytesIO
from typing import List

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from loading_engine import OptimizationResult, Option

NAVY = "0B2545"
TEAL = "13A89E"


def comparison_df(res: OptimizationResult) -> pd.DataFrame:
    rec = res.recommended
    rows = []
    for o in res.options:
        if o.feasible:
            if o is rec:
                remark = "✔ Recommended – lowest total cost"
            else:
                remark = f"₹{o.cost - rec.cost:,.0f} more than recommended" if rec else ""
        else:
            remark = "Rejected: " + (o.reason or "cannot load shipment")
        rows.append({
            "Truck Type": o.label,
            "Loading Method": o.scenario,
            "Boxes Loaded": o.boxes if o.feasible else 0,
            "Trucks Required": o.n_trucks if o.feasible else None,
            "Space Utilization (%)": round(o.space_util * 100, 1) if o.feasible else None,
            "Weight Utilization (%)": round(o.weight_util * 100, 1) if o.feasible else None,
            "Total Cost (₹)": round(o.cost) if o.feasible else None,
            "Feasibility": "Feasible" if o.feasible else "Not feasible",
            "Remarks": remark,
        })
    df = pd.DataFrame(rows)
    df["_f"] = (df["Feasibility"] != "Feasible").astype(int)
    df["_c"] = df["Total Cost (₹)"].fillna(1e12)
    return df.sort_values(["_f", "_c"]).drop(columns=["_f", "_c"]).reset_index(drop=True)


def loading_details_df(opt: Option, pkgs) -> pd.DataFrame:
    rows = []
    for p in pkgs:
        method = opt.methods[p.pid]
        used = [l for l in opt.loads if l.boxes_by_pid.get(p.pid, 0) > 0]
        info = next((l.info[p.pid] for l in used if p.pid in l.info), {})
        pallets = sum(l.pallets_by_pid.get(p.pid, 0) for l in opt.loads)
        per_truck = [l.boxes_by_pid.get(p.pid, 0) for l in opt.loads]
        rows.append({
            "Packaging ID": p.pid,
            "Packaging Type": p.ptype,
            "Quantity": p.qty,
            "Loading Method": method,
            "Box Orientation": info.get("orientation", ""),
            "Loading Arrangement": info.get("arrangement", ""),
            "Boxes per Layer on Pallet": info.get("per_layer") if method == "Pallet" else None,
            "Layers per Pallet": info.get("layers") if method == "Pallet" else None,
            "Boxes per Pallet": info.get("boxes_per_pallet") if method == "Pallet" else None,
            "Total Pallets": pallets if method == "Pallet" else 0,
            "Trucks Carrying It": len(used),
            "Max Boxes in One Truck": max(per_truck) if per_truck else 0,
            "Boxes per Truck (list)": " | ".join(str(x) for x in per_truck),
            "Cargo Weight (kg)": round(p.qty * p.gross, 1),
            "Pallet Weight (kg)": round(pallets * p.pallet_weight, 1) if method == "Pallet" else 0,
            "Truck Types": ", ".join(sorted(set(l.truck.name for l in used))),
        })
    return pd.DataFrame(rows)


def truck_wise_df(opt: Option) -> pd.DataFrame:
    rows = []
    for i, l in enumerate(opt.loads, 1):
        rows.append({
            "Truck #": i,
            "Truck Type": l.truck.name,
            "Loaded Boxes (by packaging)": " | ".join(f"{k}: {v}" for k, v in l.boxes_by_pid.items()),
            "Pallets": l.pallets,
            "Gross Load (kg)": round(l.weight, 1),
            "Remaining Payload (kg)": round(l.remaining_payload, 1),
            "Remaining Volume (m³)": round(l.remaining_volume_m3, 2),
            "Space Utilization (%)": round(l.space_util * 100, 1),
            "Floor Utilization (%)": round(l.floor_util * 100, 1),
            "Weight Utilization (%)": round(l.weight_util * 100, 1),
            "Length Used (mm)": l.length_used,
        })
    return pd.DataFrame(rows)


def summary_rows(res: OptimizationResult) -> List[tuple]:
    r = res.recommended
    total_pallets = sum(sum(l.pallets_by_pid.values()) for l in r.loads) if r else 0
    return [
        ("Total packaging quantity", res.total_qty),
        ("Total shipment gross weight (kg, boxes only)", round(res.total_cargo_weight, 1)),
        ("Total pallets required", total_pallets),
        ("Recommended loading method", r.scenario if r else "No feasible option"),
        ("Recommended truck", r.label if r else "-"),
        ("Number of trucks", r.n_trucks if r else "-"),
        ("Total transportation cost (₹)", round(r.cost) if r else "-"),
        ("Space utilization (%)", round(r.space_util * 100, 1) if r else "-"),
        ("Weight utilization (%)", round(r.weight_util * 100, 1) if r else "-"),
    ]


def _style_header(ws, row, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.alignment = Alignment(wrap_text=True, vertical="center")


def build_excel_report(res: OptimizationResult) -> bytes:
    buf = BytesIO()
    comp = comparison_df(res)
    summ = pd.DataFrame(summary_rows(res), columns=["Item", "Value"])
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        summ.to_excel(xw, sheet_name="Shipment Summary", index=False)
        comp.to_excel(xw, sheet_name="Truck Comparison", index=False)
        n_det = 0
        if res.recommended:
            det = loading_details_df(res.recommended, res.pkgs)
            tw = truck_wise_df(res.recommended)
            n_det = len(det)
            det.to_excel(xw, sheet_name="Loading Details", index=False, startrow=1)
            tw.to_excel(xw, sheet_name="Loading Details", index=False, startrow=n_det + 5)
        wb = xw.book
        ws1, ws2 = wb["Shipment Summary"], wb["Truck Comparison"]
        _style_header(ws1, 1, 2)
        _style_header(ws2, 1, comp.shape[1])
        for r in range(2, ws1.max_row + 1):
            ws1.cell(r, 1).font = Font(bold=True, color=NAVY)
        # notes at the bottom of the summary sheet
        base = ws1.max_row + 2
        ws1.cell(base, 1, "Notes and unverified constraints").font = Font(bold=True, color=TEAL)
        for i, n in enumerate(res.notes + res.warnings, 1):
            ws1.cell(base + i, 1, n).alignment = Alignment(wrap_text=False)
        if res.recommended:
            ws3 = wb["Loading Details"]
            ws3.cell(1, 1, "Packaging-wise loading (recommended option)").font = Font(bold=True, color=TEAL, size=12)
            ws3.cell(n_det + 5, 1, "Truck-wise loading (recommended option)").font = Font(bold=True, color=TEAL, size=12)
            _style_header(ws3, 2, det.shape[1])
            _style_header(ws3, n_det + 6, tw.shape[1])
        for ws in wb.worksheets:
            for col in range(1, ws.max_column + 1):
                width = max((len(str(c.value)) for c in ws[get_column_letter(col)][:60]
                             if c.value is not None and c.row > 0 and not (ws.title == "Shipment Summary" and c.row > 11 and col == 1)),
                            default=8)
                ws.column_dimensions[get_column_letter(col)].width = min(max(width + 2, 10), 48)
    return buf.getvalue()
