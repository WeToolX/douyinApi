Douyin UI YOLO CoreML export for TouchSprite.

Model: douyin-ui-20cls-jun11-final-hardfix-yolov8s best.pt
Input: RGB image, width=544 height=960
Outputs: confidence [N,20], coordinates [N,4]
CoreML NMS defaults: confidenceThreshold=0.25, iouThreshold=0.45

Build method:
This package is exported from source with TouchSprite-compatible old CoreML pipeline behavior:
- no class padding to 80 columns
- pipeline spec version 5
- nonMaximumSuppression spec version 5
- user metadata includes names/imgsz/task/stride

Use model.mlpackage as the TouchSprite setModel path.
