"""Truck Loading Optimization Tool – Streamlit app.   Run:  streamlit run app.py"""
from __future__ import annotations

import io

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from loading_engine import Optimizer, verify_load
from report import (build_excel_report, comparison_df, loading_details_df,
                    summary_rows, truck_wise_df)
from sample_data import build_template_bytes
from truck_config import SETTINGS, load_trucks
from part_runner_ui import render_part_runner
from validators import REQUIRED_COLUMNS, validate_dataframe

NAVY, TEAL, LIGHT = "#0B2545", "#13A89E", "#EEF4F8"
PALETTE = ["#13A89E", "#0B2545", "#F2A541", "#6C8EAD", "#C8553D", "#8E7DBE", "#3D9970", "#B5838D"]

st.set_page_config(page_title="Truck Loading Optimizer", page_icon="🚚", layout="wide")
st.markdown(f"""
<style>
.block-container {{padding-top: 1.4rem;}}
.hdr {{background:{NAVY}; color:white; padding:18px 24px; border-radius:10px; border-bottom:4px solid {TEAL}; margin-bottom:18px;}}
.hdr h1 {{margin:0; font-size:1.7rem; color:white;}} .hdr p {{margin:4px 0 0 0; color:#C9D6E2;}}
.card {{background:white; border:1px solid #D9E2EA; border-top:4px solid {TEAL}; border-radius:8px; padding:12px 14px; height:100%;}}
.card .l {{font-size:.78rem; color:#5B6B7A; text-transform:uppercase; letter-spacing:.04em;}}
.card .v {{font-size:1.45rem; font-weight:700; color:{NAVY}; line-height:1.25;}}
.card .s {{font-size:.78rem; color:#5B6B7A;}}
h2, h3 {{color:{NAVY};}}
</style>
<div class="hdr"><h1>🚚 Truck Loading Optimization Tool</h1>
<p>Truck loading &amp; truck recommendation · Part runner classification (Fast / High / Medium / Slow / Stranger / Non-Moving)</p></div>
""", unsafe_allow_html=True)


def card(label, value, sub=""):
    return f'<div class="card"><div class="l">{label}</div><div class="v">{value}</div><div class="s">{sub}</div></div>'


def floor_figure(load, pkgs):
    t = load.truck
    UL, UW = t.usable_length, t.usable_width
    colors = {p.pid: PALETTE[i % len(PALETTE)] for i, p in enumerate(pkgs)}
    fig = go.Figure()
    shapes = [dict(type="rect", x0=0, y0=0, x1=t.length_mm, y1=t.width_mm,
                   line=dict(color=NAVY, width=3), fillcolor="rgba(0,0,0,0)")]
    pts = {}
    for b in load.blocks:
        for k in range(b["n"]):
            y0 = b["y"] + k * b["across"]
            shapes.append(dict(type="rect", x0=b["x"], x1=b["x"] + b["depth"], y0=y0, y1=y0 + b["across"],
                               fillcolor=colors[b["pid"]], opacity=0.85, line=dict(color="white", width=1)))
            pts.setdefault(b["pid"], []).append(
                (b["x"] + b["depth"] / 2, y0 + b["across"] / 2,
                 f"{b['pid']} ({b['kind']})<br>{b['levels']} high · {b['levels'] * b['h']} mm"))
    for pid, lst in pts.items():
        fig.add_trace(go.Scatter(x=[p[0] for p in lst], y=[p[1] for p in lst], mode="markers", name=pid,
                                 marker=dict(color=colors[pid], size=9, symbol="square"),
                                 text=[p[2] for p in lst], hoverinfo="text"))
    fig.update_layout(shapes=shapes, height=330, margin=dict(l=10, r=10, t=30, b=10),
                      title="Top view (truck door at right) – each square is one stack",
                      xaxis=dict(title="Length (mm)", range=[-50, t.length_mm + 50]),
                      yaxis=dict(title="Width (mm)", range=[-50, t.width_mm + 50], scaleanchor="x", scaleratio=1),
                      plot_bgcolor="white", legend=dict(orientation="h", y=-0.25))
    return fig


