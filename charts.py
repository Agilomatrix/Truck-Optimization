"""Plotly figures used by the dashboard."""
from __future__ import annotations

import plotly.graph_objects as go

import part_runner as pr

NAVY, TEAL = "#0B2545", "#13A89E"
PALETTE = ["#13A89E", "#0B2545", "#F2A541", "#6C8EAD", "#C8553D", "#8E7DBE", "#3D9970", "#B5838D"]
CAT_COLORS = {
    "Fast Runner": "#13A89E", "High Runner": "#2E86AB", "Medium Runner": "#F2A541",
    "Slow Runner": "#E07A5F", "Stranger / One-Time": "#8E7DBE", "Non-Moving": "#8A99A8",
}


def floor_figure(load, pkgs):
    t = load.truck
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
    shapes = [dict(type="rect", x0=0, y0=0, x1=PL, y1=PW, line=dict(color=NAVY, width=3))]
    for (x, y, w, h) in info["layer_rects"]:
        shapes.append(dict(type="rect", x0=x, y0=y, x1=x + w, y1=y + h, fillcolor=TEAL, opacity=0.8,
                           line=dict(color="white", width=1)))
    fig = go.Figure()
    fig.update_layout(shapes=shapes, height=260, margin=dict(l=10, r=10, t=10, b=10),
                      xaxis=dict(range=[-30, PL + 30], visible=False),
                      yaxis=dict(range=[-30, PW + 30], visible=False, scaleanchor="x"), plot_bgcolor="white")
    return fig


def runner_figures(res: pr.RunnerResult):
    s = res.summary.set_index("Runner Category")
    cats = pr.CATEGORY_ORDER
    pie = go.Figure(go.Pie(labels=cats, values=[s.loc[c, "Number of Parts"] for c in cats], hole=0.55,
                           marker=dict(colors=[CAT_COLORS[c] for c in cats]), sort=False, textinfo="label+percent"))
    pie.update_layout(title="Share of parts by category", height=340, margin=dict(l=10, r=10, t=50, b=10),
                      showlegend=False)
    grp = go.Figure()
    grp.add_bar(x=cats, y=[s.loc[c, "% of Parts"] for c in cats], name="% of parts", marker_color=NAVY)
    grp.add_bar(x=cats, y=[s.loc[c, "% of Dispatch Quantity"] for c in cats], name="% of dispatch quantity",
                marker_color=TEAL)
    grp.update_layout(barmode="group", title="Parts vs dispatch-quantity contribution (%)", height=340,
                      margin=dict(l=10, r=10, t=50, b=10), plot_bgcolor="white",
                      legend=dict(orientation="h", y=-0.25))
    dist = res.parts["Dispatch Months"].value_counts().reindex(range(pr.PERIOD_MONTHS + 1), fill_value=0)
    hist = go.Figure(go.Bar(x=[str(i) for i in dist.index], y=dist.values, text=dist.values, textposition="outside",
                            marker_color=[CAT_COLORS[pr.categorize(i)] for i in dist.index]))
    hist.update_layout(title="Number of parts by active dispatch months", height=300,
                       xaxis_title="Distinct months with a dispatch", yaxis_title="Parts",
                       margin=dict(l=10, r=10, t=50, b=10), plot_bgcolor="white")
    return pie, grp, hist
