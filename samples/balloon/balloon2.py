"""
Mask R-CNN
Train on the toy Balloon dataset and implement color splash effect.

Copyright (c) 2018 Matterport, Inc.
Licensed under the MIT License (see LICENSE for details)
Written by Waleed Abdulla

------------------------------------------------------------

Usage: import the module (see Jupyter notebooks for examples), or run from
       the command line as such:

    # Train a new model starting from pre-trained COCO weights
    python3 balloon.py train --dataset=/path/to/balloon/dataset --weights=coco

    # Resume training a model that you had trained earlier
    python3 balloon.py train --dataset=/path/to/balloon/dataset --weights=last

    # Train a new model starting from ImageNet weights
    python3 balloon.py train --dataset=/path/to/balloon/dataset --weights=imagenet

    # Apply color splash to an image
    python3 balloon.py splash --weights=/path/to/weights/file.h5 --image=<URL or path to file>

    # Apply color splash to video using the last weights you trained
    python3 balloon.py splash --weights=last --video=<URL or path to file>
"""

import os
import os.path as osp
import sys
import json
import glob
import datetime
import numpy as np
import skimage.draw
from skimage.filters import threshold_otsu
from pathlib import Path
import tensorflow as tf
from tensorflow.python.framework.ops import disable_eager_execution

# Root directory of the project
ROOT_DIR = os.path.abspath("/content/Mask_RCNN")

# Import Mask RCNN
sys.path.append(ROOT_DIR)  # To find local version of the library
from mrcnn.config import Config
from mrcnn import model as modellib, utils

# Path to trained weights file
COCO_WEIGHTS_PATH = os.path.join(ROOT_DIR, "mask_rcnn_coco.h5")

# Directory to save logs and model checkpoints, if not provided
# through the command line argument --logs
DEFAULT_LOGS_DIR = os.path.join(ROOT_DIR, "logs")

############################################################
#  Configurations
############################################################


class BalloonConfig(Config):
    """Configuration for training on the toy  dataset.
    Derives from the base Config class and overrides some values.
    """
    # Give the configuration a recognizable name
    NAME = "reported"

    # We use a GPU with 12GB memory, which can fit two images.
    # Adjust down if you use a smaller GPU.
    IMAGES_PER_GPU = 2

    # Number of classes (including background)
    NUM_CLASSES = 1 + 2  # Background + balloon

    # Number of training steps per epoch
    STEPS_PER_EPOCH = 100

    # Skip detections with < 90% confidence
    DETECTION_MIN_CONFIDENCE = 0.9


############################################################
#  Dataset
############################################################

class BalloonDataset(utils.Dataset):

    def load_balloon(self, dataset_dir, subset):
        """Load a subset of the Balloon dataset.
        dataset_dir: Root directory of the dataset.
        subset: Subset to load: train or val
        """
        # Add classes. We have only one class to add.
        self.add_class("reported", 1, "blocked bike lane")
        self.add_class("reported", 2, "blocked the crosswalk")

        # Train or validation dataset?
        assert subset in ["train", "val"]
        labelz = {
            "blocked bike lane": 1,
            "blcoked the crosswalk": 2
        }

        dataset_dir = os.path.join(dataset_dir, subset)
        print("dataset_dir",dataset_dir)
        # Load annotations
        # VGG Image Annotator (up to version 1.6) saves each image in the form:
        # { 'filename': '28503151_5b5b7ec140_b.jpg',
        #   'regions': {
        #       '0': {
        #           'region_attributes': {},
        #           'shape_attributes': {
        #               'all_points_x': [...],
        #               'all_points_y': [...],
        #               'name': 'polygon'}},
        #       ... more regions ...
        #   },
        #   'size': 100202
        # }
        # We mostly care about the x and y coordinates of each region
        # Note: In VIA 2.0, regions was changed from a dict to a list.
        files = glob.glob(osp.join(dataset_dir, "*.json"))
        # print("found files", files)
        for file in files:
            p = Path(file)
            annotation = json.load(open(file))
            a = annotation
            polygons = []
            labels = []
            for shape in a["shapes"]:
                label = shape["label"]
                if label not in labelz:
                    continue
                if shape["shape_type"]!="polygon":
                    continue
                polygon = shape["points"]
                polygons.append(polygon)
                labels.append(label)
            if len(labels)==0:
                continue
            image_path = os.path.join(dataset_dir, str(p.with_suffix('.jpg')))
            image = skimage.io.imread(image_path)
            height, width = image.shape[:2]
            print("adding image ", polygons, labels)
            suff = str(p.with_suffix('.jpg'))
            num_ids = []
            for x in labels:
                if x in labelz:
                    num_ids.append(labelz[x])
            self.add_image(
                "reported",
                image_id=suff,  # use file name as a unique image id
                path=image_path,
                width=width, height=height,
                polygons=polygons,
                num_ids=num_ids
                )
        #
        # annotations = json.load(open(os.path.join(dataset_dir, "via_region_data.json")))
        # annotations = list(annotations.values())  # don't need the dict keys

        # The VIA tool saves images in the JSON even if they don't have any
        # annotations. Skip unannotated images.


    def load_mask(self, image_id):
        """Generate instance masks for an image.
       Returns:
        masks: A bool array of shape [height, width, instance count] with
            one mask per instance.
        class_ids: a 1D array of class IDs of the instance masks.
        """
        # If not a balloon dataset image, delegate to parent class.
        image_info = self.image_info[image_id]
        if image_info["source"] != "reported":
            return super(self.__class__, self).load_mask(image_id)

        # Convert polygons to a bitmap mask of shape
        # [height, width, instance_count]
        info = self.image_info[image_id]
        polygons = info["polygons"]
        mask = np.zeros([info["height"], info["width"], len(polygons)],
                        dtype=np.uint8)
        for index, polygon in enumerate(polygons):
            x = []
            y = []
            for k in polygon:
                x.append(k[0])
                y.append(k[1])
            # Get indexes of pixels inside the polygon and set them to 1
            rr, cc = skimage.draw.polygon(y,x)
            mask[rr, cc, index] = 1

        # Return mask, and array of class IDs of each instance. Since we have
        # one class ID only, we return an array of 1s
        num_ids = info['num_ids']
        num_ids = np.array(num_ids, dtype=np.int32)
        return mask, num_ids

    def image_reference(self, image_id):
        """Return the path of the image."""
        info = self.image_info[image_id]
        if info["source"] == "reported":
            return info["path"]
        else:
            super(self.__class__, self).image_reference(image_id)


