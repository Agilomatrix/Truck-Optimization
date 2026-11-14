"""Tests for Part Runner Classification.  Run: python test_part_runner.py"""
import io

import pandas as pd

import part_runner as pr


def mk(rows):
    return pd.DataFrame(rows, columns=["Part Number", "Dispatch Date", "Dispatch Quantity"])


def months_rows(part, months, qty=10):
    return [[part, f"2026-{m:02d}-10", qty] for m in months]


END = pd.Period("2026-10", "M")


def run(df, master=None):
    clean, errs, _ = pr.validate_dispatch(df)
    assert not errs, errs
    return pr.classify(clean, END, master)


def test_boundaries():
    rows = []
    expected = {}
    for n in range(1, 10):                       # parts with 1..9 distinct months
        p = f"P{n}"
        rows += months_rows(p, range(2, 2 + n))
        expected[p] = pr.categorize(n)
    res = run(mk(rows))
    got = dict(zip(res.parts["Part Number"], res.parts["Runner Category"]))
    assert got == expected, got
    assert got["P9"] == "Fast Runner" and got["P8"] == got["P7"] == "High Runner"
    assert got["P6"] == got["P4"] == "Medium Runner" and got["P3"] == got["P2"] == "Slow Runner"
    assert got["P1"] == "Stranger / One-Time"
    print("boundaries ok:", got)


def test_months_not_quantity():
    rows = months_rows("BIG", [5], qty=1_000_000) + months_rows("SMALL", [2, 3, 4, 5, 6, 7, 8, 9, 10], qty=1)
    # several rows in same month for one part still counts as one month
    rows += [["MULTI", "2026-05-01", 5], ["MULTI", "2026-05-15", 5], ["MULTI", "2026-05-28", 5]]
    r = run(mk(rows)).parts.set_index("Part Number")
    assert r.loc["BIG", "Runner Category"] == "Stranger / One-Time"
    assert r.loc["SMALL", "Runner Category"] == "Fast Runner"
    assert r.loc["MULTI", "Dispatch Months"] == 1 and r.loc["MULTI", "Total Dispatch Quantity"] == 15
    print("distinct months (not quantity) ok")


def test_non_moving_and_master():
    rows = months_rows("A", [3, 4]) + [["ZERO", "2026-04-01", 0], ["OLD", "2025-01-10", 50]]
    master = pd.DataFrame({"Part Number": ["A", "NEVER"]})
    m, _ = pr.read_master(master)
    res = run(mk(rows), m)
    r = res.parts.set_index("Part Number")
    assert r.loc["ZERO", "Runner Category"] == "Non-Moving"
    assert r.loc["OLD", "Runner Category"] == "Non-Moving"      # only dispatched before the window
    assert r.loc["NEVER", "Runner Category"] == "Non-Moving"    # in master only
    assert res.master_added == 1 and res.rows_outside_window == 1
    print("non-moving ok:", sorted(r.index[r["Runner Category"] == "Non-Moving"]))


def test_percentages():
    rows = months_rows("A", range(2, 11), 100) + months_rows("B", [4], 100)
    res = run(mk(rows))
    assert abs(res.parts["Percentage Contribution (%)"].sum() - 100) < 0.01
    assert abs(res.summary["% of Parts"].sum() - 100) < 0.01
    assert abs(res.summary["% of Dispatch Quantity"].sum() - 100) < 0.05
    assert res.summary["Number of Parts"].sum() == res.unique_parts == 2
    print("percentages ok")


def test_part_normalisation_and_window():
    rows = [[1001.0, "2026-10-01", 5], ["1001", "2026-09-01", 5], [" ab-1 ", "2026-10-02", 1], ["AB-1", "2026-09-02", 1]]
    res = run(mk(rows))
    assert res.unique_parts == 2 and set(res.parts["Dispatch Months"]) == {2}
    clean, _, _ = pr.validate_dispatch(mk(months_rows("X", range(1, 11))))
    r = pr.classify(clean, END)              # Jan is outside Feb..Oct window
    assert r.parts.loc[0, "Dispatch Months"] == 9 and r.rows_outside_window == 1
    print("normalisation / window ok")


def test_validation_errors():
    bad = mk([["", "2026-03-01", 5], ["A", "not a date", 5], ["B", "2026-03-01", -4],
              ["C", "2026-03-01", "abc"], ["D", None, 3]])
    clean, errs, _ = pr.validate_dispatch(bad)
    assert clean is None and len(errs) == 5, errs
    _, errs, _ = pr.validate_dispatch(pd.DataFrame({"x": [1]}))
    assert "missing" in errs[0]["Issue"]
    print("validation ok:", len(errs) , "errors flagged")


def test_sample_and_report():
    df = pd.read_excel(io.BytesIO(pr.build_sample_dispatch_bytes()))
    clean, errs, _ = pr.validate_dispatch(df)
    assert not errs
    res = pr.classify(clean, END)
    cat = dict(zip(res.parts["Part Number"], res.parts["Runner Category"]))
    assert cat["FR-1001"] == cat["FR-1002"] == "Fast Runner"
    assert cat["HR-2001"] == cat["HR-2002"] == "High Runner"
    assert cat["MR-3001"] == cat["MR-3002"] == cat["MR-3003"] == "Medium Runner"
    assert cat["SR-4001"] == cat["SR-4002"] == "Slow Runner"
    assert cat["ST-5001"] == cat["ST-5002"] == "Stranger / One-Time"
    assert cat["NM-6001"] == "Non-Moving"
    x = pd.ExcelFile(io.BytesIO(pr.build_runner_report(res)))
    assert x.sheet_names == ["Runner Summary", "Part Classification", "Monthly Dispatch Matrix"]
    cols = x.parse("Part Classification").columns.tolist()
    for c in ("Part Number", "Dispatch Months", "Total Dispatch Quantity", "Runner Category", "Percentage Contribution (%)"):
        assert c in cols, cols
    print(res.summary.to_string(index=False))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("\nALL PART RUNNER TESTS PASSED")