def layer_figure(info):
    PL, PW, _ = info["pallet"]
    fig = go.Figure()
    shapes = [dict(type="rect", x0=0, y0=0, x1=PL, y1=PW, line=dict(color=NAVY, width=3))]
    for (x, y, w, h) in info["layer_rects"]:
        shapes.append(dict(type="rect", x0=x, y0=y, x1=x + w, y1=y + h, fillcolor=TEAL, opacity=0.8,
                           line=dict(color="white", width=1)))
    fig.update_layout(shapes=shapes, height=260, margin=dict(l=10, r=10, t=10, b=10),
                      xaxis=dict(range=[-30, PL + 30], visible=False),
                      yaxis=dict(range=[-30, PW + 30], visible=False, scaleanchor="x"), plot_bgcolor="white")
    return fig


def truck_loading_tab():
    # Upload / validate / calculate / results
    # --------------------------------------------------------------------------- #
    try:
        TRUCKS = load_trucks()
    except ValueError as e:
        st.error(f"Truck configuration error: {e}")
        return

    c1, c2 = st.columns([3, 1])
    with c1:
        up = st.file_uploader("Upload packaging data (Excel or CSV)", type=["xlsx", "xls", "csv"], key="truck_upload")
    with c2:
        st.write("")
        st.write("")
        st.download_button("⬇ Download sample template", build_template_bytes(),
                           file_name="sample_packaging_data.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    with st.expander("Truck database in use (edit truck_config.py to change – values are illustrative)"):
        st.dataframe(pd.DataFrame([{
            "Truck": t.name, "Internal L×W×H (mm)": f"{t.length_mm}×{t.width_mm}×{t.height_mm}",
            "Payload (kg)": t.payload_kg, "Cost (₹)": t.cost,
            "Door W×H (mm)": f"{t.door_width_mm}×{t.door_height_mm}",
            "Clearance L/W/H (mm)": f"{t.clearance_length_mm}/{t.clearance_width_mm}/{t.clearance_height_mm}",
            "Available": "unlimited" if t.available is None else t.available} for t in TRUCKS]), hide_index=True)

    if up is None:
        st.info("Upload a file to begin. Required columns: " + ", ".join(REQUIRED_COLUMNS)
                + ". Optional: Orientation Restriction (Fixed / Upright / Any).")
        return

    try:
        raw = up.getvalue()
        if up.name.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(raw))
        else:
            xl = pd.ExcelFile(io.BytesIO(raw))
            df = xl.parse("Packaging Data" if "Packaging Data" in xl.sheet_names else xl.sheet_names[0])
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not read the file: {e}")
        return

    pkgs, errors, warns = validate_dataframe(df, SETTINGS["gross_weight_tolerance_kg"])
    st.subheader("Uploaded data")
    st.dataframe(df, hide_index=True)
    if warns:
        with st.expander(f"⚠ {len(warns)} warning(s)", expanded=True):
            st.dataframe(pd.DataFrame(warns), hide_index=True)
    if errors:
        st.error(f"{len(errors)} data problem(s) found. Fix them in the file and upload again – no calculation was run.")
        st.dataframe(pd.DataFrame(errors), hide_index=True)
        return
    st.success(f"{len(pkgs)} packaging type(s) validated.")

    if st.button("🧮 Calculate loading plan", type="primary"):
        with st.spinner("Calculating loading plans for all truck options…"):
            try:
                st.session_state["result"] = Optimizer(pkgs, TRUCKS, SETTINGS).optimize()
                st.session_state["file"] = up.name
            except Exception as e:  # noqa: BLE001
                st.session_state.pop("result", None)
                st.error(f"Calculation failed: {e}")

    res = st.session_state.get("result")
    if res is None or st.session_state.get("file") != up.name:
        return

    # --------------------------------------------------------------------------- #
    # Results
    # --------------------------------------------------------------------------- #
    rec = res.recommended
    for w in res.warnings:
        st.warning(w)
    if rec is None:
        st.error("No truck option can carry this shipment. See the rejection reasons below.")
    else:
        pallets = sum(l.pallets for l in rec.loads)
        problems = [p for l in rec.loads for p in verify_load(l, pkgs)]
        if problems:
            st.error("Internal verification found problems in the recommended plan: " + "; ".join(problems))
        cols = st.columns(4)
        items = [("Total Packaging Quantity", f"{res.total_qty:,}", f"{len(pkgs)} packaging type(s)"),
                 ("Shipment Gross Weight", f"{res.total_cargo_weight:,.0f} kg", "boxes only"),
                 ("Total Pallets Required", f"{pallets}", rec.scenario),
                 ("Recommended Truck", rec.label, f"{rec.scenario}"),
                 ("Trucks Required", f"{rec.n_trucks}", ""),
                 ("Total Transportation Cost", f"₹{rec.cost:,.0f}", "lowest feasible option"),
                 ("Space Utilization", f"{rec.space_util:.1%}", "volume of cargo / truck volume"),
                 ("Weight Utilization", f"{rec.weight_util:.1%}", "load / payload")]
        for i, it in enumerate(items):
            with cols[i % 4]:
                st.markdown(card(*it), unsafe_allow_html=True)
            if i == 3:
                cols = st.columns(4)

        st.download_button("⬇ Download Excel report", build_excel_report(res), file_name="truck_loading_report.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")

    st.subheader("Truck comparison")
    comp = comparison_df(res)
    st.dataframe(comp, hide_index=True)
    feas = comp[comp["Feasibility"] == "Feasible"]
    if not feas.empty:
        feas = feas.assign(lbl=feas["Truck Type"] + " · " + feas["Loading Method"].str.replace(" Loading", "", regex=False))
        fig = go.Figure(go.Bar(x=feas["Total Cost (₹)"], y=feas["lbl"], orientation="h",
                               marker_color=[TEAL if r.startswith("✔") else NAVY for r in feas["Remarks"]],
                               text=[f"₹{v:,.0f}" for v in feas["Total Cost (₹)"]], textposition="outside"))
        fig.update_layout(height=max(260, 34 * len(feas) + 80), margin=dict(l=10, r=40, t=30, b=10),
                          title="Total transportation cost by option", yaxis=dict(autorange="reversed"),
                          plot_bgcolor="white")
        st.plotly_chart(fig)

    if rec:
        st.subheader("Loading details (recommended option)")
        st.dataframe(loading_details_df(rec, pkgs), hide_index=True)
        st.dataframe(truck_wise_df(rec), hide_index=True)

        st.subheader("Loading arrangement")
        names = [f"Truck {i} – {l.truck.name}" for i, l in enumerate(rec.loads, 1)]
        pick = st.selectbox("Select truck", names)
        load = rec.loads[names.index(pick)]
        st.plotly_chart(floor_figure(load, pkgs))
        pal_info = {}
        for l in rec.loads:
            for pid, inf in l.info.items():
                if inf.get("method") == "Pallet":
                    pal_info.setdefault(pid, inf)
        if pal_info:
            st.markdown("**Pallet layer pattern**")
            cs = st.columns(min(3, len(pal_info)))
            for i, (pid, inf) in enumerate(pal_info.items()):
                with cs[i % len(cs)]:
                    st.caption(f"{pid}: {inf['per_layer']} boxes/layer × {inf['layers']} layers = {inf['boxes_per_pallet']} per pallet")
                    st.plotly_chart(layer_figure(inf))

    with st.expander("Assumptions and constraints that could NOT be verified"):
        for n in res.notes:
            st.markdown(f"- {n}")


tab_truck, tab_runner = st.tabs(["🚚 Truck Loading", "📊 Part Runner Classification"])
with tab_truck:
    truck_loading_tab()
with tab_runner:
    render_part_runner()
