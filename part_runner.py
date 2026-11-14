"""
Part Runner Classification – pure logic (no Streamlit).

Rule: a part is classified by the number of DISTINCT MONTHS (in the 9-month analysis
window) in which it had a dispatch (net quantity > 0). Quantity size never changes the class.

    Fast Runner          9 months
    High Runner          7-8 months
    Medium Runner        4-6 months
    Slow Runner          2-3 months
    Stranger / One-Time  1 month
    Non-Moving           0 months (part known, but no dispatch in the window)

"Percentage Contribution":
  * per part      = part's share of the TOTAL DISPATCH QUANTITY in the window
  * per category  = share of PARTS (count) and share of DISPATCH QUANTITY
"""
from __future__ import annotations

import warnings as _warnings
from dataclasses import dataclass, field
from io import BytesIO
from typing import Dict, List, Optional, Tuple

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

PERIOD_MONTHS = 9

# (category, min months, max months, rule text) – editable
CATEGORY_RULES = [
    ("Fast Runner", PERIOD_MONTHS, PERIOD_MONTHS, f"All {PERIOD_MONTHS} months"),
    ("High Runner", 7, 8, "7–8 months"),
    ("Medium Runner", 4, 6, "4–6 months"),
    ("Slow Runner", 2, 3, "2–3 months"),
    ("Stranger / One-Time", 1, 1, "Only 1 month"),
    ("Non-Moving", 0, 0, "No dispatch in the period"),
]
CATEGORY_ORDER = [c[0] for c in CATEGORY_RULES]

_ALIASES = {
    "Part Number": ["part number", "part no", "part no.", "part_no", "partno", "part"],
    "Dispatch Date": ["dispatch date", "dispatch month", "date", "month", "dispatch dt"],
    "Dispatch Quantity": ["dispatch quantity", "dispatch qty", "quantity", "qty"],
    "Part Description": ["part description", "description", "part name"],
    "Packaging ID": ["packaging id", "packaging", "pack id", "packaging code"],
    "Qty per Packaging": ["qty per packaging", "quantity per packaging", "qty per pack", "parts per box",
                          "qty per box", "std pack qty", "std pack", "standard pack quantity"],
}
NAVY, TEAL = "0B2545", "13A89E"


def categorize(months: int) -> str:
    for name, lo, hi, _ in CATEGORY_RULES:
        if lo <= months <= hi:
            return name
    raise ValueError(f"No category for {months} months")


# --------------------------------------------------------------------------- #
# Input parsing / validation
# --------------------------------------------------------------------------- #

def _norm(s) -> str:
    return " ".join(str(s).strip().lower().split())


