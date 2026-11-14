"""Streamlit tab for Part Runner Classification (logic lives in part_runner.py)."""
from __future__ import annotations

import io

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import part_runner as pr

NAVY, TEAL = "#0B2545", "#13A89E"
CAT_COLORS = {
    "Fast Runner": "#13A89E", "High Runner": "#2E86AB", "Medium Runner": "#F2A541",
    "Slow Runner": "#E07A5F", "Stranger / One-Time": "#8E7DBE", "Non-Moving": "#8A99A8",
}


def _card(label, value, sub="", color=TEAL):
    return (f'<div class="card" style="border-top-color:{color}"><div class="l">{label}</div>'
            f'<div class="v">{value}</div><div class="s">{sub}</div></div>')


def _read(upload):
    raw = upload.getvalue()
    if upload.name.lower().endswith(".csv"):
        return pd.read_csv(io.BytesIO(raw))
    xl = pd.ExcelFile(io.BytesIO(raw))
    sheet = next((s for s in xl.sheet_names if s.lower() in ("dispatch history", "dispatch")), xl.sheet_names[0])
    return xl.parse(sheet)


def render_part_runner():
    st.markdown("Classify every part by the **number of distinct months** it was dispatched in the last "
                f"**{pr.PERIOD_MONTHS} months** (quantity does not affect the class).")
    c1, c2 = st.columns([3, 1])
    with c1:
        up = st.file_uploader("Upload dispatch history – last 9 months (Excel or CSV)",
                              type=["xlsx", "xls", "csv"], key="runner_upload")
    with c2:
        st.write("")
        st.write("")
        st.download_button("⬇ Download sample dispatch file", pr.build_sample_dispatch_bytes(),
                           file_name="sample_part_dispatch.xlsx", key="runner_sample",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    with st.expander("Rules, required columns and optional part master"):
        st.dataframe(pd.DataFrame([{"Runner Category": n, "Distinct dispatch months": r}
                                   for n, _, _, r in pr.CATEGORY_RULES]), hide_index=True)
        st.markdown("**Required columns:** `Part Number`, `Dispatch Date` (or `Dispatch Month`), `Dispatch Quantity` "
                    "(one row per dispatch). **Optional:** `Part Description`. Text dates such as 05/03/2026 are read "
                    "as DD/MM/YYYY; ISO dates (2026-03-05) are read as-is. Rows with quantity 0 are not dispatches. "
                    "**Non-Moving** = a known part with no dispatch in the period (a zero-quantity row, a part only "
                    "dispatched before the window, or a part listed in the optional master below).")
        master_up = st.file_uploader("Optional part master (Excel/CSV with a 'Part Number' column) – lists parts that "
                                     "may have had no dispatch at all", type=["xlsx", "xls", "csv"], key="runner_master")

    if up is None:
        st.info("Upload dispatch history to begin.")
        return
    try:
        df = _read(up)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not read the file: {e}")
        return

    clean, errors, warns = pr.validate_dispatch(df)
    if warns:
        with st.expander(f"⚠ {len(warns)} warning(s)"):
            st.dataframe(pd.DataFrame(warns), hide_index=True)
    if errors:
        st.error(f"{len(errors)} data problem(s) found. Fix them in the file and upload again – no classification was run.")
        st.dataframe(pd.DataFrame(errors), hide_index=True)
        return

    master = None
    if master_up is not None:
        try:
            master, msgs = pr.read_master(_read(master_up))
            for m in msgs:
                st.warning(m)
        except Exception as e:  # noqa: BLE001
            st.warning(f"Part master could not be read and was ignored: {e}")

    months = pr.available_months(clean)
    if not months:
        st.error("No valid dispatch dates found – cannot define a 9-month period.")
        return
    labels = [m.strftime("%b-%Y") for m in months]
    cc1, cc2 = st.columns([1, 3])
    with cc1:
        end_label = st.selectbox("Period ends in (last month)", labels[::-1], index=0, key="runner_end")
    end = months[labels.index(end_label)]
    window = pd.period_range(end=end, periods=pr.PERIOD_MONTHS, freq="M")
    with cc2:
        st.write("")
        st.caption(f"Analysis period: **{window[0].strftime('%b-%Y')} → {window[-1].strftime('%b-%Y')}** "
                   f"({pr.PERIOD_MONTHS} months). Data found for {clean['month'].dropna().nunique()} distinct month(s).")
    if len(months) < pr.PERIOD_MONTHS:
        st.warning(f"The file only covers {len(months)} month(s). Months with no data count as no dispatch, so parts "
                   "may be classified lower than their true runner category.")

    if st.button("📊 Classify parts", type="primary", key="runner_go"):
        st.session_state["runner_result"] = pr.classify(clean, end, master)
        st.session_state["runner_file"] = (up.name, end_label)
    res = st.session_state.get("runner_result")
    if res is None or st.session_state.get("runner_file") != (up.name, end_label):
        return

    # ------------------------------------------------------------------ cards
    s = res.summary.set_index("Runner Category")
    cols = st.columns(4)
    cols[0].markdown(_card("Unique Part Numbers", f"{res.unique_parts:,}", f"{res.start} → {res.end}", NAVY),
                     unsafe_allow_html=True)
    for i, name in enumerate(pr.CATEGORY_ORDER[:3]):
        cols[i + 1].markdown(_card(name, f"{int(s.loc[name, 'Number of Parts']):,}",
                                   f"{s.loc[name, '% of Parts']:.1f}% of parts · {s.loc[name, '% of Dispatch Quantity']:.1f}% of qty",
                                   CAT_COLORS[name]), unsafe_allow_html=True)
    cols = st.columns(4)
    for i, name in enumerate(pr.CATEGORY_ORDER[3:]):
        cols[i].markdown(_card(name, f"{int(s.loc[name, 'Number of Parts']):,}",
                               f"{s.loc[name, '% of Parts']:.1f}% of parts · {s.loc[name, '% of Dispatch Quantity']:.1f}% of qty",
                               CAT_COLORS[name]), unsafe_allow_html=True)
    cols[3].markdown(_card("Total Dispatch Quantity", f"{res.total_qty:,.0f}", "in the period", NAVY),
                     unsafe_allow_html=True)
    for n in res.notes:
        st.info(n)

    st.download_button("⬇ Download classification (Excel)", pr.build_runner_report(res),
                       file_name="part_runner_classification.xlsx", type="primary", key="runner_dl",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ----------------------------------------------------------------- charts
    cats = pr.CATEGORY_ORDER
    colors = [CAT_COLORS[c] for c in cats]
    g1, g2 = st.columns(2)
    with g1:
        fig = go.Figure(go.Pie(labels=cats, values=[s.loc[c, "Number of Parts"] for c in cats], hole=0.55,
                               marker=dict(colors=colors), sort=False, textinfo="label+percent"))
        fig.update_layout(title="Share of parts by category", height=340, margin=dict(l=10, r=10, t=50, b=10),
                          showlegend=False)
        st.plotly_chart(fig)
    with g2:
        fig = go.Figure()
        fig.add_bar(x=cats, y=[s.loc[c, "% of Parts"] for c in cats], name="% of parts", marker_color=NAVY)
        fig.add_bar(x=cats, y=[s.loc[c, "% of Dispatch Quantity"] for c in cats], name="% of dispatch quantity",
                    marker_color=TEAL)
        fig.update_layout(barmode="group", title="Parts vs dispatch-quantity contribution (%)", height=340,
                          margin=dict(l=10, r=10, t=50, b=10), plot_bgcolor="white", legend=dict(orientation="h", y=-0.25))
        st.plotly_chart(fig)
    dist = res.parts["Dispatch Months"].value_counts().reindex(range(pr.PERIOD_MONTHS + 1), fill_value=0)
    fig = go.Figure(go.Bar(x=[str(i) for i in dist.index], y=dist.values,
                           marker_color=[CAT_COLORS[pr.categorize(i)] for i in dist.index], text=dist.values,
                           textposition="outside"))
    fig.update_layout(title="Number of parts by distinct dispatch months", height=300,
                      xaxis_title="Distinct months with a dispatch", yaxis_title="Parts",
                      margin=dict(l=10, r=10, t=50, b=10), plot_bgcolor="white")
    st.plotly_chart(fig)

    # ----------------------------------------------------------------- tables
    st.subheader("Category summary")
    st.dataframe(res.summary, hide_index=True)
    st.subheader("Part classification")
    f1, f2 = st.columns([2, 1])
    with f1:
        pick = st.multiselect("Filter by category", cats, default=cats, key="runner_filter")
    with f2:
        q = st.text_input("Search part number", key="runner_search")
    view = res.parts[res.parts["Runner Category"].isin(pick)]
    if q.strip():
        view = view[view["Part Number"].str.contains(q.strip(), case=False, regex=False)]
    st.dataframe(view, hide_index=True)
    st.caption("Percentage Contribution = the part's share of the total dispatch quantity in the period.")
    with st.expander("Monthly dispatch quantity per part"):
        st.dataframe(res.matrix, hide_index=True)
