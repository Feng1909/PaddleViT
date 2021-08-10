import numpy as np
import math
import cv2
import collections.abc
import paddle
import paddle.nn.functional as F

def slide_inference(model, img, crop_size, stride_size, num_classes):
    """
    Inference by sliding-window with overlap, the overlap is equal to stride.

    Args:
        model (paddle.nn.Layer): model to get logits of image.
        im (Tensor): the input image.
        crop_size (tuple|list): the size of sliding window, (w, h).
        stride_size (tuple|list): the size of stride, (w, h).
        num_classes (int): the number of classes

    Return:
        final_logit (Tensor): The logit of input image, whose size is equal to 
        the size of img (not the orginal size).
    """
    h_img, w_img = img.shape[-2:]
    w_crop, h_crop = crop_size
    w_stride, h_stride = stride_size
    # calculate the crop nums
    rows = max(h_img - h_crop + h_stride -1, 0) // h_stride + 1
    cols = max(w_img - w_crop + w_stride -1, 0) // w_stride + 1
    count = np.zeros([1, 1, h_img, w_img])
    final_logit = paddle.zeros([1, num_classes, h_img, w_img], dtype='float32')
    for r in range(rows):
        for c in range(cols):
            h1 = r * h_stride
            w1 = c * w_stride
            h2 = min(h1 + h_crop, h_img)
            w2 = min(w1 + w_crop, w_img)
            h1 = max(h2 - h_crop, 0)
            w1 = max(w2 - w_crop, 0)
            img_crop = img[:, :, h1:h2, w1:w2]
            logits = model(img_crop)
            logit = logits[0]
            final_logit += F.pad(logit, [w1, w_img - w2, h1, h_img - h2])
            count[:, :, h1:h2, w1:w2] += 1
    final_logit = final_logit.numpy() / count
    final_logit = paddle.to_tensor(final_logit)
    return final_logit


def ss_inference(model, img, ori_shape, transforms, is_slide, base_size, 
        stride_size, crop_size, num_classes, rescale_from_ori=False):
    """
    Single-scale inference for image.

    Args:
        model (paddle.nn.Layer): model to get logits of image.
        img (Tensor): the input image.
        ori_shape (list): origin shape of image.
        transforms (list): transforms for image.
        is_slide (bool): whether to infer by sliding window.
        base_size (list): the size of short edge is resize to min(base_size) 
        when it is smaller than min(base_size)  
        stride_size (tuple|list): the size of stride, (w, h). It should be 
        probided if is_slide is True.
        crop_size (tuple|list). the size of sliding window, (w, h). It should 
        be probided if is_slide is True.
        num_classes (int): the number of classes
        rescale_from_ori (bool): whether rescale image from the original size. 
        Default: False.

    Returns:
        pred (tensor): If ori_shape is not None, a prediction with shape (1, 1, h, w) 
        is returned. If ori_shape is None, a logit with shape (1, num_classes, 
        h, w) is returned.
    """
    if not is_slide:
        logits = model(img)
        if not isinstance(logits, collections.abc.Sequence):
            raise TypeError("The type of logits must be one of "
                "collections.abc.Sequence, e.g. list, tuple. But received {}"
                .format(type(logits)))
        logit = logits[0]
    else:
        # TODO (wutianyiRosun@gmail.com): when dataloader does not uses resize,
        #  rescale or padding
        if rescale_from_ori:
            h, w = img.shape[-2], img.shape[-1]
            if min(h,w) < min(base_size):
                new_short = min(base_size)
                if h > w :
                    new_h, new_w = int(new_short * h / w), new_short
                else:
                    new_h, new_w = new_short, int(new_short * w / h)
                h, w = new_h, new_w
                img = F.interpolate(img, (h, w), mode='bilinear')
                #print("rescale, img.shape: ({}, {})".format(h,w))
        logit = slide_inference(model, img, crop_size, stride_size, num_classes)

    if ori_shape is not None:
        # resize to original shape
        logit = F.interpolate(logit, ori_shape, mode='bilinear', align_corners=False)  
        logit = F.softmax(logit, axis=1)
        pred = paddle.argmax(logit, axis=1, keepdim=True, dtype='int32')
        return pred
    else:
        return logit


