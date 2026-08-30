"""
make_figures.py — Regenerate the paper's figures from measurement CSVs.

Usage:
    python3 make_figures.py --datadir data --outdir figures

Inputs (in --datadir):
    results_pipeline.csv    payload sizes (bits/char is derived, deterministic)
    results_c_latency.csv   C codec latency (encode/decode, ns)

Outputs (in --outdir):
    fig_rq1_bpc.{png,pdf}        bits/char vs message length, per corpus
    fig_rq3_tradeoff.{png,pdf}   payload vs computation trade-off scatter
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
    lmed = lat.groupby(["corpus", "length", "scheme", "op"])["us"].median().reset_index()

    # ---- Figure 1: bits/char vs length ----
    styles = {"S1-6bit": ("o-", "#1f77b4"), "S2-dom": ("s-", "#d62728"),
              "S2-en": ("s--", "#ff9896"), "S3-zstd": ("^-", "#2ca02c"),
              "S4-utf8": ("x-", "#7f7f7f")}
    titles = {"c1": "C1: English text", "c2": "C2: IoT JSON",
              "c3": "C3: uniform 54-char"}
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.4), sharey=True)
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
    axes[2].legend(fontsize=7, loc="lower right")
    plt.tight_layout()
    plt.savefig(o / "fig_rq1_bpc.png", dpi=200)
    plt.savefig(o / "fig_rq1_bpc.pdf")

    # ---- Figure 2: trade-off scatter at 5,000 chars ----
    fig, ax = plt.subplots(figsize=(5.6, 4.2))
    cols = {"S1-6bit": "#1f77b4", "S2-dom": "#d62728",
            "S3-zstd": "#2ca02c", "S4-utf8": "#7f7f7f"}
    marks = {"c1": "o", "c2": "s", "c3": "^"}
    cmap = {("c1", "s1"): "S1-6bit", ("c3", "s1"): "S1-6bit",
            ("c1", "s2en"): "S2-dom", ("c2", "s2c2"): "S2-dom",
            ("c3", "s2c3"): "S2-dom"}
    pts = []
    for (c, sch), paper in cmap.items():
        e = lmed[(lmed.corpus == c) & (lmed.length == 5000) &
                 (lmed.scheme == sch) & (lmed.op == "encode")]["us"].values
        dec = lmed[(lmed.corpus == c) & (lmed.length == 5000) &
                   (lmed.scheme == sch) & (lmed.op == "decode")]["us"].values
        b = bpc[(bpc.corpus == c) & (bpc.length == 5000) &
                (bpc.scheme == paper)]["bpc"].values
        if len(e) and len(b):
            pts.append((c, paper, b[0], e[0] + dec[0]))
    for c in ["c1", "c2", "c3"]:
        for paper in ["S3-zstd", "S4-utf8"]:
            b = bpc[(bpc.corpus == c) & (bpc.length == 5000) &
                    (bpc.scheme == paper)]["bpc"].values
            g = ok[(ok.corpus == c) & (ok.length == 5000) & (ok.scheme == paper)]
            t = (g["t_encode_ns"].median() + g["t_decode_ns"].median()) / 1e3
            pts.append((c, paper, b[0], t))
    for c, s, b, t in pts:
        ax.scatter(b, t, color=cols[s], marker=marks[c], s=55, zorder=3)
    for s, col in cols.items():
        ax.scatter([], [], color=col, label=s)
    for c, m in marks.items():
        ax.scatter([], [], color="k", marker=m, label=f"corpus {c}",
                   facecolors="none")
    ax.set_yscale("log")
    ax.set_xlabel("bits per character (payload cost)")
    ax.set_ylabel("encode+decode median latency, µs @5,000 chars")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7, ncol=2, loc="lower left")
    ax.set_title("Payload vs computation trade-off", fontsize=10)
    plt.tight_layout()
    plt.savefig(o / "fig_rq3_tradeoff.png", dpi=200)
    plt.savefig(o / "fig_rq3_tradeoff.pdf")
    print(f"figures written to {o}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
