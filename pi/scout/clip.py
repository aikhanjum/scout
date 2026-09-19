"""The image half of CLIP, on the Pi, through onnxruntime.

The text half never runs here. `pi/tools/make_clip_labels.py` runs it once on a laptop and writes
`labels.npz`, a table of one unit vector per label. Classifying is then: encode the still, normalise
it, dot it against that table, softmax. No tokenizer, no text transformer, no torch on the robot.

Expects two files in the model directory (default pi/models/):
    clip_image.onnx   the image encoder
    labels.npz        'names' (the label strings) and 'vectors' (one row each, unit length)

Raises on any problem at construction, which camera.py catches and downgrades to "unknown".
"""
import logging
import os

log = logging.getLogger("scout.clip")

SIZE = 224
MEAN = (0.48145466, 0.4578275, 0.40821073)     # CLIP's own normalisation, not ImageNet's
STD = (0.26862954, 0.26130258, 0.27577711)
TEMPERATURE = 100.0                            # CLIP's trained logit scale


class ClipClassifier:
    def __init__(self, labels, model_dir=None):
        import numpy as np
        import onnxruntime as ort
        from PIL import Image
        self._np, self._Image = np, Image

        d = model_dir or os.path.join(os.path.dirname(__file__), "..", "models")
        onnx_path = os.path.join(d, "clip_image.onnx")
        npz_path = os.path.join(d, "labels.npz")
        for p in (onnx_path, npz_path):
            if not os.path.exists(p):
                raise FileNotFoundError(p)

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2          # leave the Pi's other cores for lidar and the server
        self._sess = ort.InferenceSession(onnx_path, opts, providers=["CPUExecutionProvider"])
        self._input = self._sess.get_inputs()[0].name

        table = np.load(npz_path, allow_pickle=True)
        names = [str(n) for n in table["names"]]
        vectors = table["vectors"].astype("float32")
        # keep only the labels rules.json actually asks for, in its order
        keep = [i for i, n in enumerate(names) if n in set(labels)]
        if not keep:
            raise ValueError("labels.npz has none of the labels in rules.json")
        self._names = [names[i] for i in keep]
        self._vectors = vectors[keep]
        self._vectors /= (np.linalg.norm(self._vectors, axis=1, keepdims=True) + 1e-9)

    def _preprocess(self, jpeg):
        np, Image = self._np, self._Image
        img = Image.open(__import__("io").BytesIO(jpeg)).convert("RGB")
        w, h = img.size
        s = SIZE / min(w, h)                                   # resize short side, then centre crop
        img = img.resize((max(SIZE, int(w * s + 0.5)), max(SIZE, int(h * s + 0.5))), Image.BILINEAR)
        w, h = img.size
        left, top = (w - SIZE) // 2, (h - SIZE) // 2
        img = img.crop((left, top, left + SIZE, top + SIZE))
        a = np.asarray(img, dtype="float32") / 255.0
        a = (a - np.array(MEAN, dtype="float32")) / np.array(STD, dtype="float32")
        return a.transpose(2, 0, 1)[None]                      # NCHW

    def classify(self, jpeg):
        """Returns (label, confidence 0..1)."""
        np = self._np
        feat = self._sess.run(None, {self._input: self._preprocess(jpeg)})[0][0].astype("float32")
        feat /= (np.linalg.norm(feat) + 1e-9)
        logits = TEMPERATURE * (self._vectors @ feat)
        e = np.exp(logits - logits.max())
        probs = e / e.sum()
        i = int(probs.argmax())
        return self._names[i], float(probs[i])
