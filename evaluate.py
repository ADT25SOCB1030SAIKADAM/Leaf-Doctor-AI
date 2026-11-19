"""Measure accuracy, precision, recall and F1 of the trained model.

Run it from the folder that holds model.keras and class_names.json.

 1. A separate validation folder (recommended):
        python evaluate.py --data ~/leaf-data/valid --all
 2. Your own photos, in folders named exactly like the classes, for example
        ~/leaf-test/Tomato___Early_blight/photo1.jpg
        python evaluate.py --data ~/leaf-test --all
 3. One folder split 80/20 the way the old train.py did:
        python evaluate.py --data ~/leaf-data/train

Output: a table on screen, metrics.txt, and confusion_matrix.png (if matplotlib is installed).
"""
import argparse
import json
import os
import sys

import numpy as np

CONF_MIN, MARGIN_MIN = 60.0, 15.0     # the same "not sure" rule the website uses


def metrics_from(y_true, y_pred, n):
    cm = np.zeros((n, n), dtype=int)
    for t, p in zip(y_true, y_pred):
        cm[t, p] += 1
    tp = np.diag(cm)
    fp = cm.sum(axis=0) - tp
    fn = cm.sum(axis=1) - tp
    support = cm.sum(axis=1)
    precision = tp / np.maximum(tp + fp, 1)
    recall = tp / np.maximum(tp + fn, 1)
    f1 = 2 * precision * recall / np.maximum(precision + recall, 1e-9)
    return cm, precision, recall, f1, support


def report_text(classes, cm, precision, recall, f1, support, unsure=None, y_true=None, y_pred=None):
    n = len(classes)
    present = [i for i in range(n) if support[i] > 0]
    lines = ["%-46s%10s%10s%10s%9s" % ("Class", "Precision", "Recall", "F1", "Images")]
    for i in present:
        lines.append("%-46s%10.3f%10.3f%10.3f%9d" % (classes[i], precision[i], recall[i], f1[i], support[i]))
    w = support[present] / support[present].sum()
    lines.append("-" * 85)
    lines.append("%-46s%10.3f%10.3f%10.3f%9d" % ("Macro average", precision[present].mean(), recall[present].mean(),
                                               f1[present].mean(), support.sum()))
    lines.append("%-46s%10.3f%10.3f%10.3f%9d" % ("Weighted average", (precision[present] * w).sum(),
                                               (recall[present] * w).sum(), (f1[present] * w).sum(), support.sum()))
    lines.append("\nOverall accuracy: %.3f  (%d of %d images correct)" % (np.trace(cm) / cm.sum(), np.trace(cm), cm.sum()))
    if unsure is not None and len(unsure):
        unsure = np.asarray(unsure, dtype=bool)
        right = np.asarray(y_true) == np.asarray(y_pred)
        lines.append("The website would answer 'not sure' for %.1f%% of these photos." % (100 * unsure.mean()))
        if (~unsure).any():
            lines.append("When it does answer, it is right %.1f%% of the time (%d photos)." % (100 * right[~unsure].mean(), (~unsure).sum()))
    off = cm.copy()
    np.fill_diagonal(off, 0)
    mistakes = []
    for flat in np.argsort(off, axis=None)[::-1][:5]:
        t, p = divmod(int(flat), n)
        if off[t, p] > 0:
            mistakes.append("  %s  ->  %s : %d photos" % (classes[t], classes[p], off[t, p]))
    if mistakes:
        lines.append("\nMost common mistakes (true class -> answer):")
        lines += mistakes
    return "\n".join(lines)


def main(a):
    folder = os.path.expanduser(a.data)
    if not os.path.isdir(folder):
        sys.exit("Folder not found: %s" % folder)
    with open("class_names.json") as f:
        classes = json.load(f)
    import tensorflow as tf
    model = tf.keras.models.load_model("model.keras")

    if a.all:       # use every photo in the folder, no split
        ds = tf.keras.utils.image_dataset_from_directory(folder, image_size=(224, 224), batch_size=32, shuffle=False)
    else:           # same 20 % split the old train.py used
        ds = tf.keras.utils.image_dataset_from_directory(folder, validation_split=0.2, subset="validation", seed=42,
                                                         image_size=(224, 224), batch_size=32)
    unknown = [c for c in ds.class_names if c not in classes]
    if unknown:
        sys.exit("These folder names are not classes of the model:\n  " + "\n  ".join(unknown) +
                 "\nUse the exact names from class_names.json (for example Tomato___Early_blight).")
    index = [classes.index(c) for c in ds.class_names]      # folder number -> model class number

    y_true, y_pred, unsure = [], [], []
    for x, y in ds:
        probs = model.predict(x, verbose=0)
        y_pred += probs.argmax(axis=1).tolist()
        y_true += [index[int(i)] for i in y.numpy()]
        order = np.sort(probs, axis=1)
        top1, top2 = order[:, -1] * 100, order[:, -2] * 100
        unsure += ((top1 < CONF_MIN) | ((top1 - top2) < MARGIN_MIN)).tolist()

    cm, precision, recall, f1, support = metrics_from(np.array(y_true), np.array(y_pred), len(classes))
    report = report_text(classes, cm, precision, recall, f1, support, unsure, y_true, y_pred)
    print(report)
    with open("metrics.txt", "w") as f:
        f.write(report + "\n")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        keep = [i for i in range(len(classes)) if support[i] > 0 or cm[:, i].sum() > 0]
        sub = cm[np.ix_(keep, keep)]
        size = max(6, len(keep) * 0.35)
        fig, ax = plt.subplots(figsize=(size, size))
        ax.imshow(sub, cmap="Greens")
        ax.set_xticks(range(len(keep)))
        ax.set_yticks(range(len(keep)))
        ax.set_xticklabels([classes[i] for i in keep], rotation=90, fontsize=7)
        ax.set_yticklabels([classes[i] for i in keep], fontsize=7)
        ax.set_xlabel("Predicted")
        ax.set_ylabel("Actual")
        ax.set_title("Confusion matrix")
        if len(keep) <= 12:
            for i in range(len(keep)):
                for j in range(len(keep)):
                    ax.text(j, i, sub[i, j], ha="center", va="center", fontsize=8)
        fig.tight_layout()
        fig.savefig("confusion_matrix.png", dpi=150)
        print("\nSaved metrics.txt and confusion_matrix.png")
    except ImportError:
        print("\nSaved metrics.txt. Run 'pip install matplotlib' to also get confusion_matrix.png")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--all", action="store_true", help="use every photo in the folder (validation folder or your own photos)")
    main(p.parse_args())
