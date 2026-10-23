"""
make_figures.py - Regenerate the paper's figures from measurement CSVs.

Usage:
    python3 make_figures.py --datadir data --outdir figures

Inputs (in --datadir):
    results_pipeline.csv    payload sizes (bits/char is derived, deterministic)
    results_c_latency.csv   latency of every scheme timed in C (encode/decode,
                            ns; 5 input slices per corpus and length)

Outputs (in --outdir):
    fig_rq1_bpc.{png,pdf}        bits/char vs message length, per corpus
    fig_rq2_decoder.{png,pdf}    C-codec latency vs length: S1 and S2 encode,
                                 S2 decode with bit-serial vs table-driven decoder
    fig_rq3_tradeoff.{png,pdf}   payload vs computation trade-off at 5,000
                                 chars, every scheme timed in C, one panel
                                 per primary corpus, every point labeled
"""

import argparse
import pathlib

import matplotlib
import matplotlib.transforms
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ENTROPY_54 = 5.755  # log2(54)


def place_labels(fig, ax, pts):
    """Label every point; try a fixed list of offsets (points) and keep the
    first whose text box overlaps no earlier label, no marker, and stays
    inside the axes."""
    fig.canvas.draw()
    rend = fig.canvas.get_renderer()
    axbox = ax.get_window_extent(rend)
    marker_boxes = []
    for x, y, _, _ in pts:
        px, py = ax.transData.transform((x, y))
        marker_boxes.append(matplotlib.transforms.Bbox.from_extents(px - 5, py - 5, px + 5, py + 5))
    taken = []
    offsets = [(7, -3), (-7, -3), (0, 8), (0, -13), (7, 6), (-7, 6), (7, -12), (-7, -12),
               (14, -3), (-14, -3), (0, 17), (0, -22), (18, 10), (-18, 10), (18, -16), (-18, -16)]
    for x, y, text, col in sorted(pts, key=lambda p: (p[0], p[1])):
        best = None
        for dx, dy in offsets:
            ha = "left" if dx > 0 else ("right" if dx < 0 else "center")
            t = ax.annotate(text, (x, y), xytext=(dx, dy), textcoords="offset points",
                            fontsize=9, color=col, ha=ha, va="bottom" if dy > 0 else "top" if dy < -5 else "center")
            bb = t.get_window_extent(rend).expanded(1.05, 1.15)
            inside = (bb.x0 >= axbox.x0 and bb.x1 <= axbox.x1 and bb.y0 >= axbox.y0 and bb.y1 <= axbox.y1)
            clash = any(bb.overlaps(o) for o in taken) or any(bb.overlaps(m) for m in marker_boxes)
            if inside and not clash:
                best = t
                break
            t.remove()
        if best is None:  # fall back to the first offset
            dx, dy = offsets[0]
            best = ax.annotate(text, (x, y), xytext=(dx, dy), textcoords="offset points",
                               fontsize=9, color=col, ha="left", va="center")
        taken.append(best.get_window_extent(rend).expanded(1.05, 1.15))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datadir", default="data")
    ap.add_argument("--outdir", default="figures")
    args = ap.parse_args()
    d = pathlib.Path(args.datadir)
    o = pathlib.Path(args.outdir)
    o.mkdir(exist_ok=True)

    pipe = pd.read_csv(d / "results_pipeline.csv")
    ok = pipe[pipe.status == "ok"].copy()
    ok["bpc"] = 8 * ok["encoded_bytes"] / ok["length"]
    bpc = ok.groupby(["corpus", "length", "scheme"])["bpc"].first().reset_index()

    lat = pd.read_csv(d / "results_c_latency.csv")
    lat["us"] = lat["ns"] / 1e3
    # cell value = median over slices of the per-slice median over repetitions
    per_slice = lat.groupby(["corpus", "length", "slice", "scheme", "op"])["us"].median().reset_index()
    lmed = per_slice.groupby(["corpus", "length", "scheme", "op"])["us"].median().reset_index()

    # ---- Figure 1: bits/char vs length ----
    styles = {"S1-6bit": ("o-", "#1f77b4"), "S2-dom": ("s-", "#d62728"),
              "S2-en": ("s--", "#ff9896"), "S3-zstd": ("^-", "#2ca02c"),
              "S3d-dom": ("v-", "#17becf"), "S3d-en": ("v--", "#9edae5"),
              "S4-utf8": ("x-", "#7f7f7f"),
              "S5-hs8": ("D-", "#bcbd22"), "S5-hs11": ("D--", "#8c6d31"),
              "S6-unishox": ("P-", "#9467bd")}
    titles = {"c1": "C1: English text", "c2": "C2: IoT JSON",
              "c3": "C3: uniform 54-char"}
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.9), sharey=True)
    for ax, c in zip(axes, ["c1", "c2", "c3"]):
        dd = bpc[bpc.corpus == c]
        for s, (st, col) in styles.items():
            g = dd[dd.scheme == s].sort_values("length")
            if len(g):
                ax.plot(g["length"], g["bpc"], st, color=col, label=s, ms=4, lw=1.3)
        ax.set_xscale("log")
        ax.set_title(titles[c], fontsize=10)
        ax.set_xlabel("message length (chars)")
        ax.grid(alpha=0.3)
        ax.axhline(ENTROPY_54, color="k", ls=":", lw=0.8)
    axes[0].set_ylabel("bits per character")
    axes[0].annotate(f"log2(54)={ENTROPY_54}", xy=(3200, ENTROPY_54 - 0.12),
                     fontsize=7, va="top")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, fontsize=7, ncol=10, loc="lower center",
               bbox_to_anchor=(0.5, 0.0), frameon=False)
    plt.tight_layout(rect=(0, 0.07, 1, 1))
    plt.savefig(o / "fig_rq1_bpc.png", dpi=200)
    plt.savefig(o / "fig_rq1_bpc.pdf")

    # ---- Figure 2: C-codec latency vs length, both S2 decoders ----
    series = [("S1-6bit", "encode", "S1 encode", "o--", "#1f77b4"),
              ("S1-6bit", "decode", "S1 decode", "o-", "#1f77b4"),
              ("S2-dom-table", "encode", "S2-dom encode", "s--", "#d62728"),
              ("S2-dom-bit", "decode", "S2-dom decode (bit-serial)", "s-", "#d62728"),
              ("S2-dom-table", "decode", "S2-dom decode (table-driven)", "^-", "#ff7f0e")]
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.9), sharey=True)
    for ax, c in zip(axes, ["c1", "c2", "c3"]):
        for sch, op, label, stl, col in series:
            g = lmed[(lmed.corpus == c) & (lmed.scheme == sch) & (lmed.op == op)].sort_values("length")
            if len(g):
                ax.plot(g["length"], g["us"], stl, color=col, label=label, ms=4, lw=1.3)
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_title(titles[c], fontsize=10)
        ax.set_xlabel("message length (chars)")
        ax.grid(alpha=0.3, which="both")
    axes[0].set_ylabel("median latency, µs (C, clock_gettime)")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, fontsize=7, ncol=5, loc="lower center",
               bbox_to_anchor=(0.5, 0.0), frameon=False)
    plt.tight_layout(rect=(0, 0.07, 1, 1))
    plt.savefig(o / "fig_rq2_decoder.png", dpi=200)
    plt.savefig(o / "fig_rq2_decoder.pdf")

    # ---- Figure 3: trade-off at 5,000 characters, every scheme timed in C ----
    # One panel per corpus; every point carries its own label, placed by a
    # small deterministic search so that labels overlap neither each other
    # nor the points.
    schemes = [("S1-6bit", "S1", "o", "#1f77b4"), ("S2-dom-bit", "S2 bit-serial", "s", "#d62728"),
               ("S2-dom-table", "S2 table", "s", "#ff7f0e"), ("S3-zstd", "S3", "^", "#2ca02c"),
               ("S3d-dom", "S3d", "v", "#17becf"), ("S4-utf8", "S4", "X", "#7f7f7f"),
               ("S5-hs8", "S5-hs8", "D", "#bcbd22"), ("S5-hs11", "S5-hs11", "D", "#8c6d31"),
               ("S6-unishox", "S6", "P", "#9467bd")]
    payload = {"S2-dom-bit": "S2-dom", "S2-dom-table": "S2-dom"}
    fig, axes = plt.subplots(1, 3, figsize=(11, 4.4), sharey=True)
    for ax, c in zip(axes, ["c1", "c2", "c3"]):
        ax.tick_params(labelsize=10)
        pts = []
        for s, short, m, col in schemes:
            e = lmed[(lmed.corpus == c) & (lmed.length == 5000) & (lmed.scheme == s) & (lmed.op == "encode")]["us"].values
            dd = lmed[(lmed.corpus == c) & (lmed.length == 5000) & (lmed.scheme == s) & (lmed.op == "decode")]["us"].values
            b = bpc[(bpc.corpus == c) & (bpc.length == 5000) & (bpc.scheme == payload.get(s, s))]["bpc"].values
            if len(e) and len(dd) and len(b):
                ax.scatter(b[0], e[0] + dd[0], color=col, marker=m, s=60, zorder=3,
                           edgecolors="k", linewidths=0.4)
                pts.append((b[0], e[0] + dd[0], short, col))
        ax.set_yscale("log")
        ax.set_xlim(0.5, 10.8)
        ax.set_ylim(0.03, 4e4)
        ax.set_title(titles[c], fontsize=12)
        ax.set_xlabel("bits per character (payload)", fontsize=11)
        ax.grid(alpha=0.3, which="major")
        place_labels(fig, ax, pts)
    axes[0].set_ylabel("encode + decode median latency, µs", fontsize=11)
    fig.suptitle("Payload vs computation at 5,000 characters (all schemes timed in C)", fontsize=12)
    plt.tight_layout()
    plt.savefig(o / "fig_rq3_tradeoff.png", dpi=200)
    plt.savefig(o / "fig_rq3_tradeoff.pdf")
    print(f"figures written to {o}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
