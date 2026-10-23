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
                                 chars, every scheme timed in C
"""

import argparse
import pathlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ENTROPY_54 = 5.755  # log2(54)


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
    marks = {"c1": "o", "c2": "s", "c3": "^"}
    cols = {"S1-6bit": "#1f77b4", "S2-dom-bit": "#d62728", "S2-dom-table": "#ff7f0e",
            "S3-zstd": "#2ca02c", "S3d-dom": "#17becf", "S4-utf8": "#7f7f7f",
            "S5-hs8": "#bcbd22", "S5-hs11": "#8c6d31", "S6-unishox": "#9467bd"}
    label = {"S2-dom-bit": "S2-dom (bit-serial dec.)", "S2-dom-table": "S2-dom (table-driven dec.)"}
    payload = {"S2-dom-bit": "S2-dom", "S2-dom-table": "S2-dom"}
    fig, ax = plt.subplots(figsize=(7.6, 4.6))
    for c in ["c1", "c2", "c3"]:
        for s, col in cols.items():
            e = lmed[(lmed.corpus == c) & (lmed.length == 5000) & (lmed.scheme == s) & (lmed.op == "encode")]["us"].values
            dd = lmed[(lmed.corpus == c) & (lmed.length == 5000) & (lmed.scheme == s) & (lmed.op == "decode")]["us"].values
            b = bpc[(bpc.corpus == c) & (bpc.length == 5000) & (bpc.scheme == payload.get(s, s))]["bpc"].values
            if len(e) and len(dd) and len(b):
                ax.scatter(b[0], e[0] + dd[0], color=col, marker=marks[c], s=50, zorder=3)
    for s, col in cols.items():
        ax.scatter([], [], color=col, label=label.get(s, s))
    for c, m in marks.items():
        ax.scatter([], [], color="k", marker=m, label=f"corpus {c}", facecolors="none")
    ax.set_yscale("log")
    ax.set_xlabel("bits per character (payload cost)")
    ax.set_ylabel("encode+decode median latency, µs @5,000 chars")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=7, ncol=1, loc="center left", bbox_to_anchor=(1.01, 0.5), frameon=False)
    ax.set_title("Payload vs computation (all schemes timed in C)", fontsize=10)
    plt.tight_layout()
    plt.savefig(o / "fig_rq3_tradeoff.png", dpi=200)
    plt.savefig(o / "fig_rq3_tradeoff.pdf")
    print(f"figures written to {o}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
