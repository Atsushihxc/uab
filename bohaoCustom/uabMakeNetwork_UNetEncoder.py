import os
import time
import numpy as np
import tensorflow as tf
from bohaoCustom import uabMakeNetwork as network
from bohaoCustom import uabMakeNetwork_UNet


class UnetEncoder(uabMakeNetwork_UNet.UnetModelCrop):
    def __init__(self, inputs, trainable, input_size, model_name='', dropout_rate=None,
                 learn_rate=1e-4, decay_step=60, decay_rate=0.1, epochs=100,
                 batch_size=5, start_filter_num=32, latent_num=500):
        network.Network.__init__(self, inputs, trainable, dropout_rate,
                                 learn_rate, decay_step, decay_rate, epochs, batch_size)
        self.name = 'UnetEncoder'
        self.model_name = self.get_unique_name(model_name)
        self.sfn = start_filter_num
        self.learning_rate = None
        self.valid_cross_entropy = tf.placeholder(tf.float32, [])
        self.update_ops = None
        self.config = None
        self.n_train = 0
        self.n_valid = 0
        self.latent_num = latent_num

    def create_graph(self, x_name, class_num, start_filter_num=32):
        self.class_num = class_num
        sfn = self.sfn

        # downsample
        conv1, pool1 = self.conv_conv_pool(self.inputs[x_name], [sfn, sfn], self.trainable, name='conv1',
                                           padding='valid', dropout=self.dropout_rate)
        conv2, pool2 = self.conv_conv_pool(pool1, [sfn*2, sfn*2], self.trainable, name='conv2',
                                           padding='valid', dropout=self.dropout_rate)
        conv3, pool3 = self.conv_conv_pool(pool2, [sfn*4, sfn*4], self.trainable, name='conv3',
                                           padding='valid', dropout=self.dropout_rate)
        conv4, pool4 = self.conv_conv_pool(pool3, [sfn*8, sfn*8], self.trainable, name='conv4',
                                           padding='valid', dropout=self.dropout_rate)
        conv5 = self.conv_conv_pool(pool4, [sfn*16, sfn*16], self.trainable, name='conv5', pool=False,
                                    padding='valid', dropout=self.dropout_rate)

        # encoding
        conv6, pool6 = self.conv_conv_pool(conv5, [sfn*8, sfn*8], self.trainable, name='encode6',
                                           padding='valid', dropout=self.dropout_rate)  # 12*12*256
        conv7, pool7 = self.conv_conv_pool(pool6, [sfn*4, sfn*4], self.trainable, name='encode7',
                                           padding='valid', dropout=self.dropout_rate)  # 4*4*128
        pool7_flat = tf.reshape(pool7, [-1, 2048])
        self.encoding = self.fc_fc(pool7_flat, [1000, self.latent_num], self.trainable, 'encode_final',
                                   activation=tf.nn.relu, dropout=False)

        self.pred = self.fc_fc(self.encoding, [100, self.class_num], self.trainable, 'encode_pred',
                               activation=None, dropout=False)
        self.output = tf.nn.softmax(self.pred)

    def make_loss(self, y_name, loss_type='xent', **kwargs):
        with tf.variable_scope('loss'):
            #pred = tf.reshape(self.pred, [-1, self.class_num])
            pred = self.pred
            #gt = tf.one_hot(self.inputs[y_name], depth=self.class_num)
            gt = self.inputs[y_name]

            if loss_type == 'xent':
                self.loss = tf.reduce_mean(tf.nn.sparse_softmax_cross_entropy_with_logits(logits=pred, labels=gt))

    def train(self, x_name, y_name, n_train, sess, summary_writer, n_valid=1000,
              train_reader=None, valid_reader=None,
              image_summary=None, verb_step=100, save_epoch=5,
              img_mean=np.array((0, 0, 0), dtype=np.float32),
              continue_dir=None, valid_iou=False):
        # define summary operations
        valid_cross_entropy_summary_op = tf.summary.scalar('xent_validation', self.valid_cross_entropy)

        if continue_dir is not None and os.path.exists(continue_dir):
            self.load_weights(continue_dir, [1, 2, 3, 4, 5])
            gs = sess.run(self.global_step)
            start_epoch = int(np.ceil(gs/n_train*self.bs))
            start_step = gs - int(start_epoch*n_train/self.bs)
        else:
            start_epoch = 0
            start_step = 0

        cross_entropy_valid_min = np.inf
        for epoch in range(start_epoch, self.epochs):
            start_time = time.time()
            for step in range(start_step, n_train, self.bs):
                X_batch, _, y_batch = train_reader.readerAction(sess)
                _, self.global_step_value = sess.run([self.optimizer, self.global_step],
                                                     feed_dict={self.inputs[x_name]:X_batch,
                                                                self.inputs[y_name]:y_batch,
                                                                self.trainable: True})
                if self.global_step_value % verb_step == 0:
                    pred_train, step_cross_entropy, step_summary = sess.run([self.pred, self.loss, self.summary],
                                                                            feed_dict={self.inputs[x_name]: X_batch,
                                                                                       self.inputs[y_name]: y_batch,
                                                                                       self.trainable: False})
                    summary_writer.add_summary(step_summary, self.global_step_value)
                    print('Epoch {:d} step {:d}\tcross entropy = {:.3f}'.
                          format(epoch, self.global_step_value, step_cross_entropy))
            # validation
            cross_entropy_valid_mean = []
            for step in range(0, n_valid, self.bs):
                X_batch_val, _, y_batch_val = valid_reader.readerAction(sess)
                pred_valid, cross_entropy_valid, iou_valid = sess.run([self.pred, self.loss, self.loss_iou],
                                                                      feed_dict={self.inputs[x_name]: X_batch_val,
                                                                                 self.inputs[y_name]: y_batch_val,
                                                                                 self.trainable: False})
                cross_entropy_valid_mean.append(cross_entropy_valid)
            cross_entropy_valid_mean = np.mean(cross_entropy_valid_mean)
            duration = time.time() - start_time
            print('Validation cross entropy: {:.3f}, duration: {:.3f}'.format(cross_entropy_valid_mean,
                                                                                  duration))
            valid_cross_entropy_summary = sess.run(valid_cross_entropy_summary_op,
                                                   feed_dict={self.valid_cross_entropy: cross_entropy_valid_mean})
            summary_writer.add_summary(valid_cross_entropy_summary, self.global_step_value)
            if cross_entropy_valid_mean < cross_entropy_valid_min:
                cross_entropy_valid_min = cross_entropy_valid_mean
                saver = tf.train.Saver(var_list=tf.global_variables(), max_to_keep=1)
                saver.save(sess, '{}/best_model.ckpt'.format(self.ckdir))

            if epoch % save_epoch == 0:
                saver = tf.train.Saver(var_list=tf.global_variables(), max_to_keep=1)
                saver.save(sess, '{}/model_{}.ckpt'.format(self.ckdir, epoch), global_step=self.global_step)

    def test(self, x_name, sess, test_iterator):
        result = []
        for X_batch in test_iterator:
            pred = sess.run(self.output, feed_dict={self.inputs[x_name]: X_batch,
                                                    self.trainable: False})
            result.append(pred)
        result = np.vstack(result)
        return result

    def run(self, train_reader=None, valid_reader=None, test_reader=None, pretrained_model_dir=None, layers2load=None,
            isTrain=False, img_mean=np.array((0, 0, 0), dtype=np.float32), verb_step=100, save_epoch=5, gpu=None,
            tile_size=(5000, 5000), patch_size=(572, 572), truth_val=1, continue_dir=None, load_epoch_num=None,
            valid_iou=False, best_model=True):
        if gpu is not None:
            os.environ['CUDA_DEVICE_ORDER'] = 'PCI_BUS_ID'
            os.environ['CUDA_VISIBLE_DEVICES'] = str(gpu)
        if isTrain:
            coord = tf.train.Coordinator()
            with tf.Session(config=self.config) as sess:
                # init model
                init = [tf.global_variables_initializer(), tf.local_variables_initializer()]
                sess.run(init)
                saver = tf.train.Saver(var_list=tf.global_variables(), max_to_keep=1)
                # load model
                if pretrained_model_dir is not None:
                    self.load_weights(pretrained_model_dir, [1, 2, 3, 4, 5])
                threads = tf.train.start_queue_runners(coord=coord, sess=sess)
                try:
                    train_summary_writer = tf.summary.FileWriter(self.ckdir, sess.graph)
                    self.train('X', 'Y', self.n_train, sess, train_summary_writer,
                               n_valid=self.n_valid, train_reader=train_reader, valid_reader=valid_reader,
                               image_summary=None, img_mean=img_mean,
                               verb_step=verb_step, save_epoch=save_epoch, continue_dir=continue_dir,
                               valid_iou=valid_iou)
                finally:
                    coord.request_stop()
                    coord.join(threads)
                    saver.save(sess, '{}/model.ckpt'.format(self.ckdir), global_step=self.global_step)
        else:
            with tf.Session() as sess:
                init = tf.global_variables_initializer()
                sess.run(init)
                self.load(pretrained_model_dir, sess, epoch=load_epoch_num)
                self.model_name = pretrained_model_dir.split('/')[-1]
                result = self.test('X', sess, test_reader)
            return result