def color_splash(image, mask):
    """Apply color splash effect.
    image: RGB image [height, width, 3]
    mask: instance segmentation mask [height, width, instance count]

    Returns result image.
    """
    # Make a grayscale copy of the image. The grayscale copy still
    # has 3 RGB channels, though.
    gray = skimage.color.gray2rgb(skimage.color.rgb2gray(image)) * 255
    thresh = threshold_otsu(gray)
    binary_thresh_img = gray > thresh
    # Copy color pixels from the original color image where mask is set
    if mask.shape[-1] > 0:
        # We're treating all instances as one, so collapse the mask into one layer
        mask = (np.sum(mask, -1, keepdims=True) >= 1)
        splash = np.where(mask, image, binary_thresh_img).astype(np.uint8)
    else:
        splash = gray.astype(np.uint8)
    return splash


def detect_and_color_splash(model, images_path=None, video_path=None):
    assert images_path or video_path

    # Image or video?
    if images_path:
        # Run model detection and generate the color splash effect
        print("Running on {}".format(images_path))
        for image_path in images_path.split(","):
            # Read image
            image = skimage.io.imread(image_path)
            # Detect objects
            r = model.detect([image], verbose=1)[0]
            # Color splash
            splash = color_splash(image, r['masks'])
            # Save output
            file_name = f'splash_{os.path.basename(image_path)}.png'
            skimage.io.imsave(file_name, splash)
    elif video_path:
        import cv2
        # Video capture
        vcapture = cv2.VideoCapture(video_path)
        width = int(vcapture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(vcapture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = vcapture.get(cv2.CAP_PROP_FPS)

        # Define codec and create video writer
        file_name = "splash_{:%Y%m%dT%H%M%S}.avi".format(datetime.datetime.now())
        vwriter = cv2.VideoWriter(file_name,
                                  cv2.VideoWriter_fourcc(*'MJPG'),
                                  fps, (width, height))

        count = 0
        success = True
        while success:
            print("frame: ", count)
            # Read next image
            success, image = vcapture.read()
            if success:
                # OpenCV returns images as BGR, convert to RGB
                image = image[..., ::-1]
                # Detect objects
                r = model.detect([image], verbose=0)[0]
                # Color splash
                splash = color_splash(image, r['masks'])
                # RGB -> BGR to save image to video
                splash = splash[..., ::-1]
                # Add image to video writer
                vwriter.write(splash)
                count += 1
        vwriter.release()
    print("Saved to ", file_name)


############################################################
#  Training
############################################################

if __name__ == '__main__':
    import argparse

    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='Train Mask R-CNN to detect balloons.')
    parser.add_argument('--weights', required=True,
                        metavar="/path/to/weights.h5",
                        help="Path to weights .h5 file or 'coco'")
    parser.add_argument('--image', required=False,
                        metavar="path or URL to image",
                        help='Image to apply the color splash effect on')
    parser.add_argument('--images', required=False,
                        metavar="path or URL to image",
                        help='Image to apply the color splash effect on')
    parser.add_argument('--video', required=False,
                        metavar="path or URL to video",
                        help='Video to apply the color splash effect on')
    args = parser.parse_args()

    class InferenceConfig(BalloonConfig):
        # Set batch size to 1 since we'll be running inference on
        # one image at a time. Batch size = GPU_COUNT * IMAGES_PER_GPU
        GPU_COUNT = 1
        IMAGES_PER_GPU = 1
    config = InferenceConfig()
    #config.display()

    model = modellib.MaskRCNN(mode="inference", config=config,
                                  model_dir="logs")


    weights_path = args.weights

    # Load weights

    model.load_weights(weights_path, by_name=True)

    # detect_and_color_splash(model, images_path=args.images,
    #                             video_path=args.video)
    disable_eager_execution()
    print(str(model.keras_model))
    converter = tf.lite.TFLiteConverter.from_keras_model(model.keras_model)
    tflite_model = converter.convert()
    with open('model.tflite', 'wb') as f:
        f.write(tflite_model)
