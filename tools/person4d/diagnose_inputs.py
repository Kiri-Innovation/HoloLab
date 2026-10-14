"""CPU-only COLMAP reprojection and mutual-SIFT epipolar diagnostics, in input pixels."""

import importlib.util
import json
import struct
from pathlib import Path

import cv2
import numpy as np
from run_experiment import CFG, OUT

spec = importlib.util.spec_from_file_location(
    "colmap", "/cloud/cloud-ssd1/FastGS-Pano/scene/colmap_loader.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
root = Path(CFG["source_path"])
sparse = root / "sparse/0"
cams = m.read_intrinsics_binary(sparse / "cameras.bin")
ims = m.read_extrinsics_binary(sparse / "images.bin")
ims = sorted(ims.values(), key=lambda im: im.name)
points = {}
with (sparse / "points3D.bin").open("rb") as f:
    for _ in range(struct.unpack("<Q", f.read(8))[0]):
        v = struct.unpack("<QdddBBBd", f.read(43))
        n = struct.unpack("<Q", f.read(8))[0]
        f.read(n * 8)
        points[v[0]] = np.array(v[1:4])


def K(im):
    c = cams[im.camera_id]
    assert c.model == "PINHOLE", c.model
    fx, fy, cx, cy = c.params
    return np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]])


def summary(x):
    x = np.asarray(x)
    return (
        dict(
            n=len(x),
            median=float(np.median(x)),
            p90=float(np.percentile(x, 90)),
            mean=float(np.mean(x)),
            over2px=float(np.mean(x > 2)),
        )
        if len(x)
        else dict(n=0)
    )


res = []
regions = {"head": [], "lower": []}
perview = []
for im in ims:
    valid = [(j, p) for j, p in enumerate(im.point3D_ids) if p in points]
    if not valid:
        perview.append(dict(view=im.name, n=0))
        continue
    xyz = np.array([points[p] for _, p in valid])
    xy = np.array([im.xys[j] for j, _ in valid])
    z = xyz @ im.qvec2rotmat().T + im.tvec
    proj = z @ K(im).T
    pred = proj[:, :2] / proj[:, 2:]
    err = np.linalg.norm(pred - xy, axis=1)
    res.extend(err)
    mask = cv2.imread(str(Path(CFG["masks"]) / im.name), 0)
    ys = np.where(mask > 127)[0]
    cut = float(ys.min() + 0.55 * (ys.max() - ys.min()))
    for key, sel in [("head", xy[:, 1] < cut), ("lower", xy[:, 1] >= cut)]:
        regions[key].extend(err[sel])
    perview.append(dict(view=im.name, **summary(err)))


def sampson(F, a, b):
    a = np.c_[a, np.ones(len(a))]
    b = np.c_[b, np.ones(len(b))]
    fa = a @ F.T
    fb = b @ F
    return abs((b * fa).sum(1)) / np.sqrt(
        fa[:, :2].__pow__(2).sum(1) + fb[:, :2].__pow__(2).sum(1) + 1e-12
    )


sift = cv2.SIFT_create(nfeatures=6000)
cache = {}


def features(i):
    if i not in cache:
        im = cv2.imread(str(root / "images" / ims[i].name), 0)
        mask = cv2.imread(str(Path(CFG["masks"]) / ims[i].name), 0)
        cache[i] = sift.detectAndCompute(im, mask)
    return cache[i]


pairs = []
for i, j in [(0, 4), (15, 19), (30, 34), (45, 49), (60, 64), (75, 79), (88, 92), (0, 92)]:
    ka, da = features(i)
    kb, db = features(j)
    bf = cv2.BFMatcher()
    ab = bf.knnMatch(da, db, k=2)
    ba = bf.knnMatch(db, da, k=2)
    back = {m.queryIdx: m.trainIdx for m, n in ba if m.distance < 0.7 * n.distance}
    good = [
        m for m, n in ab if m.distance < 0.7 * n.distance and back.get(m.trainIdx) == m.queryIdx
    ]
    a = np.float64([ka[m.queryIdx].pt for m in good])
    b = np.float64([kb[m.trainIdx].pt for m in good])
    if len(a) < 8:
        continue
    A, B = ims[i], ims[j]
    R = B.qvec2rotmat() @ A.qvec2rotmat().T
    t = B.tvec - R @ A.tvec
    tx = np.array([[0, -t[2], t[1]], [t[2], 0, -t[0]], [-t[1], t[0], 0]])
    F = np.linalg.inv(K(B)).T @ tx @ R @ np.linalg.inv(K(A))
    fixed = sampson(F, a, b)
    best, inliers = cv2.findFundamentalMat(a, b, cv2.USAC_MAGSAC, 1.0, 0.999, 10000)
    free = sampson(best, a, b)
    if i == 0:
        left = cv2.resize(cv2.imread(str(root / "images" / A.name)), (640, 640))
        right = cv2.resize(cv2.imread(str(root / "images" / B.name)), (640, 640))
        canvas = np.full((690, 1280, 3), 25, dtype=np.uint8)
        canvas[50:, :640] = left
        canvas[50:, 640:] = right
        for u, v, error in zip(a, b, fixed, strict=True):
            color = (40, 40, 240) if error > 2 else (40, 190, 40)
            u = tuple((u * 640 / cams[A.camera_id].width).astype(int) + np.array([0, 50]))
            v = tuple((v * 640 / cams[B.camera_id].width).astype(int) + np.array([640, 50]))
            cv2.circle(canvas, u, 3, color, 1)
            cv2.circle(canvas, v, 3, color, 1)
        text = f"INPUT frames {i}/{j} - mutual SIFT: red >2px epipolar residual; green <=2px"
        cv2.putText(canvas, text, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1)
        cv2.imwrite(str(OUT / f"input_evidence_{i:03d}_{j:03d}.png"), canvas)
    pairs.append(
        dict(
            views=[A.name, B.name],
            matches=len(a),
            colmap_pose_sampson_px=summary(fixed),
            refit_fundamental_sampson_px=summary(free),
            refit_inlier_fraction=float(inliers.mean()),
        )
    )
    (OUT / "input_diagnostics.json").write_text(
        json.dumps(
            dict(
                reprojection_px=summary(res),
                regions={k: summary(v) for k, v in regions.items()},
                per_view=perview,
                pairs=pairs,
                caveat="Sparse retained tracks and mutual SIFT are selection-biased. Residuals conflate nonrigidity, pose/calibration error and matching error. Single camera-time coupling prevents causal identification.",
            ),
            indent=2,
        )
    )
print(
    json.dumps(
        dict(
            reprojection_px=summary(res),
            regions={k: summary(v) for k, v in regions.items()},
            pairs=pairs,
        ),
        indent=2,
    )
)
