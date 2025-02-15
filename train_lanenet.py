import argparse
import math
import os
import os.path as ops
import time
import tensorflow as tf
import cv2
import glog as log
import numpy as np
#tf.compat.v1.disable_eager_execution()
#tf.config.run_functions_eagerly(False)
#import tensorflow as tf
import lanenet_merge_model
import lanenet_data_processor
import global_config

CFG = global_config.cfg
VGG_MEAN = [103.939, 116.779, 123.68]




def minmax_scale(input_arr):
    """

    :param input_arr:
    :return:
    """
    min_val = np.min(input_arr)
    max_val = np.max(input_arr)

    output_arr = (input_arr - min_val) * 255.0 / (max_val - min_val)

    return output_arr


def train_net(dataset_dir, weights_path=None, net_flag='vgg'):
    """

    :param dataset_dir:
    :param net_flag: choose which base network to use
    :param weights_path:
    :return:
    """

    print("DATASET directory: ", dataset_dir)

    train_dataset_file = ops.join(dataset_dir, 'train.txt').replace("\\","/")
    val_dataset_file = ops.join(dataset_dir, 'val.txt').replace("\\","/")

    print("TRAIN_DATASET_FILE = ", train_dataset_file)
    print("VAL_DATASET_FILE = ", val_dataset_file)

    assert ops.exists(train_dataset_file)

    train_dataset = lanenet_data_processor.DataSet(train_dataset_file)
    val_dataset = lanenet_data_processor.DataSet(val_dataset_file)

    print("TRAIN_DATASET = ", train_dataset)
    print("TRAIN_DATASET LENGTH = ", len(train_dataset._gt_img_list))
    print("VAL_DATASET = ", val_dataset)
    print("VAL_DATASET LENGTH = ", len(val_dataset._gt_img_list))
    #tf.reset_default_graph()
    with tf.device('/gpu:1'):
        input_tensor = tf.compat.v1.placeholder(dtype=tf.float32,
                                      shape=[CFG.TRAIN.BATCH_SIZE, CFG.TRAIN.IMG_HEIGHT,
                                             CFG.TRAIN.IMG_WIDTH, 3], name='input_tensor')
        binary_label_tensor = tf.compat.v1.placeholder(dtype=tf.int64,
                                             shape=[CFG.TRAIN.BATCH_SIZE, CFG.TRAIN.IMG_HEIGHT,
                                                    CFG.TRAIN.IMG_WIDTH, 1],
                                             name='binary_input_label')
        instance_label_tensor = tf.compat.v1.placeholder(dtype=tf.float32,
                                               shape=[CFG.TRAIN.BATCH_SIZE, CFG.TRAIN.IMG_HEIGHT,
                                                      CFG.TRAIN.IMG_WIDTH],
                                               name='instance_input_label')
        phase = tf.compat.v1.placeholder(dtype=tf.string, shape=None, name='net_phase')

        net = lanenet_merge_model.LaneNet(net_flag=net_flag, phase=phase)

        # calculate the loss
        compute_ret = net.compute_loss(input_tensor=input_tensor, binary_label=binary_label_tensor,
                                       instance_label=instance_label_tensor, name='lanenet_model')
        total_loss = compute_ret['total_loss']
        binary_seg_loss = compute_ret['binary_seg_loss']
        disc_loss = compute_ret['discriminative_loss']
        pix_embedding = compute_ret['instance_seg_logits']

        # calculate the accuracy
        out_logits = compute_ret['binary_seg_logits']
        out_logits = tf.nn.softmax(logits=out_logits)
        out_logits_out = tf.argmax(out_logits, axis=-1)
        out = tf.argmax(out_logits, axis=-1)
        out = tf.expand_dims(out, axis=-1)

        idx = tf.where(tf.equal(binary_label_tensor, 1))
        pix_cls_ret = tf.gather_nd(out, idx)
        accuracy = tf.count_nonzero(pix_cls_ret)
        accuracy = tf.divide(accuracy, tf.cast(tf.shape(pix_cls_ret)[0], tf.int64))

        global_step = tf.Variable(0, trainable=False)
        #global_step=tf.train.get_global_step()
        learning_rate = tf.train.exponential_decay(CFG.TRAIN.LEARNING_RATE, global_step,
                                                   100000, 0.1, staircase=True)
        update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
        with tf.control_dependencies(update_ops):
            optimizer = tf.train.MomentumOptimizer(
                learning_rate=learning_rate, momentum=0.9).minimize(loss=total_loss,
                                                                    var_list=tf.trainable_variables(),
                                                                    global_step=global_step)

    # Set tf saver
    saver = tf.train.Saver()
    model_save_dir = './model/culane_lanenet_2/'
    if not ops.exists(model_save_dir):
        os.makedirs(model_save_dir)
    train_start_time = time.strftime('%Y-%m-%d-%H-%M-%S', time.localtime(time.time()))
    model_name = 'culane_lanenet_2_{:s}_{:s}.ckpt'.format(net_flag, str(train_start_time))
    model_save_path = ops.join(model_save_dir, model_name)

    # Set tf summary
    tboard_save_path = 'tboard/culane_lanenet_2/{:s}'.format(net_flag)
    if not ops.exists(tboard_save_path):
        os.makedirs(tboard_save_path)
    train_cost_scalar = tf.summary.scalar(name='train_cost', tensor=total_loss)
    val_cost_scalar = tf.summary.scalar(name='val_cost', tensor=total_loss)
    train_accuracy_scalar = tf.summary.scalar(name='train_accuracy', tensor=accuracy)
    val_accuracy_scalar = tf.summary.scalar(name='val_accuracy', tensor=accuracy)
    train_binary_seg_loss_scalar = tf.summary.scalar(name='train_binary_seg_loss', tensor=binary_seg_loss)
    val_binary_seg_loss_scalar = tf.summary.scalar(name='val_binary_seg_loss', tensor=binary_seg_loss)
    train_instance_seg_loss_scalar = tf.summary.scalar(name='train_instance_seg_loss', tensor=disc_loss)
    val_instance_seg_loss_scalar = tf.summary.scalar(name='val_instance_seg_loss', tensor=disc_loss)
    learning_rate_scalar = tf.summary.scalar(name='learning_rate', tensor=learning_rate)
    train_merge_summary_op = tf.summary.merge([train_accuracy_scalar, train_cost_scalar,
                                               learning_rate_scalar, train_binary_seg_loss_scalar,
                                               train_instance_seg_loss_scalar])
    val_merge_summary_op = tf.summary.merge([val_accuracy_scalar, val_cost_scalar,
                                             val_binary_seg_loss_scalar, val_instance_seg_loss_scalar])

    # Set sess configuration
    sess_config = tf.ConfigProto(allow_soft_placement=True)
    sess_config.gpu_options.per_process_gpu_memory_fraction = CFG.TRAIN.GPU_MEMORY_FRACTION
    sess_config.gpu_options.allow_growth = CFG.TRAIN.TF_ALLOW_GROWTH
    sess_config.gpu_options.allocator_type = 'BFC'

    sess = tf.Session(config=sess_config)

    summary_writer = tf.summary.FileWriter(tboard_save_path)
    summary_writer.add_graph(sess.graph)

    # Set the training parameters
    train_epochs = CFG.TRAIN.EPOCHS

    log.info('Global configuration is as follows:')
    log.info(CFG)

    with sess.as_default():

        tf.train.write_graph(graph_or_graph_def=sess.graph, logdir='',
                             name='{:s}/lanenet_model.pb'.format(model_save_dir))

        if weights_path is None:
            log.info('Training from scratch')
            init = tf.global_variables_initializer()
            sess.run(init)
        else:
            log.info('Restore model from last model checkpoint {:s}'.format(weights_path))
            saver.restore(sess=sess, save_path=weights_path)

        # 加载预训练参数
        if net_flag == 'vgg' and weights_path is None:
            pretrained_weights = np.load(
                './data/vgg16.npy',allow_pickle=True,
                encoding='latin1').item()

            for vv in tf.trainable_variables():
                weights_key = vv.name.split('/')[-3]
                try:
                    weights = pretrained_weights[weights_key][0]
                    _op = tf.assign(vv, weights)
                    sess.run(_op)
                except Exception as e:
                    continue

        train_cost_time_mean = []
        val_cost_time_mean = []
        for epoch in range(train_epochs):
            # training part
            t_start = time.time()

            with tf.device('/gpu:0'):
                gt_imgs, binary_gt_labels, instance_gt_labels = train_dataset.next_batch(CFG.TRAIN.BATCH_SIZE)
                gt_imgs = [cv2.resize(tmp,
                                      dsize=(CFG.TRAIN.IMG_WIDTH, CFG.TRAIN.IMG_HEIGHT),
                                      dst=tmp,
                                      interpolation=cv2.INTER_LINEAR)
                           for tmp in gt_imgs]
                gt_imgs = [tmp - VGG_MEAN for tmp in gt_imgs]
                binary_gt_labels = [cv2.resize(tmp,
                                               dsize=(CFG.TRAIN.IMG_WIDTH, CFG.TRAIN.IMG_HEIGHT),
                                               dst=tmp,
                                               interpolation=cv2.INTER_NEAREST)
                                    for tmp in binary_gt_labels]
                binary_gt_labels = [np.expand_dims(tmp, axis=-1) for tmp in binary_gt_labels]
                instance_gt_labels = [cv2.resize(tmp,
                                                 dsize=(CFG.TRAIN.IMG_WIDTH, CFG.TRAIN.IMG_HEIGHT),
                                                 dst=tmp,
                                                 interpolation=cv2.INTER_NEAREST)
                                      for tmp in instance_gt_labels]
            phase_train = 'train'

            _, c, train_accuracy, train_summary, binary_loss, instance_loss, embedding, binary_seg_img = \
                sess.run([optimizer, total_loss,
                          accuracy,
                          train_merge_summary_op,
                          binary_seg_loss,
                          disc_loss,
                          pix_embedding,
                          out_logits_out],
                         feed_dict={input_tensor: gt_imgs,
                                    binary_label_tensor: binary_gt_labels,
                                    instance_label_tensor: instance_gt_labels,
                                    phase: phase_train})

            if math.isnan(c) or math.isnan(binary_loss) or math.isnan(instance_loss):
                log.error('cost is: {:.5f}'.format(c))
                log.error('binary cost is: {:.5f}'.format(binary_loss))
                log.error('instance cost is: {:.5f}'.format(instance_loss))
                cv2.imwrite('nan_image.png', gt_imgs[0] + VGG_MEAN)
                cv2.imwrite('nan_instance_label.png', instance_gt_labels[0])
                cv2.imwrite('nan_binary_label.png', binary_gt_labels[0] * 255)
                return

            if epoch % 100 == 0:
                cv2.imwrite('image.png', gt_imgs[0] + VGG_MEAN)
                cv2.imwrite('binary_label.png', binary_gt_labels[0] * 255)
                cv2.imwrite('instance_label.png', instance_gt_labels[0])
                cv2.imwrite('binary_seg_img.png', binary_seg_img[0] * 255)

                for i in range(4):
                    embedding[0][:, :, i] = minmax_scale(embedding[0][:, :, i])
                embedding_image = np.array(embedding[0], np.uint8)
                cv2.imwrite('embedding.png', embedding_image)

            cost_time = time.time() - t_start
            train_cost_time_mean.append(cost_time)
            summary_writer.add_summary(summary=train_summary, global_step=epoch)

            # validation part
            with tf.device('/gpu:0'):
                gt_imgs_val, binary_gt_labels_val, instance_gt_labels_val \
                    = val_dataset.next_batch(CFG.TRAIN.VAL_BATCH_SIZE)
                gt_imgs_val = [cv2.resize(tmp,
                                          dsize=(CFG.TRAIN.IMG_WIDTH, CFG.TRAIN.IMG_HEIGHT),
                                          dst=tmp,
                                          interpolation=cv2.INTER_LINEAR)
                               for tmp in gt_imgs_val]
                gt_imgs_val = [tmp - VGG_MEAN for tmp in gt_imgs_val]
                binary_gt_labels_val = [cv2.resize(tmp,
                                                   dsize=(CFG.TRAIN.IMG_WIDTH, CFG.TRAIN.IMG_HEIGHT),
                                                   dst=tmp)
                                        for tmp in binary_gt_labels_val]
                binary_gt_labels_val = [np.expand_dims(tmp, axis=-1) for tmp in binary_gt_labels_val]
                instance_gt_labels_val = [cv2.resize(tmp,
                                                     dsize=(CFG.TRAIN.IMG_WIDTH, CFG.TRAIN.IMG_HEIGHT),
                                                     dst=tmp,
                                                     interpolation=cv2.INTER_NEAREST)
                                          for tmp in instance_gt_labels_val]
            phase_val = 'test'

            t_start_val = time.time()
            c_val, val_summary, val_accuracy, val_binary_seg_loss, val_instance_seg_loss = \
                sess.run([total_loss, val_merge_summary_op, accuracy, binary_seg_loss, disc_loss],
                         feed_dict={input_tensor: gt_imgs_val,
                                    binary_label_tensor: binary_gt_labels_val,
                                    instance_label_tensor: instance_gt_labels_val,
                                    phase: phase_val})

            if epoch % 100 == 0:
                cv2.imwrite('test_image.png', gt_imgs_val[0] + VGG_MEAN)

            summary_writer.add_summary(val_summary, global_step=epoch)

            cost_time_val = time.time() - t_start_val
            val_cost_time_mean.append(cost_time_val)

            if epoch % CFG.TRAIN.DISPLAY_STEP == 0:
                log.info('Epoch: {:d} total_loss= {:6f} binary_seg_loss= {:6f} instance_seg_loss= {:6f} accuracy= {:6f}'
                         ' mean_cost_time= {:5f}s '.
                         format(epoch + 1, c, binary_loss, instance_loss, train_accuracy,
                                np.mean(train_cost_time_mean)))
                train_cost_time_mean.clear()

            if epoch % CFG.TRAIN.TEST_DISPLAY_STEP == 0:
                log.info('Epoch_Val: {:d} total_loss= {:6f} binary_seg_loss= {:6f} '
                         'instance_seg_loss= {:6f} accuracy= {:6f} '
                         'mean_cost_time= {:5f}s '.
                         format(epoch + 1, c_val, val_binary_seg_loss, val_instance_seg_loss, val_accuracy,
                                np.mean(val_cost_time_mean)))
                val_cost_time_mean.clear()

            if epoch % 10 == 0:
                saver.save(sess=sess, save_path=model_save_path, global_step=epoch)
    sess.close()

    return


if __name__ == '__main__':

    # train lanenet
    dataset_dir="C:/Users/talka/OneDrive/Dokumenty/Studia/2stopien/WK/Projekt/culane_dataset_lanenet/"
    weights_path= "model/culane_lanenet_2/culane_lanenet_2_vgg_2022-01-04-05-36-40.ckpt-8890"
    net_flag="vgg"
    train_net(dataset_dir, weights_path, net_flag)