def _blank(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "" or str(v).lower() == "nan"


def clean_part(v) -> str:
    if _blank(v):
        return ""
    if isinstance(v, float) and v.is_integer():     # Excel numeric part numbers: 1001.0 -> 1001
        return str(int(v))
    return str(v).strip()


def _map_columns(df: pd.DataFrame) -> Dict[str, str]:
    actual = {_norm(c): c for c in df.columns}
    out = {}
    for std, aliases in _ALIASES.items():
        for a in aliases:
            if a in actual:
                out[std] = actual[a]
                break
    return out


def _to_dt(s: pd.Series) -> pd.Series:
    """Real dates and ISO strings (YYYY-MM-DD, YYYY-MM) are read as-is; any other text uses
    day-first (DD-MM-YYYY / DD/MM/YYYY). dayfirst must NOT be applied to ISO strings."""
    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore")
        if pd.api.types.is_datetime64_any_dtype(s):
            return s
        out = pd.to_datetime(s, errors="coerce", format="ISO8601")
        rest = out.isna() & ~s.map(_blank)
        if rest.any():
            try:
                out[rest] = pd.to_datetime(s[rest], errors="coerce", dayfirst=True, format="mixed")
            except (TypeError, ValueError):
                out[rest] = pd.to_datetime(s[rest], errors="coerce", dayfirst=True)
        return out


def validate_dispatch(df: pd.DataFrame) -> Tuple[Optional[pd.DataFrame], List[Dict], List[Dict]]:
    """Return (clean rows, errors, warnings). Clean columns: key, Part Number, Part Description, month, qty."""
    errors: List[Dict] = []
    warns: List[Dict] = []
    cols = _map_columns(df)
    missing = [k for k in ("Part Number", "Dispatch Date", "Dispatch Quantity") if k not in cols]
    if missing:
        errors.append({"Row": "-", "Part Number": "-", "Column": ", ".join(missing),
                       "Issue": "Required column(s) missing. Expected: Part Number, Dispatch Date "
                                "(or Dispatch Month), Dispatch Quantity."})
        return None, errors, warns

    d = pd.DataFrame({
        "raw_part": df[cols["Part Number"]],
        "raw_date": df[cols["Dispatch Date"]],
        "raw_qty": df[cols["Dispatch Quantity"]],
        "Part Description": df[cols["Part Description"]] if "Part Description" in cols else "",
    })
    d["row"] = df.index + 2
    d = d[~(d["raw_part"].map(_blank) & d["raw_date"].map(_blank) & d["raw_qty"].map(_blank))].copy()
    if d.empty:
        errors.append({"Row": "-", "Part Number": "-", "Column": "-", "Issue": "The file contains no data rows."})
        return None, errors, warns

    d["Part Number"] = d["raw_part"].map(clean_part)
    d["Part Description"] = d["Part Description"].map(lambda v: "" if _blank(v) else str(v).strip())
    qty_blank = d["raw_qty"].map(_blank)
    d["qty"] = pd.to_numeric(d["raw_qty"].astype(str).str.replace(",", "", regex=False), errors="coerce")
    d["date"] = _to_dt(d["raw_date"])
    date_blank = d["raw_date"].map(_blank)

    def add(mask, col, msg, target=errors):
        for _, r in d[mask].iterrows():
            target.append({"Row": int(r["row"]), "Part Number": r["Part Number"] or "(blank)",
                           "Column": col, "Issue": msg})

    add(d["Part Number"] == "", "Part Number", "Part Number is missing.")
    add(~qty_blank & d["qty"].isna(), "Dispatch Quantity", "Quantity is not a number.")
    add(d["qty"] < 0, "Dispatch Quantity", "Negative quantity (returns/reversals must be removed or netted first).")
    positive = d["qty"].fillna(0) > 0
    add(positive & date_blank, "Dispatch Date", "Dispatch Date is missing for a row with quantity > 0.")
    add(positive & ~date_blank & d["date"].isna(), "Dispatch Date",
        "Dispatch Date could not be read (use a real date, DD-MM-YYYY, or YYYY-MM).")
    # not errors – no dispatch implied
    add(qty_blank, "Dispatch Quantity", "Quantity blank – treated as 0 (no dispatch).", warns)
    add(d["qty"].fillna(0).eq(0) & ~qty_blank & d["qty"].notna(), "Dispatch Quantity",
        "Quantity is 0 – counted as no dispatch in that month.", warns)
    add((d["qty"].fillna(0) <= 0) & d["date"].isna(), "Dispatch Date",
        "No valid date on a zero-quantity row – part is kept as known, with no dispatch.", warns)
    if errors:
        return None, errors, warns

    d["qty"] = d["qty"].fillna(0.0)
    d["month"] = d["date"].dt.to_period("M")
    d["key"] = d["Part Number"].str.upper()
    return d[["key", "Part Number", "Part Description", "month", "qty"]].reset_index(drop=True), errors, warns


def read_master(df: pd.DataFrame) -> Tuple[Optional[pd.DataFrame], List[str]]:
    """Complete Part Master: every part that exists (so Non-Moving parts can be identified).
    Optional link columns: Packaging ID, Qty per Packaging (parts per pack)."""
    cols = _map_columns(df)
    if "Part Number" not in cols:
        return None, ["Part Master needs a 'Part Number' column – it was ignored."]
    n = len(df)
    m = pd.DataFrame({
        "Part Number": df[cols["Part Number"]].map(clean_part),
        "Part Description": df[cols["Part Description"]].map(lambda v: "" if _blank(v) else str(v).strip())
        if "Part Description" in cols else [""] * n,
        "Packaging ID": df[cols["Packaging ID"]].map(clean_part) if "Packaging ID" in cols else [""] * n,
        "Qty per Packaging": pd.to_numeric(df[cols["Qty per Packaging"]].astype(str).str.replace(",", "", regex=False),
                                           errors="coerce") if "Qty per Packaging" in cols else [float("nan")] * n,
    })
    msgs = []
    n_blank = int((m["Part Number"] == "").sum())
    if n_blank:
        msgs.append(f"{n_blank} blank Part Number row(s) in the Part Master were skipped.")
    m = m[m["Part Number"] != ""]
    m["key"] = m["Part Number"].str.upper()
    n_dup = int(m["key"].duplicated().sum())
    if n_dup:
        msgs.append(f"{n_dup} duplicate Part Number row(s) in the Part Master – the first occurrence is used.")
    return m.drop_duplicates("key").reset_index(drop=True), msgs


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

@dataclass
class RunnerResult:
    parts: pd.DataFrame
    summary: pd.DataFrame
    matrix: pd.DataFrame
    window: List[str]
    start: str
    end: str
    unique_parts: int
    total_qty: float
    rows_in_window: int
    rows_outside_window: int
    master_added: int
    notes: List[str] = field(default_factory=list)


def available_months(clean: pd.DataFrame) -> List[pd.Period]:
    m = clean["month"].dropna()
    if m.empty:
        return []
    return list(pd.period_range(m.min(), m.max(), freq="M"))


def classify(clean: pd.DataFrame, end_month: pd.Period, master: Optional[pd.DataFrame] = None) -> RunnerResult:
    window = pd.period_range(end=end_month, periods=PERIOD_MONTHS, freq="M")
    in_win = clean[clean["month"].isin(window)]
    rows_outside = int(clean["month"].notna().sum() - len(in_win))

    names = clean.drop_duplicates("key").set_index("key")[["Part Number", "Part Description"]]
    master_added = 0
    if master is not None and not master.empty:
        extra = master[~master["key"].isin(names.index)].set_index("key")[["Part Number", "Part Description"]]
        master_added = len(extra)
        names = pd.concat([names, extra])
        # fill missing descriptions from master
        md = master.set_index("key")["Part Description"]
        names["Part Description"] = [d or md.get(k, "") for k, d in zip(names.index, names["Part Description"])]

    monthly = (in_win.groupby(["key", "month"])["qty"].sum().unstack(fill_value=0.0)
               .reindex(columns=window, fill_value=0.0).reindex(names.index, fill_value=0.0))
    dist_months = (monthly > 0).sum(axis=1).astype(int)          # DISTINCT months with a dispatch
    total_qty = monthly.clip(lower=0).sum(axis=1)
    grand = float(total_qty.sum())

    parts = pd.DataFrame({
        "Part Number": names["Part Number"],
        "Part Description": names["Part Description"],
        "Dispatch Months": dist_months,
        "Total Dispatch Quantity": total_qty,
        "Runner Category": dist_months.map(categorize),
        "Percentage Contribution (%)": (total_qty / grand * 100) if grand > 0 else 0.0,
    })
    if (parts["Part Description"] == "").all():
        parts = parts.drop(columns=["Part Description"])
    parts["_c"] = parts["Runner Category"].map({c: i for i, c in enumerate(CATEGORY_ORDER)})
    parts = parts.sort_values(["_c", "Total Dispatch Quantity", "Part Number"], ascending=[True, False, True]) \
        .drop(columns="_c").reset_index(drop=True)
    parts["Percentage Contribution (%)"] = parts["Percentage Contribution (%)"].round(3)

    n = len(parts)
    rows = []
    for name, lo, hi, rule in CATEGORY_RULES:
        sub = parts[parts["Runner Category"] == name]
        q = float(sub["Total Dispatch Quantity"].sum())
        rows.append({"Runner Category": name, "Rule (distinct dispatch months)": rule,
                     "Number of Parts": len(sub),
                     "% of Parts": round(len(sub) / n * 100, 2) if n else 0.0,
                     "Total Dispatch Quantity": q,
                     "% of Dispatch Quantity": round(q / grand * 100, 2) if grand > 0 else 0.0})
    summary = pd.DataFrame(rows)

    labels = [p.strftime("%b-%Y") for p in window]
    matrix = monthly.copy()
    matrix.columns = labels
    matrix.insert(0, "Part Number", names["Part Number"])
    matrix = matrix.reset_index(drop=True).merge(parts[["Part Number", "Runner Category"]], on="Part Number") \
        .set_index("Part Number").loc[parts["Part Number"]].reset_index()

    notes = []
    if rows_outside:
        notes.append(f"{rows_outside} dispatch row(s) fall outside the selected 9-month window and were excluded.")
    if master_added:
        notes.append(f"{master_added} part(s) from the part master had no dispatch rows and are Non-Moving.")
    return RunnerResult(parts, summary, matrix, labels, labels[0], labels[-1], n, grand,
                        len(in_win), rows_outside, master_added, notes)


# --------------------------------------------------------------------------- #
# Excel report + sample
# --------------------------------------------------------------------------- #

def _hdr(ws, row, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.alignment = Alignment(wrap_text=True, vertical="center")


def build_runner_report(res: RunnerResult) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        info = pd.DataFrame([
            ("Analysis period", f"{res.start} to {res.end} ({PERIOD_MONTHS} months)"),
            ("Unique part numbers", res.unique_parts),
            ("Total dispatch quantity", res.total_qty),
            ("Classification basis", "Number of DISTINCT months with a dispatch (quantity not used)"),
            ("Percentage Contribution (part)", "Part's share of total dispatch quantity in the period"),
        ], columns=["Item", "Value"])
        info.to_excel(xw, sheet_name="Runner Summary", index=False)
        res.summary.to_excel(xw, sheet_name="Runner Summary", index=False, startrow=len(info) + 3)
        res.parts.to_excel(xw, sheet_name="Part Classification", index=False)
        res.matrix.to_excel(xw, sheet_name="Monthly Dispatch Matrix", index=False)
        wb = xw.book
        ws = wb["Runner Summary"]
        _hdr(ws, 1, 2)
        ws.cell(len(info) + 3, 1, "Category summary").font = Font(bold=True, color=TEAL, size=12)
        _hdr(ws, len(info) + 4, res.summary.shape[1])
        for n in res.notes:
            ws.cell(ws.max_row + 2 if n == res.notes[0] else ws.max_row + 1, 1, n)
        _hdr(wb["Part Classification"], 1, res.parts.shape[1])
        _hdr(wb["Monthly Dispatch Matrix"], 1, res.matrix.shape[1])
        for w in wb.worksheets:
            w.freeze_panes = "A2" if w.title != "Runner Summary" else None
            for col in range(1, w.max_column + 1):
                width = max((len(str(c.value)) for c in w[get_column_letter(col)][:80]
                             if c.value is not None and not (w.title == "Runner Summary" and c.row > len(info) + 8 and col == 1)),
                            default=8)
                w.column_dimensions[get_column_letter(col)].width = min(max(width + 2, 11), 46)
    return buf.getvalue()


def build_sample_dispatch_bytes() -> bytes:
    """Sample dispatch history, Feb-2026 … Oct-2026 (one part per runner category)."""
    months = list(range(2, 11))               # Feb..Oct 2026
    spec = {  # part -> (description, months dispatched, qty per dispatch)
        "FR-1001": ("Brake bracket", months, 500),
        "FR-1002": ("Wheel hub", months, 120),
        "HR-2001": ("Clutch plate", months[:8], 200),
        "HR-2002": ("Gear shaft", months[1:8], 80),
        "MR-3001": ("Oil seal", months[:6], 300),
        "MR-3002": ("Gasket", [2, 4, 6, 8, 10], 150),
        "MR-3003": ("Bushing", [3, 5, 7, 9], 60),
        "SR-4001": ("Spring", [4, 9, 10], 40),
        "SR-4002": ("Clip", [2, 7], 1000),
        "ST-5001": ("Special bracket (big one-time order)", [6], 25000),
        "ST-5002": ("Cover plate", [10], 10),
        "NM-6001": ("Obsolete washer", [], 0),
    }
    rows = []
    for part, (desc, ms, q) in spec.items():
        for m in ms:
            rows.append([part, desc, f"2026-{m:02d}-05", q])
            if part == "FR-1001":
                rows.append([part, desc, f"2026-{m:02d}-20", q // 2])   # 2 dispatches in a month = still 1 month
        if not ms:
            rows.append([part, desc, "2026-03-10", 0])                  # known part, zero dispatch
    rows.append(["HR-2002", "Gear shaft", "2026-01-15", 70])            # outside window -> excluded
    df = pd.DataFrame(rows, columns=["Part Number", "Part Description", "Dispatch Date", "Dispatch Quantity"])
    df["Dispatch Date"] = pd.to_datetime(df["Dispatch Date"])
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl", datetime_format="DD-MM-YYYY") as xw:
        df.to_excel(xw, sheet_name="Dispatch History", index=False)
        _hdr(xw.book["Dispatch History"], 1, 4)
        for i, w in enumerate((14, 38, 16, 18), 1):
            xw.book["Dispatch History"].column_dimensions[get_column_letter(i)].width = w
    return buf.getvalue()


if __name__ == "__main__":
    with open("sample_part_dispatch.xlsx", "wb") as f:
        f.write(build_sample_dispatch_bytes())
    print("Wrote sample_part_dispatch.xlsx")