def ms_inference(model,
                  img,
                  ori_shape,
                  transforms,
                  is_slide,
                  base_size,
                  stride_size,
                  crop_size,
                  num_classes, 
                  scales=[1.0,],
                  flip_horizontal=True, 
                  flip_vertical=False,
                  rescale_from_ori=False):

    """
    Multi-scale inference.

    For each scale, the segmentation result is first generated by sliding-window
    testing with overlap. Then the segmentation result is resize to the original 
    size, followed by softmax operation. Finally, the segmenation logits of all 
    scales are averaged (+argmax) 

    Args:
        model (paddle.nn.Layer): model to get logits of image.
        img (Tensor): the input image.
        ori_shape (list): origin shape of image.
        transforms (list): transforms for image.
        is_slide (bool): whether to infer by sliding wimdow. 
        base_size (list): the size of short edge is resize to min(base_size) 
        when it is smaller than min(base_size)  
        crop_size (tuple|list). the size of sliding window, (w, h). It should
        be probided if is_slide is True.
        stride_size (tuple|list). the size of stride, (w, h). It should be 
        probided if is_slide is True.
        num_classes (int): the number of classes
        scales (list):  scales for resize. Default: [1.0,].
        flip_horizontal (bool): whether to flip horizontally. Default: True
        flip_vertical (bool): whether to flip vertically. Default: False.
        rescale_from_ori (bool): whether rescale image from the original size. Default: False.

    Returns:
        Pred (tensor): Prediction of image with shape (1, 1, h, w) is returned.
    """
    if not isinstance(scales, (tuple, list)):
        raise('`scales` expects tuple/list, but received {}'.format(type(scales)))
    final_logit = 0
    if rescale_from_ori:
        if not isinstance(base_size, tuple):
            raise('base_size is not a tuple, but received {}'.format(type(tupel)))
        h_input, w_input = base_size
    else:
        h_input, w_input = img.shape[-2], img.shape[-1]
    for scale in scales:
        h = int(h_input * scale + 0.5)
        w = int(w_input * scale + 0.5)
        if rescale_from_ori:
            # TODO (wutianyiRosun@gmail.com): whole image testing, rescale 
            # original image according to the scale_factor between the 
            # origianl size and scale
            # scale_factor := min ( max(scale) / max(ori_size), min(scale) / min(ori_size) ) 
            h_ori, w_ori = img.shape[-2], img.shape[-1]
            max_long_edge = max(h, w)
            max_short_edge = min(h, w)
            scale_factor = min(max_long_edge / max(h, w),
                               max_short_edge / min(h, w))
            # compute new size
            new_h = int(h_ori * float(scale_factor) + 0.5)
            new_w = int(w_ori * float(scale_factor) + 0.5)
            h, w = new_h, new_w
            img = F.interpolate(img, (h, w), mode='bilinear')
            logits = model(img)
            logit = logits[0]
        else:
            # sliding-window testing
            # if min(h,w) is smaller than crop_size[0], the smaller edge of the
            # image will be matched to crop_size[0] maintaining the aspect ratio
            if min(h,w) < crop_size[0]:
                new_short = crop_size[0]
                if h > w :
                    new_h, new_w = int(new_short * h / w), new_short
                else:
                    new_h, new_w = new_short, int(new_short * w / h)
                h, w = new_h, new_w
            img = F.interpolate(img, (h, w), mode='bilinear')
            logit = slide_inference(model, img, crop_size, stride_size, num_classes)

        logit = F.interpolate(logit, ori_shape, mode='bilinear', align_corners=False)  
        logit = F.softmax(logit, axis=1)
        final_logit = final_logit + logit
        # flip_horizontal testing
        if flip_horizontal == True:
            img_flip = img[:, :, :, ::-1]
            logit_flip = slide_inference(model, img_flip, crop_size, 
                stride_size, num_classes)
            logit = logit_flip[:, :, :, ::-1]
            logit = F.interpolate(logit, ori_shape, mode='bilinear', align_corners=False)  
            logit = F.softmax(logit, axis=1)
            final_logit = final_logit + logit
        # TODO (wutianyiRosun@gmail.com): add flip_vertical testing
    pred = paddle.argmax(final_logit, axis=1, keepdim=True, dtype='int32')
    return pred
