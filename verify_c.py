"""verify_c.py — Byte-exact C-vs-Python verification (both directions).
Run after any change to codec.c or the training corpora:
    python3 gen_s2_tables.py && gcc -O2 -o bench codec.c bench_main.c && python3 verify_c.py
Checks S1, S2 (bit-serial decoder) and S2 (table-driven decoder).
Passes only if every C output equals the Python reference byte-for-byte."""
import pathlib, subprocess, sys
import s1_sixbit
from s2_huffman import HuffmanTable

en = HuffmanTable.train(pathlib.Path("s2_train.txt").read_bytes())
dom = {"c1": ("s2en", en),
       "c2": ("s2c2", HuffmanTable.train(pathlib.Path("s2_train_c2.txt").read_bytes())),
       "c3": ("s2c3", HuffmanTable.train(pathlib.Path("s2_train_c3.txt").read_bytes()))}

def c_run(op, sch, inpath, outpath):
    r = subprocess.run(["./bench", op, sch, inpath, outpath], capture_output=True)
    assert r.returncode == 0, f"{op} {sch} {inpath}: {r.stderr.decode()}"
    return pathlib.Path(outpath).read_bytes()

fails = total = 0
for cdir in sorted(pathlib.Path("corpus").iterdir()):
    if not cdir.is_dir(): continue
    for fp in sorted(cdir.glob("c?_*.txt")):
        text = fp.read_text()
        if cdir.name != "c2":  # policy (c): S1 N/A on c2
            ok1 = s1_sixbit.encode(text, mode="drop") == c_run("enc","s1",str(fp),"/tmp/c.bin")
            ok2 = c_run("dec","s1","/tmp/c.bin","/tmp/c.txt").decode() == s1_sixbit.canonicalize(text,"drop")[0]
            total += 2; fails += (not ok1)+(not ok2)
            if not (ok1 and ok2): print("S1 FAIL", fp)
        sch, table = dom[cdir.name]
        py = table.encode(text)
        ok1 = py == c_run("enc",sch,str(fp),"/tmp/c.bin")
        ok2 = c_run("dec",sch,"/tmp/c.bin","/tmp/c.txt").decode() == text
        pathlib.Path("/tmp/py.bin").write_bytes(py)
        ok3 = c_run("dec",sch,"/tmp/py.bin","/tmp/c2.txt").decode() == text
        # table-driven decoder must reproduce the bit-serial decoder exactly
        ok4 = c_run("dec",sch+"-lut","/tmp/c.bin","/tmp/c3.txt").decode() == text
        ok5 = c_run("dec",sch+"-lut","/tmp/py.bin","/tmp/c4.txt").decode() == text
        total += 5; fails += (not ok1)+(not ok2)+(not ok3)+(not ok4)+(not ok5)
        if not (ok1 and ok2 and ok3 and ok4 and ok5): print("S2 FAIL", fp)
print(f"byte-exact checks: {total-fails}/{total} passed")
sys.exit(1 if fails else 0)
