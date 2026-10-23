"""
make_figures.py — Regenerate the paper's figures from measurement CSVs.

Usage:
    python3 make_figures.py --datadir data --outdir figures

Inputs (in --datadir):
    results_pipeline.csv    payload sizes (bits/char is derived, deterministic)
    results_c_latency.csv   C codec latency (encode/decode, ns)

Outputs (in --outdir):
    fig_rq1_bpc.{png,pdf}        bits/char vs message length, per corpus
    fig_rq2_decoder.{png,pdf}    C-codec latency vs length: S1 and S2 encode,
                                 S2 decode with bit-serial vs table-driven decoder
    fig_rq3_tradeoff.{png,pdf}   payload vs computation trade-off, two panels:
                                 (a) C-timed codecs, (b) Python-timed codecs
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
              "S3d-dom": ("v-", "#17becf"), "S3d-en": ("v--", "#9edae5"),
              "S4-utf8": ("x-", "#7f7f7f"),
              "S5-hs8": ("D-", "#bcbd22"), "S5-hs11": ("D--", "#8c6d31")}
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
    fig.legend(handles, labels, fontsize=7, ncol=9, loc="lower center",
               bbox_to_anchor=(0.5, 0.0), frameon=False)
    plt.tight_layout(rect=(0, 0.07, 1, 1))
    plt.savefig(o / "fig_rq1_bpc.png", dpi=200)
    plt.savefig(o / "fig_rq1_bpc.pdf")

    # ---- Figure 2: C-codec latency vs length, both S2 decoders ----
    s2of = {"c1": "s2en", "c2": "s2c2", "c3": "s2c3"}
    series = [("s1", "encode", "S1 encode", "o--", "#1f77b4"),
              ("s1", "decode", "S1 decode", "o-", "#1f77b4"),
              ("s2", "encode", "S2-dom encode", "s--", "#d62728"),
              ("s2", "decode", "S2-dom decode (bit-serial)", "s-", "#d62728"),
              ("s2-lut", "decode", "S2-dom decode (table-driven)", "^-", "#ff7f0e")]
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.9), sharey=True)
    for ax, c in zip(axes, ["c1", "c2", "c3"]):
        for key, op, label, stl, col in series:
            sch = {"s1": "s1", "s2": s2of[c], "s2-lut": s2of[c] + "-lut"}[key]
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

    # ---- Figure 3: trade-off at 5,000 chars, two panels by timing source ----
    marks = {"c1": "o", "c2": "s", "c3": "^"}
    fig, (axa, axb) = plt.subplots(1, 2, figsize=(10.5, 4.2))

    # (a) C-timed codecs: S1, S2-dom with each decoder
    cols_a = {"S1-6bit": "#1f77b4", "S2-dom (bit-serial dec.)": "#d62728",
              "S2-dom (table-driven dec.)": "#ff7f0e"}
    pts = []
    for c in ["c1", "c2", "c3"]:
        s2 = s2of[c]
        cases = [("s1", "s1", "S1-6bit", "S1-6bit")] if c != "c2" else []
        cases += [(s2, s2, "S2-dom", "S2-dom (bit-serial dec.)"),
                  (s2, s2 + "-lut", "S2-dom", "S2-dom (table-driven dec.)")]
        for enc_sch, dec_sch, paper, label in cases:
            e = lmed[(lmed.corpus == c) & (lmed.length == 5000) & (lmed.scheme == enc_sch) & (lmed.op == "encode")]["us"].values
            d = lmed[(lmed.corpus == c) & (lmed.length == 5000) & (lmed.scheme == dec_sch) & (lmed.op == "decode")]["us"].values
            b = bpc[(bpc.corpus == c) & (bpc.length == 5000) & (bpc.scheme == paper)]["bpc"].values
            if len(e) and len(d) and len(b):
                pts.append((c, label, b[0], e[0] + d[0]))
    for c, s, b, t in pts:
        axa.scatter(b, t, color=cols_a[s], marker=marks[c], s=55, zorder=3)
    for s, col in cols_a.items():
        axa.scatter([], [], color=col, label=s)
    for c, m in marks.items():
        axa.scatter([], [], color="k", marker=m, label=f"corpus {c}", facecolors="none")
    axa.set_yscale("log")
    axa.set_xlabel("bits per character (payload cost)")
    axa.set_ylabel("encode+decode median latency, µs @5,000 chars")
    axa.grid(alpha=0.3)
    axa.legend(fontsize=7, loc="lower left")
    axa.set_title("(a) C codecs, clock_gettime", fontsize=10)

    # (b) Python-timed codecs: library calls via Python bindings
    cols_b = {"S3-zstd": "#2ca02c", "S3d-dom": "#17becf", "S4-utf8": "#7f7f7f",
              "S5-hs8": "#bcbd22", "S5-hs11": "#8c6d31"}
    pts = []
    for c in ["c1", "c2", "c3"]:
        for paper in cols_b:
            b = bpc[(bpc.corpus == c) & (bpc.length == 5000) & (bpc.scheme == paper)]["bpc"].values
            g = ok[(ok.corpus == c) & (ok.length == 5000) & (ok.scheme == paper)]
            if len(b) and len(g):
                t = (g["t_encode_ns"].median() + g["t_decode_ns"].median()) / 1e3
                pts.append((c, paper, b[0], t))
    for c, s, b, t in pts:
        axb.scatter(b, t, color=cols_b[s], marker=marks[c], s=55, zorder=3)
    for s, col in cols_b.items():
        axb.scatter([], [], color=col, label=s)
    for c, m in marks.items():
        axb.scatter([], [], color="k", marker=m, label=f"corpus {c}", facecolors="none")
    axb.set_yscale("log")
    axb.set_xlabel("bits per character (payload cost)")
    axb.set_ylabel("encode+decode median latency, µs @5,000 chars")
    axb.grid(alpha=0.3)
    axb.legend(fontsize=7, ncol=2, loc="lower left")
    axb.set_title("(b) library codecs via Python, perf_counter", fontsize=10)
    plt.tight_layout()
    plt.savefig(o / "fig_rq3_tradeoff.png", dpi=200)
    plt.savefig(o / "fig_rq3_tradeoff.pdf")
    print(f"figures written to {o}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
