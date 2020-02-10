import cv2
import time
import math
import os
import json
import numpy as np
import tensorflow as tf
import lanms
import locality_aware_nms as nms_locality
from socket import *

tf.app.flags.DEFINE_string('gpu_list', '0', '')
tf.app.flags.DEFINE_string('checkpoint_path',
                           './east_icdar2015_resnet_v1_50_rbox/', '')

import model
from icdar import restore_rectangle

FLAGS = tf.app.flags.FLAGS


def resize_image(im, max_side_len=2400):
    '''
    resize image to a size multiple of 32 which is required by the network
    :param im: the resized image
    :param max_side_len: limit of max image size to avoid out of memory in gpu
    :return: the resized image and the resize ratio
    '''
    h, w, _ = im.shape

    resize_w = w
    resize_h = h

    # limit the max side
    if max(resize_h, resize_w) > max_side_len:
        ratio = float(
            max_side_len) / resize_h if resize_h > resize_w else float(
                max_side_len) / resize_w
    else:
        ratio = 1.
    resize_h = int(resize_h * ratio)
    resize_w = int(resize_w * ratio)

    resize_h = resize_h if resize_h % 32 == 0 else (resize_h // 32 - 1) * 32
    resize_w = resize_w if resize_w % 32 == 0 else (resize_w // 32 - 1) * 32
    resize_h = max(32, resize_h)
    resize_w = max(32, resize_w)
    im = cv2.resize(im, (int(resize_w), int(resize_h)))

    ratio_h = resize_h / float(h)
    ratio_w = resize_w / float(w)

    return im, (ratio_h, ratio_w)


def detect(score_map,
           geo_map,
           timer,
           score_map_thresh=0.8,
           box_thresh=0.1,
           nms_thres=0.2):
    '''
    restore text boxes from score map and geo map
    :param score_map:
    :param geo_map:
    :param timer:
    :param score_map_thresh: threshhold for score map
    :param box_thresh: threshhold for boxes
    :param nms_thres: threshold for nms
    :return:
    '''
    if len(score_map.shape) == 4:
        score_map = score_map[0, :, :, 0]
        geo_map = geo_map[0, :, :, ]
    # filter the score map
    xy_text = np.argwhere(score_map > score_map_thresh)
    # sort the text boxes via the y axis
    xy_text = xy_text[np.argsort(xy_text[:, 0])]
    # restore
    start = time.time()
    text_box_restored = restore_rectangle(xy_text[:, ::-1] * 4,
                                          geo_map[xy_text[:, 0],
                                                  xy_text[:, 1], :])  # N*4*2
    boxes = np.zeros((text_box_restored.shape[0], 9), dtype=np.float32)
    boxes[:, :8] = text_box_restored.reshape((-1, 8))
    boxes[:, 8] = score_map[xy_text[:, 0], xy_text[:, 1]]
    timer['restore'] = time.time() - start
    # nms part
    start = time.time()
    # boxes = nms_locality.nms_locality(boxes.astype(np.float64), nms_thres)
    boxes = lanms.merge_quadrangle_n9(boxes.astype('float32'), nms_thres)
    timer['nms'] = time.time() - start

    if boxes.shape[0] == 0:
        return None, timer

    # here we filter some low score boxes by the average score map, this is different from the orginal paper
    for i, box in enumerate(boxes):
        mask = np.zeros_like(score_map, dtype=np.uint8)
        cv2.fillPoly(mask, box[:8].reshape((-1, 4, 2)).astype(np.int32) // 4,
                     1)
        boxes[i, 8] = cv2.mean(score_map, mask)[0]
    boxes = boxes[boxes[:, 8] > box_thresh]

    return boxes, timer


def extract_dots_with_setted_environment(img_path, input_images, f_score,
                                         f_geometry, saver):
    os.environ['CUDA_VISIBLE_DEVICES'] = FLAGS.gpu_list
    working_step = []
    with tf.Session(config=tf.ConfigProto(allow_soft_placement=True)) as sess:
        ckpt_state = tf.train.get_checkpoint_state(FLAGS.checkpoint_path)
        model_path = os.path.join(
            FLAGS.checkpoint_path,
            os.path.basename(ckpt_state.model_checkpoint_path))
        saver.restore(sess, model_path)
        im = cv2.imread(img_path)[:, :, ::-1]
        start_time = time.time()
        im_resized, (ratio_h, ratio_w) = resize_image(im)
        timer = {'net': 0, 'restore': 0, 'nms': 0}
        start = time.time()
        score, geometry = sess.run([f_score, f_geometry],
                                   feed_dict={input_images: [im_resized]})
        timer['net'] = time.time() - start
        boxes, timer = detect(score_map=score, geo_map=geometry, timer=timer)
        if boxes is not None:
            boxes = boxes[:, :8].reshape((-1, 4, 2))
            boxes[:, :, 0] /= ratio_w
            boxes[:, :, 1] /= ratio_h
        duration = time.time() - start_time
        dots = []
        if boxes is not None:
            for box in boxes:
                p1 = (int(box[0, 0]), int(box[0, 1]))
                p2 = (int(box[2, 0]), int(box[2, 1]))
                dots.append([p1, p2])
        return dots


def get_word_coord_thread_response(server_socket, content, addr):
    ret_value = get_word_coord()
    result = {"return": ret_value}


def get_word_coord_wrapper_server():
    with tf.get_default_graph().as_default():
        input_images = tf.placeholder(tf.float32,
                                      shape=[None, None, None, 3],
                                      name='input_images')
        global_step = tf.get_variable('global_step', [],
                                      initializer=tf.constant_initializer(0),
                                      trainable=False)

        f_score, f_geometry = model.model(input_images, is_training=False)

        variable_averages = tf.train.ExponentialMovingAverage(
            0.997, global_step)
        saver = tf.train.Saver(variable_averages.variables_to_restore())
        server_socket = socket(AF_INET, SOCK_DGRAM)
        server_socket.bind(('', 8080))

        print("SERVER ON")
        while True:
            data, addr = server_socket.recvfrom(65000)
            content = json.loads(data.decode('utf-8'))
            result = {
                'return':
                extract_dots_with_setted_environment(content['img_path'],
                                                     input_images, f_score,
                                                     f_geometry, saver)
            }
            print(result)
            server_socket.sendto(bytes(json.dumps(result), 'utf-8'), addr)


def main(argv=None):
    get_word_coord_wrapper_server()


if __name__ == '__main__':
    tf.app.run()
