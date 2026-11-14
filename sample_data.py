"""Sample / template Excel generation.  Run `python sample_data.py` to write
sample_packaging_data.xlsx (the app also offers the same file as a download)."""
from __future__ import annotations

from io import BytesIO

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill

from validators import REQUIRED_COLUMNS, ORIENT_COL

COLUMNS = REQUIRED_COLUMNS + [ORIENT_COL]

SAMPLE_ROWS = [
    ["PK-A", "Carton", 600, 400, 300, 8.0, 0.5, 8.5, 100, "Yes", 5, "Optional",
     "Wooden", 1200, 1000, 150, 25, "Upright"],
    ["PK-B", "Plastic Box", 400, 300, 250, 5.0, 1.2, 6.2, 50, "Yes", 4, "Yes",
     "Plastic", 1200, 800, 144, 15, "Upright"],
    ["PK-C", "Wooden Box", 800, 600, 500, 40.0, 8.0, 48.0, 75, "No", None, "No",
     None, None, None, None, None, "Fixed"],
]

INSTRUCTIONS = [
    ("Column", "Meaning / allowed values"),
    ("Packaging ID", "Unique ID (required)."),
    ("Packaging Type", "Carton, Plastic Box, Wooden Box, ..."),
    ("Length / Width / Height (mm)", "External packaging size, must be > 0."),
    ("Net / Packaging / Gross Weight (kg)", "Gross must equal Net + Packaging (tolerance 0.05 kg)."),
    ("Quantity", "Whole number > 0."),
    ("Stackable", "Yes / No."),
    ("Max Stack Level", "Whole number >= 1 (boxes high). Required when Stackable = Yes. "
                        "On a pallet it limits the number of layers."),
    ("Pallet Required", "Yes = always palletized, No = direct loading only, "
                        "Optional = both are calculated and compared."),
    ("Pallet Type / Length / Width / Height / Weight", "Required when Pallet Required = Yes (and for Optional "
                                                       "to evaluate pallets). Leave blank for No."),
    (ORIENT_COL + " (optional column)", "Fixed = no rotation; Upright = may rotate 90° about the vertical axis "
                                        "(default if blank); Any = box may also be laid on its side."),
]


def build_template_bytes() -> bytes:
    buf = BytesIO()
    df = pd.DataFrame(SAMPLE_ROWS, columns=COLUMNS)
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name="Packaging Data", index=False)
        pd.DataFrame(INSTRUCTIONS[1:], columns=INSTRUCTIONS[0]).to_excel(xw, sheet_name="Instructions", index=False)
        for ws in xw.book.worksheets:
            for c in ws[1]:
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = PatternFill("solid", fgColor="0B2545")
                c.alignment = Alignment(wrap_text=True, vertical="center")
            for i, col in enumerate(ws.columns, 1):
                w = max(len(str(c.value)) for c in col if c.value is not None)
                ws.column_dimensions[col[0].column_letter].width = min(max(w + 2, 12), 70)
    return buf.getvalue()


if __name__ == "__main__":
    with open("sample_packaging_data.xlsx", "wb") as f:
        f.write(build_template_bytes())
    print("Wrote sample_packaging_data.xlsx")
