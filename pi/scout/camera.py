"""Naming what the lidar found: the Pi camera, plus zero-shot CLIP (PROTOCOL.md section 6).

The lidar says where a thing is and how big it is. It cannot say what it is, and in particular it
cannot tell a ramp from a wall, because a ramp meeting a horizontal scan plane looks exactly like a
wall. So when Scout stops in front of something it takes one still and scores it against the label
list in data/rules.json. Zero-shot: no training data, no fine-tuning, and changing the labels is a
data edit.

Two deliberate splits keep this cheap on a 2 GB Pi 4:

* Only the image half of CLIP runs here. The text half is run once on a laptop and its output
  shipped as a small table of label vectors, so the Pi needs no tokenizer and no text model.
  `pi/tools/make_clip_labels.py` builds both files.
* Classification only happens while Scout is stopped, a second or two at a time, so a slow model
  costs nothing on the driving path.

Everything degrades: no camera, no model, or no numpy and Scout still maps and still measures
widths, reporting every obstacle as "unknown" (rule 9).
"""
import io
import logging
import threading
import time
import uuid

log = logging.getLogger("scout.camera")

PHOTO_KEEP = 40          # stills held in memory for GET /photo/<id>; a run rarely finds more
SETTLE_S = 0.25          # let exposure settle after the wheels stop before grabbing the frame
MIN_CONFIDENCE = 0.28    # below this the label is not worth saying, so it becomes "unknown"
RAMP_LABELS = ("ramp", "wheelchair ramp", "slope", "incline")


class Camera:
    """Capture and classify. Thread-safe for one caller; the server calls it from its own loop."""

    def __init__(self, labels, model_dir=None):
        self.labels = list(labels) if labels else ["unknown"]
        self.connected = False
        self._cam = None
        self._clf = None
        self._photos = {}                 # id -> jpeg bytes
        self._order = []
        self._lock = threading.Lock()
        self._start(model_dir)

    def _start(self, model_dir):
        try:
            from picamera2 import Picamera2
        except Exception as e:
            log.error("CAMERA NOT AVAILABLE (%s): obstacles will be reported as 'unknown'", e)
            return
        try:
            self._cam = Picamera2()
            self._cam.configure(self._cam.create_still_configuration(main={"size": (640, 480)}))
            self._cam.start()
            self.connected = True
            log.info("CAMERA started (640x480)")
        except Exception as e:
            log.error("CAMERA FAILED TO START (%s): obstacles will be reported as 'unknown'", e)
            self._cam = None
            return
        try:
            from .clip import ClipClassifier
            self._clf = ClipClassifier(self.labels, model_dir)
            log.info("CLIP loaded: %d labels", len(self.labels))
        except Exception as e:
            log.error("CLIP NOT LOADED (%s): stills will be kept but not labelled. "
                      "Run pi/tools/make_clip_labels.py and copy its output to the Pi.", e)

    def close(self):
        if self._cam:
            try:
                self._cam.stop()
            except Exception:
                pass

    def look(self):
        """Take one still and name it. Returns (label, confidence, photo_id, is_ramp).

        Never raises and never blocks for long: a failure anywhere returns 'unknown', which the
        protocol allows and the dashboard shows plainly.
        """
        if not self._cam:
            return "unknown", 0.0, "", False
        try:
            time.sleep(SETTLE_S)
            buf = io.BytesIO()
            self._cam.capture_file(buf, format="jpeg")
            jpeg = buf.getvalue()
        except Exception as e:
            log.warning("capture failed: %s", e)
            return "unknown", 0.0, "", False

        pid = uuid.uuid4().hex[:8]
        with self._lock:
            self._photos[pid] = jpeg
            self._order.append(pid)
            while len(self._order) > PHOTO_KEEP:
                self._photos.pop(self._order.pop(0), None)

        if not self._clf:
            return "unknown", 0.0, pid, False
        try:
            label, conf = self._clf.classify(jpeg)
        except Exception as e:
            log.warning("classify failed: %s", e)
            return "unknown", 0.0, pid, False
        if conf < MIN_CONFIDENCE:
            return "unknown", round(conf, 2), pid, False
        return label, round(conf, 2), pid, label.lower() in RAMP_LABELS

    def photo(self, pid):
        with self._lock:
            return self._photos.get(pid)
