"""Build the two files the Pi needs for zero-shot labelling. Run this on a laptop, not the robot.

    pip install open_clip_torch torch onnx numpy
    python pi/tools/make_clip_labels.py
    scp pi/models/clip_image.onnx pi/models/labels.npz pi@scout.local:~/scout/pi/models/

It exports CLIP's image encoder to ONNX, and runs CLIP's text encoder once over every label in
data/rules.json to produce a table of label vectors. The robot then only ever runs the image half
(see pi/scout/clip.py), which is why it needs neither torch nor a tokenizer.

Labels are wrapped in prompts before encoding -- "a photo of a ramp" scores far better than "ramp"
on its own, which is how CLIP was trained. Edit PROMPTS if the labels start missing.
"""
import json
import os
import sys

MODEL = "ViT-B-32"
PRETRAINED = "laion2b_s34b_b79k"
PROMPTS = (
    "a photo of {}",
    "a photo of a {} in a building corridor",
    "a close-up photo of a {}",
)
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT = os.path.join(REPO, "pi", "models")


def main():
    import numpy as np
    import open_clip
    import torch

    with open(os.path.join(REPO, "data", "rules.json")) as f:
        labels = json.load(f).get("labels") or []
    if not labels:
        sys.exit("data/rules.json has no 'labels' list")
    os.makedirs(OUT, exist_ok=True)

    model, _, _ = open_clip.create_model_and_transforms(MODEL, pretrained=PRETRAINED)
    model.eval()
    tokenizer = open_clip.get_tokenizer(MODEL)

    # text side: average the prompt variants per label, then normalise
    with torch.no_grad():
        vectors = []
        for label in labels:
            toks = tokenizer([p.format(label) for p in PROMPTS])
            v = model.encode_text(toks).float()
            v /= v.norm(dim=-1, keepdim=True)
            v = v.mean(dim=0)
            vectors.append((v / v.norm()).numpy())
    npz = os.path.join(OUT, "labels.npz")
    np.savez(npz, names=np.array(labels), vectors=np.stack(vectors).astype("float32"))
    print(f"wrote {npz}: {len(labels)} labels, dim {vectors[0].shape[0]}")

    # image side: export to ONNX
    class ImageOnly(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, x):
            return self.m.encode_image(x)

    onnx_path = os.path.join(OUT, "clip_image.onnx")
    torch.onnx.export(
        ImageOnly(model), torch.zeros(1, 3, 224, 224), onnx_path,
        input_names=["image"], output_names=["features"],
        dynamic_axes={"image": {0: "batch"}, "features": {0: "batch"}},
        opset_version=17,
    )
    mb = os.path.getsize(onnx_path) / 1e6
    print(f"wrote {onnx_path}: {mb:.0f} MB")
    if mb > 400:
        print("that is large for a 2 GB Pi; consider a smaller CLIP variant")


if __name__ == "__main__":
    main()
