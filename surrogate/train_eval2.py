"""Pilot: train a small U-Net surrogate on the LISFLOOD-FP samples and compare it, on terrain areas it never saw,
against the current SAILAB Instant estimate (HAND channel flood + depression ponding). Writes pilot_report.json/.md."""
import glob, json, os, sys, time
import numpy as np, torch, torch.nn as nn, torch.nn.functional as F

HERE = os.path.dirname(os.path.abspath(__file__))
WET = 0.05
EPOCHS = int(sys.argv[1]) if len(sys.argv) > 1 else 160


def feats(z):
    dem = z["dem"].astype("float32"); filled = z["filled"].astype("float32"); cs = float(z["cell"])
    gy, gx = np.gradient(dem, cs); slope = np.clip(np.hypot(gx, gy), 0, 2) / 2
    m = dem.mean()
    rain, dur = float(z["rain"]), float(z["dur"])
    ch = [np.clip((dem - m) / 50, -3, 3), np.clip((filled - m) / 50, -3, 3), slope,
          np.clip(np.nan_to_num(z["hand"].astype("float32"), nan=20.0, posinf=20.0), 0, 20) / 20, np.log1p(z["acc"].astype("float32")) / 10,
          np.full_like(dem, np.log1p(rain * dur) / 6), np.full_like(dem, np.log(rain) / 5), np.full_like(dem, dur / 6),
          np.full_like(dem, np.log(float(z["friction"]) / 0.05) / 1.5), np.full_like(dem, float(z["infil"]) / 20)]
    return np.stack(ch).astype("float32")


def target(d):  # log-compressed depth so shallow ponds and deep channels both matter
    return np.log1p(np.maximum(d, 0) / WET).astype("float32")


def block(cin, cout):
    return nn.Sequential(nn.Conv2d(cin, cout, 3, padding=1), nn.GroupNorm(8, cout), nn.GELU(),
                         nn.Conv2d(cout, cout, 3, padding=1), nn.GroupNorm(8, cout), nn.GELU())


class UNet(nn.Module):
    def __init__(self, cin=10, w=32):
        super().__init__()
        self.e1, self.e2, self.e3 = block(cin, w), block(w, 2 * w), block(2 * w, 4 * w)
        self.mid = block(4 * w, 4 * w)
        self.d3, self.d2, self.d1 = block(8 * w, 2 * w), block(4 * w, w), block(2 * w, w)
        self.out = nn.Conv2d(w, 1, 1)

    def forward(self, x):
        e1 = self.e1(x); e2 = self.e2(F.max_pool2d(e1, 2)); e3 = self.e3(F.max_pool2d(e2, 2))
        m = self.mid(F.max_pool2d(e3, 2))
        d3 = self.d3(torch.cat([F.interpolate(m, scale_factor=2), e3], 1))
        d2 = self.d2(torch.cat([F.interpolate(d3, scale_factor=2), e2], 1))
        d1 = self.d1(torch.cat([F.interpolate(d2, scale_factor=2), e1], 1))
        return self.out(d1)[:, 0]


def get_device():
    for k in range(4):   # this machine's first CUDA call can fail transiently
        try:
            torch.zeros(1).cuda(); return torch.device("cuda")
        except Exception as e:
            print("cuda init retry", k, str(e)[:80], flush=True); time.sleep(3)
    raise RuntimeError("GPU unavailable")


def score(pred, truth):
    p, t = pred > WET, truth > WET
    tp = (p & t).sum(); fp = (p & ~t).sum(); fn = (~p & t).sum()
    csi = tp / max(tp + fp + fn, 1) if (tp + fp + fn) else np.nan
    return dict(csi=csi, pod=tp / max(tp + fn, 1) if (tp + fn) else np.nan, far=fp / max(tp + fp, 1) if (tp + fp) else np.nan,
                area_ratio=(p.sum() / t.sum()) if t.sum() else np.nan,
                mae=float(np.abs(np.where(p | t, pred, 0) - np.where(p | t, truth, 0)).sum() / max((p | t).sum(), 1)))


