#
"""A library to predict using Inception on a single GPU.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import math
import time
from datetime import datetime

import numpy as np
import tensorflow as tf

from fabric import util
from processors.imagenet.image_processing_imagenet import image_preprocess
from .feed import Feed

FLAGS = tf.app.flags.FLAGS

tf.app.flags.DEFINE_string(
    'predict_dir', '/tmp/imagenet_predict',
    """Directory where to write event logs.""")

tf.app.flags.DEFINE_string(
    'checkpoint_path', '/tmp/imagenet_train',
    """Directory or file where to read model checkpoint(s).""")

tf.app.flags.DEFINE_float(
    'moving_average_decay', None,
    'The decay to use for the moving average.'
    'If left as None, then moving averages are not used.')


def _predict(feed, saver, output_op, names_op):
    """Runs prediction
    """
    predictions = []
    with tf.Session() as sess:
        init_op = tf.group(tf.initialize_all_variables(), tf.initialize_local_variables())
        sess.run(init_op)

        checkpoint_path, global_step = util.resolve_checkpoint_path(FLAGS.checkpoint_path)
        if not checkpoint_path:
            print('No checkpoint file found at %s' % FLAGS.checkpoint_path)
            return
        saver.restore(sess, checkpoint_path)
        print('Successfully loaded model from %s at step=%d.' % (checkpoint_path, global_step))

        # Start the queue runners.
        coord = tf.train.Coordinator()
        threads = []
        try:
            for qr in tf.get_collection(tf.GraphKeys.QUEUE_RUNNERS):
                threads.extend(qr.create_threads(sess, coord=coord, daemon=True, start=True))

            num_examples = feed.num_examples_per_epoch()
            num_iter = int(math.ceil(num_examples / feed.batch_size))

            print('%s: starting inference on %d examples in (%s).' %
                  (datetime.now(), num_examples, feed.dataset.subset))
            step = 0
            start_time = time.time()
            while step < num_iter and not coord.should_stop():
                output_batch, name_batch = sess.run([output_op, names_op])
                name_batch = np.expand_dims(name_batch, axis=1)
                batch = np.concatenate([name_batch, output_batch], axis=1)

                step += 1
                if step % 20 == 0:
                    duration = time.time() - start_time
                    sec_per_batch = duration / 20.0
                    examples_per_sec = feed.batch_size / sec_per_batch
                    print('%s: [%d batches out of %d] (%.1f examples/sec; %.3f sec/batch)'
                          % (datetime.now(), step, num_iter, examples_per_sec, sec_per_batch))
                    start_time = time.time()

                predictions.append(batch)

        except Exception as e:  # pylint: disable=broad-except
            coord.request_stop(e)

        coord.request_stop()
        coord.join(threads, stop_grace_period_secs=10)

        return np.vstack(predictions)


def predict(dataset, model):
    """Predict/infer outputs for dataset using model."""
    with tf.Graph().as_default():
        # Get images and labels from the dataset.
        feed = Feed(dataset, image_preprocess, batch_size=FLAGS.batch_size)
        eval_inputs = feed.inputs_for_eval()
        inputs, identities, _ = feed.map_inputs(eval_inputs)
        # Build a Graph that computes the logits predictions from the inference model.
        outputs = model.build_tower(inputs)
        if dataset.has_background_class:
            softmax_output = tf.nn.softmax(tf.slice(outputs, [0, 1], [-1, -1]))
        else:
            softmax_output = tf.nn.softmax(outputs)

        if FLAGS.moving_average_decay:
            variable_averages = tf.train.ExponentialMovingAverage(model.MOVING_AVERAGE_DECAY)
            variables_to_restore = variable_averages.variables_to_restore()
        else:
            variables_to_restore = tf.contrib.framework.get_model_variables()

        saver = tf.train.Saver(variables_to_restore)

        predictions = _predict(feed, saver, softmax_output, identities)

        return predictions