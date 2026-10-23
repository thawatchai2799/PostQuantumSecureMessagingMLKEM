# Third-party code

| Directory    | Project   | Version / revision                          | License    | Upstream |
|--------------|-----------|---------------------------------------------|------------|----------|
| heatshrink/  | heatshrink | 0.4.1 (as bundled in heatshrink2 0.15.0)   | ISC        | https://github.com/atomicobject/heatshrink |
| unishox2/    | Unishox2  | tag 1.0.4, commit a5b950cb12712bf2d5a951c1bfe41b98b16d7d0e | Apache-2.0 | https://github.com/siara-cc/Unishox2 |
| (downloaded) | zstd      | 1.5.7 release tarball, SHA-256 eb33e51f49a15e023950cd7825ca74a4a2b43db8354825ac24fc1b7ee09e6fa3 | BSD-3-Clause / GPL-2.0 | https://github.com/facebook/zstd |

The files are copied unmodified. zstd is not vendored because of its size:
`run_all.py` downloads the pinned release tarball (or uses a local copy of
`zstd-1.5.7.tar.gz` placed next to `run_all.py`), checks the SHA-256 and
builds `libzstd.a`. The Python `zstandard` binding used by the pipeline
bundles the same libzstd version (1.5.7), so the C and Python encoders
produce identical frames; `verify_lib.py` checks this.