def main():
    files = sorted(glob.glob(os.path.join(HERE, "data2", "s*.npz")))
    Z = [dict(np.load(f)) for f in files]
    held = np.array([((int(z["block"][0]) * 7 + int(z["block"][1]) * 13) % 4 == 0) for z in Z])
    print(len(Z), "samples,", held.sum(), "held out (whole 58 km blocks the network never sees)")
    X = np.stack([feats(z) for z in Z]); Y = np.stack([target(z["truth"]) for z in Z])
    dev = get_device(); torch.manual_seed(0)
    tr = np.where(~held)[0]; te = np.where(held)[0]
    Xtr, Ytr = torch.tensor(X[tr]).to(dev), torch.tensor(Y[tr]).to(dev)
    net = UNet().to(dev); opt = torch.optim.AdamW(net.parameters(), 2e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, 2e-3, total_steps=EPOCHS * ((len(tr) + 7) // 8))
    wet_w = lambda y: 1 + 4 * (y > np.log1p(1.0)).float()
    t0 = time.time()
    for ep in range(EPOCHS):
        net.train(); perm = torch.randperm(len(tr), device=dev); tot = 0
        for i in range(0, len(tr), 8):
            idx = perm[i:i + 8]; xb, yb = Xtr[idx], Ytr[idx]
            k = int(torch.randint(0, 4, (1,))); xb, yb = torch.rot90(xb, k, (2, 3)), torch.rot90(yb, k, (1, 2))
            if torch.rand(1) < .5: xb, yb = xb.flip(3), yb.flip(2)
            loss = (F.smooth_l1_loss(net(xb), yb, reduction="none") * wet_w(yb)).mean()
            opt.zero_grad(); loss.backward(); opt.step(); sched.step(); tot += float(loss) * len(idx)
        if ep % 20 == 0 or ep == EPOCHS - 1:
            print(f"epoch {ep} train loss {tot/len(tr):.4f}  {time.time()-t0:.0f}s", flush=True)
    net.eval()
    with torch.no_grad():
        P = (torch.expm1(net(torch.tensor(X[te]).to(dev))).clamp(min=0) * WET).cpu().numpy()
    rows = []
    for j, i in enumerate(te):
        z = Z[i]; truth = z["truth"]
        rows.append(dict(sample=files[i].split("/")[-1], rain=float(z["rain"]), dur=float(z["dur"]), truth_wet_pct=float((truth > WET).mean() * 100),
                         surrogate=score(P[j], truth), instant=score(z["baseline"], truth)))
    def agg(k, m): 
        v = np.array([r[k][m] for r in rows], dtype=float); return float(np.nanmean(v)), float(np.nanmedian(v))
    rng = np.random.default_rng(0)
    diff = np.array([r["surrogate"]["csi"] - r["instant"]["csi"] for r in rows], dtype=float); diff = diff[~np.isnan(diff)]
    boots = [np.mean(rng.choice(diff, len(diff))) for _ in range(2000)]
    rep = dict(n_train=int(len(tr)), n_heldout=int(len(te)), epochs=EPOCHS,
               surrogate={m: agg("surrogate", m) for m in ("csi", "pod", "far", "area_ratio", "mae")},
               instant={m: agg("instant", m) for m in ("csi", "pod", "far", "area_ratio", "mae")},
               csi_gain_mean=float(diff.mean()), csi_gain_ci95=[float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
               share_surrogate_better=float((diff > 0).mean()), per_sample=rows)
    json.dump(rep, open(os.path.join(HERE, "pilot2_report.json"), "w"), indent=1, default=float)
    torch.save(net.state_dict(), os.path.join(HERE, "unet_pilot2.pt"))
    s, i = rep["surrogate"], rep["instant"]
    md = f"""# Surrogate pilot: U-Net vs current Instant estimate
Held-out: {rep['n_heldout']} terrain windows from areas the network never trained on ({rep['n_train']} used for training, {EPOCHS} epochs).
Truth = LISFLOOD-FP GPU run (roughness 0.02-0.15 and infiltration 0-20 mm/h vary per sample, no buildings; Instant ignores both). Wet = depth > {WET} m. Mean / median over held-out windows.

| metric | U-Net surrogate | Instant (HAND + ponding) |
|---|---|---|
| CSI (higher better) | {s['csi'][0]:.3f} / {s['csi'][1]:.3f} | {i['csi'][0]:.3f} / {i['csi'][1]:.3f} |
| POD (hit rate) | {s['pod'][0]:.3f} / {s['pod'][1]:.3f} | {i['pod'][0]:.3f} / {i['pod'][1]:.3f} |
| FAR (false alarms) | {s['far'][0]:.3f} / {s['far'][1]:.3f} | {i['far'][0]:.3f} / {i['far'][1]:.3f} |
| flooded-area ratio (1 = exact) | {s['area_ratio'][0]:.2f} / {s['area_ratio'][1]:.2f} | {i['area_ratio'][0]:.2f} / {i['area_ratio'][1]:.2f} |
| depth MAE where either is wet (m) | {s['mae'][0]:.3f} / {s['mae'][1]:.3f} | {i['mae'][0]:.3f} / {i['mae'][1]:.3f} |

CSI gain (surrogate minus Instant), mean {rep['csi_gain_mean']:+.3f}, 95% bootstrap interval [{rep['csi_gain_ci95'][0]:+.3f}, {rep['csi_gain_ci95'][1]:+.3f}]; surrogate better on {rep['share_surrogate_better']*100:.0f}% of windows.
"""
    open(os.path.join(HERE, "pilot2_report.md"), "w").write(md); print(md)


if __name__ == "__main__":
    main()
