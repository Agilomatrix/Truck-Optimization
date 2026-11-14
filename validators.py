"""Input validation. Every problem is reported; nothing is silently ignored."""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

import pandas as pd

from models import Packaging

REQUIRED_COLUMNS = [
    "Packaging ID", "Packaging Type", "Length (mm)", "Width (mm)", "Height (mm)",
    "Net Weight (kg)", "Packaging Weight (kg)", "Gross Weight (kg)", "Quantity",
    "Stackable", "Max Stack Level", "Pallet Required", "Pallet Type",
    "Pallet Length (mm)", "Pallet Width (mm)", "Pallet Height (mm)", "Pallet Weight (kg)",
]
ORIENT_COL = "Orientation Restriction"   # optional column
OPTIONAL_COLUMNS = [ORIENT_COL]

_YN = {"yes": True, "y": True, "true": True, "1": True,
       "no": False, "n": False, "false": False, "0": False}


def _norm(s) -> str:
    return " ".join(str(s).strip().lower().split())


def _blank(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or str(v).strip() == ""


def _num(v):
    """Return (value, status) with status in ok | blank | bad."""
    if _blank(v):
        return None, "blank"
    try:
        x = float(str(v).replace(",", "").strip())
        if math.isnan(x) or math.isinf(x):
            return None, "bad"
        return x, "ok"
    except ValueError:
        return None, "bad"


def validate_dataframe(df: pd.DataFrame, gross_tol: float = 0.05, quantity_optional: bool = False
                       ) -> Tuple[List[Packaging], List[Dict], List[Dict]]:
    """Return (packagings, errors, warnings). If errors is non-empty, do not calculate."""
    errors: List[Dict] = []
    warnings: List[Dict] = []

    def err(row, pid, col, msg):
        errors.append({"Row": row, "Packaging ID": pid, "Column": col, "Issue": msg})

    def warn(row, pid, col, msg):
        warnings.append({"Row": row, "Packaging ID": pid, "Column": col, "Issue": msg})

    actual = {_norm(c): c for c in df.columns}
    required = [c for c in REQUIRED_COLUMNS if not (quantity_optional and c == "Quantity")]
    missing = [c for c in required if _norm(c) not in actual]
    if missing:
        err("-", "-", ", ".join(missing), "Required column(s) missing from the uploaded file.")
        return [], errors, warnings

    d = pd.DataFrame({c: df[actual[_norm(c)]] for c in REQUIRED_COLUMNS if _norm(c) in actual})
    if "Quantity" not in d:          # quantities come from the current part shipment instead
        d["Quantity"] = None
    has_orient = _norm(ORIENT_COL) in actual
    d[ORIENT_COL] = df[actual[_norm(ORIENT_COL)]] if has_orient else None
    d = d.dropna(how="all", subset=REQUIRED_COLUMNS)
    if not has_orient:
        warn("-", "-", ORIENT_COL,
             "Optional column not found: all packaging is assumed UPRIGHT (may be rotated 90° "
             "about the vertical axis, never tipped). Add the column with Fixed / Upright / Any to control this.")
    if d.empty:
        err("-", "-", "-", "The file contains no data rows.")
        return [], errors, warnings

    packs: List[Packaging] = []
    seen: Dict[str, int] = {}

    for idx, r in d.iterrows():
        row = int(idx) + 2   # Excel row number (header = row 1)
        n_err_before = len(errors)
        pid = "" if _blank(r["Packaging ID"]) else str(r["Packaging ID"]).strip()
        if not pid:
            err(row, "(blank)", "Packaging ID", "Packaging ID is missing.")
        elif pid in seen:
            err(row, pid, "Packaging ID", f"Duplicate Packaging ID (first seen in row {seen[pid]}).")
        else:
            seen[pid] = row
        label = pid or "(blank)"

        ptype = "" if _blank(r["Packaging Type"]) else str(r["Packaging Type"]).strip()

        dims = {}
        for col in ("Length (mm)", "Width (mm)", "Height (mm)"):
            v, st = _num(r[col])
            if st != "ok":
                err(row, label, col, "Value is missing." if st == "blank" else "Value is not a number.")
            elif v <= 0:
                err(row, label, col, "Dimension must be greater than zero.")
            else:
                dims[col] = v

        w = {}
        for col in ("Net Weight (kg)", "Packaging Weight (kg)", "Gross Weight (kg)"):
            v, st = _num(r[col])
            if st != "ok":
                err(row, label, col, "Value is missing." if st == "blank" else "Value is not a number.")
            elif v < 0 or (col == "Gross Weight (kg)" and v <= 0):
                err(row, label, col, "Weight must be positive (Net / Packaging weight may be 0).")
            else:
                w[col] = v
        if len(w) == 3:
            diff = abs(w["Gross Weight (kg)"] - (w["Net Weight (kg)"] + w["Packaging Weight (kg)"]))
            if diff > gross_tol:
                err(row, label, "Gross Weight (kg)",
                    f"Gross ({w['Gross Weight (kg)']}) ≠ Net + Packaging "
                    f"({w['Net Weight (kg)'] + w['Packaging Weight (kg)']:.3f}); difference {diff:.3f} kg.")

        q, st = _num(r["Quantity"])
        qty = 0
        if quantity_optional and st == "blank":
            qty = 1                  # placeholder – real quantity is derived from the part shipment
        elif st != "ok":
            err(row, label, "Quantity", "Value is missing." if st == "blank" else "Value is not a number.")
        elif q <= 0 or abs(q - round(q)) > 1e-9:
            err(row, label, "Quantity", "Quantity must be a positive whole number.")
        else:
            qty = int(round(q))

        s_raw = "" if _blank(r["Stackable"]) else _norm(r["Stackable"])
        if s_raw not in _YN:
            err(row, label, "Stackable", "Must be Yes or No.")
            stackable = False
        else:
            stackable = _YN[s_raw]
        m, st = _num(r["Max Stack Level"])
        max_stack = 1
        if stackable:
            if st != "ok" or m < 1 or abs(m - round(m)) > 1e-9:
                err(row, label, "Max Stack Level", "Stackable = Yes requires a whole number ≥ 1.")
            else:
                max_stack = int(round(m))
        elif st == "ok" and m > 1:
            warn(row, label, "Max Stack Level", "Stackable = No: Max Stack Level is ignored (1 level).")

        pr_raw = "" if _blank(r["Pallet Required"]) else _norm(r["Pallet Required"])
        pr = {"yes": "Yes", "y": "Yes", "no": "No", "n": "No", "optional": "Optional"}.get(pr_raw)
        if pr is None:
            err(row, label, "Pallet Required", "Must be Yes, No or Optional.")
            pr = "No"

        pal = {}
        pal_issue = []
        for col in ("Pallet Length (mm)", "Pallet Width (mm)", "Pallet Height (mm)", "Pallet Weight (kg)"):
            v, st = _num(r[col])
            if st == "ok" and ((v <= 0 and col != "Pallet Weight (kg)") or v < 0):
                pal_issue.append(f"{col}: must be positive")
            elif st != "ok":
                pal_issue.append(f"{col}: {'missing' if st == 'blank' else 'not a number'}")
            else:
                pal[col] = v
        has_pallet = not pal_issue
        if pr == "Yes" and not has_pallet:
            err(row, label, "Pallet fields", "Pallet Required = Yes but pallet data is incomplete/invalid → "
                + "; ".join(pal_issue))
        elif pr == "Optional" and not has_pallet:
            warn(row, label, "Pallet fields", "Pallet Required = Optional but pallet data is incomplete → "
                 "only DIRECT loading will be evaluated for this packaging.")

        o_raw = "" if _blank(r[ORIENT_COL]) else _norm(r[ORIENT_COL])
        orient = {"": "Upright", "upright": "Upright", "fixed": "Fixed", "this side up": "Fixed",
                  "any": "Any", "none": "Any", "free": "Any"}.get(o_raw)
        if orient is None:
            err(row, label, ORIENT_COL, "Must be Fixed, Upright or Any (blank = Upright).")
            orient = "Upright"

        if len(errors) == n_err_before:
            packs.append(Packaging(
                pid=pid, ptype=ptype,
                length=int(round(dims["Length (mm)"])), width=int(round(dims["Width (mm)"])),
                height=int(round(dims["Height (mm)"])),
                net=w["Net Weight (kg)"], pack=w["Packaging Weight (kg)"], gross=w["Gross Weight (kg)"],
                qty=qty, stackable=stackable, max_stack=max_stack, pallet_req=pr,
                pallet_type="" if _blank(r["Pallet Type"]) else str(r["Pallet Type"]).strip(),
                pallet_length=int(round(pal.get("Pallet Length (mm)", 0))),
                pallet_width=int(round(pal.get("Pallet Width (mm)", 0))),
                pallet_height=int(round(pal.get("Pallet Height (mm)", 0))),
                pallet_weight=float(pal.get("Pallet Weight (kg)", 0.0)),
                orient=orient, has_pallet=has_pallet,
            ))
    return packs, errors, warnings
