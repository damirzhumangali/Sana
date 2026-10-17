"""Fine-tune a local YOLO detector on reviewed cash frames (CPU by default)."""

import argparse
import json
import os
from pathlib import Path


def local_yolo():
    config = Path("data/ultralytics").resolve()
    config.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("YOLO_CONFIG_DIR", str(config))
    os.environ.setdefault("YOLO_OFFLINE", "true")
    from ultralytics import YOLO, settings

    settings.update({"sync": False, "wandb": False, "mlflow": False, "comet": False})
    return YOLO


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/cash/dataset/cash.yaml"))
    parser.add_argument("--base", type=Path, default=Path("yolo11n.pt"))
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--imgsz", type=int, default=768)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--name", default="baseline")
    args = parser.parse_args()
    if not args.base.is_file() or not args.data.is_file():
        parser.error("Local base weights and dataset YAML must exist")
    if args.epochs <= 0 or args.batch <= 0 or args.imgsz < 32:
        parser.error("epochs/batch must be positive; imgsz must be at least 32")
    if Path("data/cash/runs", args.name).exists():
        parser.error("Run already exists; choose another --name")
    model = local_yolo()(str(args.base))
    model.train(
        data=str(args.data.resolve()),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        workers=0,
        amp=False,
        freeze=10,
        optimizer="AdamW",
        lr0=0.001,
        nbs=args.batch,
        weight_decay=0.0005,
        project=str(Path("data/cash/runs").resolve()),
        name=args.name,
        exist_ok=False,
        seed=42,
        deterministic=True,
        patience=0,
        plots=False,
        mosaic=0.0,
        close_mosaic=0,
        scale=0.15,
        translate=0.1,
        fliplr=0.5,
        degrees=5.0,
        hsv_h=0.01,
        hsv_s=0.3,
        hsv_v=0.3,
        val=True,
    )
    best = Path(model.trainer.best)
    print(json.dumps({"best": str(best.resolve()), "validation": model.trainer.metrics}))


if __name__ == "__main__":
    main()
