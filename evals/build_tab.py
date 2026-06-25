"""Convert the Text Anonymization Benchmark (TAB, ECHR court cases) into our eval shape.

Writes (capped at 1000 docs):
  data_tab/docs/<doc_id>.txt        — the case text
  evals/ground_truth_tab.json       — per-doc must-mask spans (DIRECT + QUASI) with
                                       char offsets, entity_type, identifier_type

Source: NorskRegnesentral/text-anonymization-benchmark (MIT). Spans are unioned across
annotators and deduped by (start,end); NO_MASK mentions are dropped.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CAP = 1000

docs = []
for f in ["echr_train.json", "echr_dev.json", "echr_test.json"]:
    docs += json.load(open(os.path.join(ROOT, "tab_raw", f)))
docs = docs[:CAP]

outdir = os.path.join(ROOT, "data_tab", "docs")
os.makedirs(outdir, exist_ok=True)

gt = {}
for d in docs:
    text = d["text"]
    fname = f"{d['doc_id']}.txt"
    with open(os.path.join(outdir, fname), "w", encoding="utf-8") as fh:
        fh.write(text)
    spans, seen = [], set()
    for ann in d["annotations"].values():
        for m in ann["entity_mentions"]:
            if m["identifier_type"] == "NO_MASK":
                continue
            key = (m["start_offset"], m["end_offset"])
            if key in seen:
                continue
            seen.add(key)
            spans.append({"start": m["start_offset"], "end": m["end_offset"],
                          "text": m["span_text"], "entity_type": m["entity_type"],
                          "identifier_type": m["identifier_type"]})
    gt[fname] = sorted(spans, key=lambda s: s["start"])

with open(os.path.join(HERE, "ground_truth_tab.json"), "w") as fh:
    json.dump(gt, fh)

n_spans = sum(len(v) for v in gt.values())
n_direct = sum(1 for v in gt.values() for s in v if s["identifier_type"] == "DIRECT")
print(f"wrote {len(docs)} docs to data_tab/docs/")
print(f"ground truth: {n_spans} must-mask spans ({n_direct} DIRECT, {n_spans - n_direct} QUASI)")