def image_summary(image, prediction, img_mean=np.array((0, 0, 0), dtype=np.float32)):
    return np.concatenate([image+img_mean, prediction+img_mean], axis=2).astype(np.uint8)


class UnetVAE(uabMakeNetwork_UNet.UnetModel):
    def __init__(self, inputs, trainable, input_size, model_name='', dropout_rate=None,
                 learn_rate=1e-4, decay_step=60, decay_rate=0.1, epochs=100,
                 batch_size=5, start_filter_num=32, latent_num=500):
        network.Network.__init__(self, inputs, trainable, dropout_rate,
                                 learn_rate, decay_step, decay_rate, epochs, batch_size)
        self.name = 'UnetVAE'
        self.model_name = self.get_unique_name(model_name)
        self.sfn = start_filter_num
        self.learning_rate = None
        self.valid_cross_entropy = tf.placeholder(tf.float32, [])
        self.update_ops = None
        self.config = None
        self.n_train = 0
        self.n_valid = 0
        self.latent_num = latent_num
        self.valid_images = tf.placeholder(tf.uint8, shape=[None, input_size[0],
                                                            input_size[1] * 2, 3], name='validation_images')

    def sampling(self):
        epsilon = tf.random_normal(shape=(self.bs, self.latent_num), mean=self.z_mean, stddev=self.z_sigma)
        return self.z_mean + tf.exp(self.z_sigma) * epsilon

    def create_graph(self, x_name, class_num, start_filter_num=32):
        self.class_num = class_num
        sfn = self.sfn

        # downsample
        with tf.variable_scope('encoder'):
            conv1, pool1 = self.conv_conv_pool(self.inputs[x_name], [sfn, sfn], self.trainable, name='conv1',
                                               padding='same', dropout=self.dropout_rate)
            conv2, pool2 = self.conv_conv_pool(pool1, [sfn*2, sfn*2], self.trainable, name='conv2',
                                               padding='same', dropout=self.dropout_rate)
            conv3, pool3 = self.conv_conv_pool(pool2, [sfn*4, sfn*4], self.trainable, name='conv3',
                                               padding='same', dropout=self.dropout_rate)
            conv4, pool4 = self.conv_conv_pool(pool3, [sfn*6, sfn*6], self.trainable, name='conv4',
                                               padding='same', dropout=self.dropout_rate)
            conv5, pool5 = self.conv_conv_pool(pool4, [sfn*8, sfn*8], self.trainable, name='conv5',
                                               padding='same', dropout=self.dropout_rate)

            # encoding
            pool5_flat = tf.reshape(pool5, [-1, 16384])
            self.encoding = self.fc_fc(pool5_flat, [10000, 5000, self.latent_num], self.trainable, 'encode_final',
                                       activation=tf.nn.relu, dropout=False)

            self.z_mean = self.fc_fc(self.encoding, [self.latent_num], self.trainable, 'encode_z_mean',
                                     activation=None, dropout=False)
            self.z_sigma = self.fc_fc(self.encoding, [self.latent_num], self.trainable, 'encode_z_sigma',
                                     activation=None, dropout=False)
        # sampling
        with tf.variable_scope('sampling'):
            epsilon = tf.random_normal(shape=(self.bs, self.latent_num))
            z = self.z_mean + tf.multiply(self.z_sigma, epsilon)

        # decoder
        with tf.variable_scope('decoder'):
            up0 = self.fc_fc(z, [5000, 10000, 8*8*256], self.trainable, 'decode_z', activation=None, dropout=False)
            up0 = tf.reshape(up0, [-1, 8, 8, 256])

            up1 = self.upsampling_2D(up0, 'upsample_0')
            conv6 = self.conv_conv_pool(up1, [sfn * 8, sfn * 8], self.trainable, name='conv6',
                                        padding='same', dropout=self.dropout_rate, pool=False)
            up2 = self.upsampling_2D(conv6, 'upsample_1')
            conv7 = self.conv_conv_pool(up2, [sfn * 4, sfn * 4], self.trainable, name='conv7',
                                        padding='same', dropout=self.dropout_rate, pool=False)
            up3 = self.upsampling_2D(conv7, 'upsample_2')
            conv8 = self.conv_conv_pool(up3, [sfn * 2, sfn * 2], self.trainable, name='conv8',
                                        padding='same', dropout=self.dropout_rate, pool=False)
            up4 = self.upsampling_2D(conv8, 'upsample_3')
            conv9 = self.conv_conv_pool(up4, [sfn, sfn], self.trainable, name='conv9',
                                        padding='same', dropout=self.dropout_rate, pool=False)
            up5 = self.upsampling_2D(conv9, 'upsample_4')
            conv10 = self.conv_conv_pool(up5, [3], self.trainable, name='conv10',
                                            padding='same', dropout=self.dropout_rate, pool=False)
            self.pred = tf.layers.conv2d(conv10, class_num, (1, 1), name='final', activation=None, padding='same')

    def make_loss(self, y_name, loss_type='xent', **kwargs):
        with tf.variable_scope('loss'):
            _, width, height, _ = self.inputs[y_name].shape
            prediction = tf.reshape(self.pred, [-1, ])
            gt = tf.reshape(self.inputs[y_name], [-1, ])
            #xent_loss = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=prediction, labels=gt))
            xent_loss = tf.reduce_mean(tf.pow(gt - prediction, 2))
            kl_loss = -0.5 * tf.reduce_mean(1.0 + self.z_sigma - tf.square(self.z_mean) - tf.exp(self.z_sigma))
            self.loss = xent_loss + kl_loss

    def train(self, x_name, y_name, n_train, sess, summary_writer, n_valid=1000,
              train_reader=None, valid_reader=None,
              image_summary=None, verb_step=100, save_epoch=5,
              img_mean=np.array((0, 0, 0), dtype=np.float32),
              continue_dir=None, valid_iou=False):
        # define summary operations
        valid_cross_entropy_summary_op = tf.summary.scalar('xent_validation', self.valid_cross_entropy)
        valid_image_summary_op = tf.summary.image('Validation_images_summary', self.valid_images,
                                                  max_outputs=10)

        if continue_dir is not None and os.path.exists(continue_dir):
            self.load(continue_dir, sess)
            gs = sess.run(self.global_step)
            start_epoch = int(np.ceil(gs/n_train*self.bs))
            start_step = gs - int(start_epoch*n_train/self.bs)
        else:
            start_epoch = 0
            start_step = 0

        for epoch in range(start_epoch, self.epochs):
            start_time = time.time()
            for step in range(start_step, n_train, self.bs):
                X_batch, _ = train_reader.readerAction(sess)
                _, self.global_step_value = sess.run([self.optimizer, self.global_step],
                                                     feed_dict={self.inputs[x_name]:X_batch,
                                                                self.inputs[y_name]:X_batch,
                                                                self.trainable: True})
                if self.global_step_value % verb_step == 0:
                    pred_train, step_cross_entropy, step_summary = sess.run([self.pred, self.loss, self.summary],
                                                                            feed_dict={self.inputs[x_name]: X_batch,
                                                                                       self.inputs[y_name]: X_batch,
                                                                                       self.trainable: False})
                    summary_writer.add_summary(step_summary, self.global_step_value)
                    print('Epoch {:d} step {:d}\tcross entropy = {:.3f}'.
                          format(epoch, self.global_step_value, step_cross_entropy))
            # validation
            cross_entropy_valid_mean = []
            for step in range(0, n_valid, self.bs):
                X_batch_val, _ = valid_reader.readerAction(sess)
                pred_valid, cross_entropy_valid, iou_valid = sess.run([self.pred, self.loss, self.loss_iou],
                                                                      feed_dict={self.inputs[x_name]: X_batch_val,
                                                                                 self.inputs[y_name]: X_batch_val,
                                                                                 self.trainable: False})
                cross_entropy_valid_mean.append(cross_entropy_valid)
            cross_entropy_valid_mean = np.mean(cross_entropy_valid_mean)
            duration = time.time() - start_time
            print('Validation cross entropy: {:.3f}, duration: {:.3f}'.format(cross_entropy_valid_mean,
                                                                              duration))
            valid_cross_entropy_summary = sess.run(valid_cross_entropy_summary_op,
                                                   feed_dict={self.valid_cross_entropy: cross_entropy_valid_mean})
            summary_writer.add_summary(valid_cross_entropy_summary, self.global_step_value)

            if image_summary is not None:
                valid_image_summary = sess.run(valid_image_summary_op,
                                               feed_dict={self.valid_images:
                                                              image_summary(X_batch_val[:,:,:,:3], pred_valid,
                                                                            img_mean)})
                summary_writer.add_summary(valid_image_summary, self.global_step_value)

            if epoch % save_epoch == 0:
                saver = tf.train.Saver(var_list=tf.global_variables(), max_to_keep=1)
                saver.save(sess, '{}/model_{}.ckpt'.format(self.ckdir, epoch), global_step=self.global_step)

    def run(self, train_reader=None, valid_reader=None, test_reader=None, pretrained_model_dir=None, layers2load=None,
            isTrain=False, img_mean=np.array((0, 0, 0), dtype=np.float32), verb_step=100, save_epoch=5, gpu=None,
            tile_size=(5000, 5000), patch_size=(572, 572), truth_val=1, continue_dir=None, load_epoch_num=None,
            valid_iou=False, best_model=True):
        if gpu is not None:
            os.environ['CUDA_DEVICE_ORDER'] = 'PCI_BUS_ID'
            os.environ['CUDA_VISIBLE_DEVICES'] = str(gpu)
        if isTrain:
            coord = tf.train.Coordinator()
            with tf.Session(config=self.config) as sess:
                # init model
                init = [tf.global_variables_initializer(), tf.local_variables_initializer()]
                sess.run(init)
                saver = tf.train.Saver(var_list=tf.global_variables(), max_to_keep=1)
                # load model
                if pretrained_model_dir is not None:
                    if layers2load is not None:
                        self.load_weights(pretrained_model_dir, layers2load)
                    else:
                        self.load(pretrained_model_dir, sess, saver, epoch=load_epoch_num)
                threads = tf.train.start_queue_runners(coord=coord, sess=sess)
                try:
                    train_summary_writer = tf.summary.FileWriter(self.ckdir, sess.graph)
                    self.train('X', 'Y', self.n_train, sess, train_summary_writer,
                               n_valid=self.n_valid, train_reader=train_reader, valid_reader=valid_reader,
                               image_summary=image_summary, img_mean=img_mean,
                               verb_step=verb_step, save_epoch=save_epoch, continue_dir=continue_dir,
                               valid_iou=valid_iou)
                finally:
                    coord.request_stop()
                    coord.join(threads)
                    saver.save(sess, '{}/model.ckpt'.format(self.ckdir), global_step=self.global_step)


