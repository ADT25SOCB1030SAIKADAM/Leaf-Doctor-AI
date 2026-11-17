# Model card: Leaf Doctor plant disease classifier

## Summary
Image classifier that names the plant and the disease (or "healthy") from one leaf photo.
MobileNetV2 pre-trained on ImageNet, with a new classification layer and the top 30 layers fine-tuned.

## Intended use
Quick guidance for gardeners, students and small farmers. It is **not** a diagnosis and should not replace an agriculture expert for serious crop loss.

## Training data
PlantVillage (augmented version), 70,295 images, 80% training and 20% validation (seed 42).
The training copy used for version 1 had **36 class folders instead of 38**: two folders (Tomato late blight and Tomato healthy) were empty and one stray folder (`dataset_small`) was counted as a class. The app hides the stray class. Retraining on a clean 38-class copy is planned.

## Results (validation set, 14,059 images, version 1)
| Measure | Value |
|---|---|
| Accuracy | 0.942 |
| Loss | about 0.18 |
| Weighted precision / recall / F1 | 0.944 / 0.942 / 0.941 |
| Macro precision / recall / F1 | 0.900 / 0.891 / 0.894 (lowered by the two empty classes) |

Weakest classes (F1): Tomato early blight 0.692 (recall 0.552), Tomato target spot 0.787, Tomato spider mites 0.856, Tomato bacterial spot 0.879.
Strongest: Corn healthy 0.999, Grape healthy 0.994, Corn common rust 0.993.

## Limitations
- Trained on lab photos (one leaf, plain background). Real field photos, soil, hands and cluttered backgrounds will lower accuracy. This has **not** been measured yet.
- The augmented dataset contains edited copies of the same photos, so validation scores can look better than real-world performance.
- Knows 14 plants only. For anything else it still picks one of its classes, so the app says "not sure" when confidence is below 60% or the top two answers are within 15 points.
- Similar-looking tomato diseases are confused with each other more often than other classes.

## Ethics and privacy
Photos are not stored. Feedback ("Was this correct?") is stored without photos or personal data and is used only to measure and improve accuracy.

## Version history
| Version | Date | Notes |
|---|---|---|
| v1 | 2026-10 | MobileNetV2, 4 epochs (2 + 2), data issue described above |

## Next steps
1. Retrain on a clean 38-class dataset and record the new per-class table (`evaluate.py`).
2. Test on photos the model has never seen: your own phone photos and a real-world dataset such as PlantDoc. Report both numbers.
3. Review the advice texts with an agriculture expert.
4. Add a `model_info.json` next to the model (`{"version": "v2"}`) so the app reports the version.
