# Plate OCR recognizer options

## Decision

Do not replace the current recognizer with a model trained on Chinese plates. The
best near-term candidate is **FastPlateOCR's global CCT-S v2 recognizer**, run
as a second local recognizer with ONNX Runtime and adopted only after it wins a
held-out set of actual Philippine gate-camera crops. It is a plate-specific,
global model, whereas the current `en_PP-OCRv5_rec_mobile` is a general English
text recognizer. The FastPlateOCR maintainers describe the global recognizers
as trained on plates from more than 65 countries, but do **not** claim a
Philippine-specific validation result; that is why a local comparison is a
required acceptance gate, not an accuracy promise.

The durable solution is to fine-tune a recognizer on consented, labelled
Philippine plate crops from the deployed cameras. This directly learns the
local fonts, plate formats, camera angle, blur, and compression that cause
errors such as `D` being read as `I`.

## Current integration constraints

`web/recognition.py` currently loads one ONNX model using OpenCV DNN and
decodes a CTC-style `[batch, time, character]` output using `en_dict.txt`. A
replacement must either preserve that input/output contract or have a small
recognizer adapter. OpenCV does provide `readNetFromONNX`, but ONNX is a model
container rather than a guarantee that every model operator will load in the
OpenCV DNN build in production. [OpenCV DNN API](https://docs.opencv.org/4.x/db/ddc/dnn_2dnn_8hpp.html)

## Options compared

| Option | Specialization and availability | License / deployment cost | Compatibility with Plate Program | Recommendation |
| --- | --- | --- | --- | --- |
| **Current PP-OCRv5 English mobile** | General English OCR; already bundled as ONNX. Not plate-trained. | Apache-2.0; no ongoing inference charge. [PaddleOCR license](https://github.com/PaddlePaddle/PaddleOCR/blob/main/pyproject.toml) | Already runs in OpenCV DNN. | Keep as baseline and fallback. |
| **FastPlateOCR global CCT-S v2** | Pretrained plate-specific global recognizer; FastPlateOCR states that its global models cover 65+ countries. Its v1.0.0 release describes CCT XS/S variants trained on 220,000 global plates. [Inference/model reference](https://ankandrew.github.io/fast-plate-ocr/latest/reference/inference/inference_class/) · [release evidence](https://github.com/ankandrew/fast-plate-ocr/releases/tag/v1.0.0) | MIT; local ONNX Runtime execution, no per-read service charge. [Package metadata](https://pypi.org/project/fast-plate-ocr/) | ONNX Runtime is the supported runtime. It expects RGB, `uint8`, channels-last input. Add a recognizer adapter and ONNX Runtime dependency; do not assume the transformer ONNX graph loads in the existing OpenCV DNN reader. | **Prototype first.** Best ready-made local candidate, but no published Philippines-specific score. |
| **Fine-tuned PP-OCR recognizer (plate alphabet)** | Train/fine-tune a PP-OCR recognizer using labelled Philippine crops and a plate-only `A-Z0-9` dictionary. PaddleOCR supports custom image/label data and a custom character dictionary. [Training guide](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version2.x/ppocr/model_train/recognition.en.md) | Apache-2.0; production inference remains local. Cost is dataset creation plus one-time training hardware/time. | Export supported inference model to ONNX with Paddle2ONNX; PaddleOCR documents stable Opset 9–11 export, while noting some architectures are not exportable. Validate the exported graph with the actual OpenCV version before shipping. [Paddle2ONNX guide](https://github.com/PaddlePaddle/PaddleOCR/blob/main/deploy/paddle2onnx/readme.md) | **Long-term production choice.** Highest expected fit once enough labelled local data exists. |
| Intel Open Model Zoo LPR (`0001`/`0007`) | Plate-specific and small, but explicitly trained for Chinese plates; `0001` was validated on 1,165 Chinese plates and warns that untested plate types may underperform. [Model card](https://github.com/openvinotoolkit/open_model_zoo/blob/master/models/intel/license-plate-recognition-barrier-0001/README.md) | Open Model Zoo is Apache-2.0. [Repository license](https://github.com/openvinotoolkit/open_model_zoo/blob/master/LICENSE) | Published for OpenVINO IR / its Security Barrier demo, not a drop-in ONNX model for the current reader. It also uses a Chinese province character dictionary. [Demo](https://github.com/openvinotoolkit/open_model_zoo/blob/master/demos/security_barrier_camera_demo/cpp/README.md) | Reject for Philippine plates. |
| PaddleOCR's published lightweight LPR pipeline | Plate-specific PP-OCRv3 pipeline, but its reported evaluation is CCPD (Chinese plates): 99% detection and 94% recognition; the source reports 12.8 MB combined / 5.8 MB quantized. [PaddleOCR LPR application](https://www.paddleocr.ai/v2.9/applications/%E8%BD%BB%E9%87%8F%E7%BA%A7%E8%BD%A6%E7%89%8C%E8%AF%86%E5%88%AB.html) | Apache-2.0; local execution. | Requires a pipeline/adapter and Philippine fine-tuning before it could be considered. | Do not use unmodified; it is useful only as a fine-tuning starting point. |

## Suggested proof before changing production

1. Preserve at least 100 correctly labelled, consented plate crops from the
   actual gate cameras, including daytime/nighttime, motion blur, glare, and
   the known `D`/`I` failures. Keep them out of the public repository.
2. Run the current PP-OCRv5 model and FastPlateOCR CCT-S v2 on exactly that
   fixed set. Compare exact plate accuracy, character accuracy, `D`/`I` error
   rate, p95 CPU latency, and unreadable/false-positive rate.
3. Adopt FastPlateOCR only if it materially improves exact accuracy without
   exceeding the gate's latency budget. Make it configurable with the current
   recognizer available as fallback.
4. If the global model still misses local fonts/formats, label more local crops
   and fine-tune PP-OCR with the exact `A-Z0-9` character set. Export to ONNX,
   verify loading and decoding against the production OpenCV DNN version, and
   promote only after the same regression suite passes.

## Scope and privacy

All recommended inference is local; no frame or plate number has to be sent to
an OCR cloud service. Training data should be collected only with the
operator's authority, access-controlled, and retained only as long as needed
for model evaluation and training.