class VGGVAE(UnetVAE):
    def __init__(self, inputs, trainable, input_size, model_name='', dropout_rate=None,
                 learn_rate=1e-4, decay_step=60, decay_rate=0.1, epochs=100,
                 batch_size=5, start_filter_num=32, latent_num=500):
        network.Network.__init__(self, inputs, trainable, dropout_rate,
                                 learn_rate, decay_step, decay_rate, epochs, batch_size)
        self.name = 'VGGVAE'
        self.model_name = self.get_unique_name(model_name)
        self.sfn = start_filter_num
        self.learning_rate = None
        self.valid_cross_entropy = tf.placeholder(tf.float32, [])
        self.update_ops = None
        self.config = None
        self.n_train = 0
        self.n_valid = 0
        self.latent_num = latent_num
        self.valid_images = tf.placeholder(tf.uint8, shape=[None, input_size[0],
                                                            input_size[1] * 2, 3], name='validation_images')

    def create_graph(self, x_name, class_num, start_filter_num=32):
        self.class_num = class_num
        sfn = self.sfn

        # downsample
        with tf.variable_scope('encoder'):
            conv1 = self.conv_conv_pool(self.inputs[x_name], [sfn], self.trainable, name='conv1',
                                        conv_stride=(2, 2), padding='same', dropout=self.dropout_rate,
                                        pool=False, activation=tf.nn.relu)  # 16*128*128
            conv2 = self.conv_conv_pool(conv1, [2 * sfn], self.trainable, name='conv2',
                                        conv_stride=(2, 2), padding='same', dropout=self.dropout_rate,
                                        pool=False, activation=tf.nn.relu)  # 32*64*64
            conv3 = self.conv_conv_pool(conv2, [4 * sfn], self.trainable, name='conv3',
                                        conv_stride=(2, 2), padding='same', dropout=self.dropout_rate,
                                        pool=False, activation=tf.nn.relu)  # 64*32*32
            conv4 = self.conv_conv_pool(conv3, [8 * sfn], self.trainable, name='conv4',
                                        conv_stride=(2, 2), padding='same', dropout=self.dropout_rate,
                                        pool=False, activation=tf.nn.relu)  # 128*16*16
            conv5 = self.conv_conv_pool(conv4, [16 * sfn], self.trainable, name='conv5',
                                        conv_stride=(2, 2), padding='same', dropout=self.dropout_rate,
                                        pool=False, activation=tf.nn.relu)  # 256*8*8
            conv6 = self.conv_conv_pool(conv5, [32 * sfn], self.trainable, name='conv6',
                                        conv_stride=(2, 2), padding='same', dropout=self.dropout_rate,
                                        pool=False, activation=tf.nn.relu)  # 512*4*4

        # encoding
        with tf.variable_scope('encoding'):
            conv6_flat = tf.reshape(conv6, [-1, 32*sfn*4*4])
            self.z_mean = self.fc_fc(conv6_flat, [self.latent_num], self.trainable, 'encode_z_mean',
                                     activation=None, dropout=False)
            self.z_sigma = self.fc_fc(conv6_flat, [self.latent_num], self.trainable, 'encode_z_sigma',
                                     activation=None, dropout=False)
        # sampling
        with tf.variable_scope('sampling'):
            epsilon = tf.random_normal(shape=(self.bs, self.latent_num))
            z = self.z_mean + tf.multiply(tf.exp(self.z_sigma), epsilon)

        # decoder
        with tf.variable_scope('decoder'):
            up0 = self.fc_fc(z, [512*4*4], self.trainable, 'decode_z', activation=None, dropout=False)
            up0 = tf.reshape(up0, [-1, 4, 4, 512])

            up1 = self.upsampling_2D(up0, 'upsample_0')  # 512*8*8
            conv6 = self.conv_conv_pool(up1, [sfn * 16], self.trainable, name='conv6',
                                        padding='same', dropout=self.dropout_rate, pool=False,
                                        activation=tf.nn.relu)  # 256*8*8
            up2 = self.upsampling_2D(conv6, 'upsample_1')  # 256*16*16
            conv7 = self.conv_conv_pool(up2, [sfn * 8], self.trainable, name='conv7',
                                        padding='same', dropout=self.dropout_rate, pool=False,
                                        activation=tf.nn.relu) # 128*16*16
            up3 = self.upsampling_2D(conv7, 'upsample_2')  # 128*32*32
            conv8 = self.conv_conv_pool(up3, [sfn * 4], self.trainable, name='conv8',
                                        padding='same', dropout=self.dropout_rate, pool=False,
                                        activation=tf.nn.relu)  # 64*32*32
            up4 = self.upsampling_2D(conv8, 'upsample_3')  # 64*64*64
            conv9 = self.conv_conv_pool(up4, [sfn * 2], self.trainable, name='conv9',
                                        padding='same', dropout=self.dropout_rate, pool=False,
                                        activation=tf.nn.relu)  # 32*64*64
            up5 = self.upsampling_2D(conv9, 'upsample_4')  # 32*128*128
            conv10 = self.conv_conv_pool(up5, [sfn], self.trainable, name='conv10',
                                        padding='same', dropout=self.dropout_rate, pool=False,
                                        activation=tf.nn.relu)  # 16*128*128
            up6 = self.upsampling_2D(conv10, 'upsample_5')  # 16*256*256
            self.pred = tf.layers.conv2d(up6, class_num, (3, 3), name='final', activation=None, padding='same')

    def encoding(self, x_name, sess, test_iterator):
        for X_batch in test_iterator:
            pred = sess.run([self.z_mean, self.z_sigma], feed_dict={self.inputs[x_name]: X_batch,
                                                                    self.trainable: False})
            encoded = np.zeros(self.latent_num * 2)
            encoded[:self.latent_num] = pred[0][0, :]
            encoded[self.latent_num:] = pred[1][0, :]
            yield encoded

    def run(self, train_reader=None, valid_reader=None, test_reader=None, pretrained_model_dir=None, layers2load=None,
            isTrain=False, img_mean=np.array((0, 0, 0), dtype=np.float32), verb_step=100, save_epoch=5, gpu=None,
            tile_size=(5000, 5000), patch_size=(572, 572), truth_val=1, continue_dir=None, load_epoch_num=None,
            valid_iou=False, best_model=True):
        if gpu is not None:
            os.environ['CUDA_DEVICE_ORDER'] = 'PCI_BUS_ID'
            os.environ['CUDA_VISIBLE_DEVICES'] = str(gpu)
        if isTrain:
            coord = tf.train.Coordinator()
            with tf.Session(config=self.config) as sess:
                # init model
                init = [tf.global_variables_initializer(), tf.local_variables_initializer()]
                sess.run(init)
                saver = tf.train.Saver(var_list=tf.global_variables(), max_to_keep=1)
                # load model
                if pretrained_model_dir is not None:
                    if layers2load is not None:
                        self.load_weights(pretrained_model_dir, layers2load)
                    else:
                        self.load(pretrained_model_dir, sess, saver, epoch=load_epoch_num)
                threads = tf.train.start_queue_runners(coord=coord, sess=sess)
                try:
                    train_summary_writer = tf.summary.FileWriter(self.ckdir, sess.graph)
                    self.train('X', 'Y', self.n_train, sess, train_summary_writer,
                               n_valid=self.n_valid, train_reader=train_reader, valid_reader=valid_reader,
                               image_summary=image_summary, img_mean=img_mean,
                               verb_step=verb_step, save_epoch=save_epoch, continue_dir=continue_dir,
                               valid_iou=valid_iou)
                finally:
                    coord.request_stop()
                    coord.join(threads)
                    saver.save(sess, '{}/model.ckpt'.format(self.ckdir), global_step=self.global_step)
