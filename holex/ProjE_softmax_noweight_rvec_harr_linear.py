import argparse
import atexit
import json
import math
import os.path
import timeit
from multiprocessing import JoinableQueue, Queue, Process

import numpy as np
import tensorflow as tf


class ProjE:
    @property
    def n_entity(self):
        return self.__n_entity

    @property
    def n_train(self):
        return self.__train_triple.shape[0]

    @property
    def trainable_variables(self):
        return self.__trainable

    @property
    def hr_t(self):
        return self.__hr_t

    @property
    def tr_h(self):
        return self.__tr_h

    @property
    def train_hr_t(self):
        return self.__train_hr_t

    @property
    def train_tr_h(self):
        return self.__train_tr_h

    @property
    def ent_embedding(self):
        return self.__ent_embedding

    @property
    def rel_embedding(self):
        return self.__rel_embedding

    def training_data(self, batch_size=100):

        n_triple = len(self.__train_triple)
        rand_idx = np.random.permutation(n_triple)

        start = 0
        while start < n_triple:
            end = min(start + batch_size, n_triple)
            hr_tlist, hr_tweight, tr_hlist, tr_hweight = self.corrupted_training(
                self.__train_triple[rand_idx[start:end]])
            yield hr_tlist, hr_tweight, tr_hlist, tr_hweight
            start = end

    def raw_training_data(self, batch_size=100):
        n_triple = len(self.__train_triple)
        rand_idx = np.random.permutation(n_triple)

        start = 0
        while start < n_triple:
            end = min(start + batch_size, n_triple)
            yield self.__train_triple[rand_idx[start:end]]
            start = end

    def testing_data(self, batch_size=100):
        n_triple = len(self.__test_triple)
        start = 0
        while start < n_triple:
            end = min(start + batch_size, n_triple)
            yield self.__test_triple[start:end, :]
            start = end

    def validation_data(self, batch_size=100):
        n_triple = len(self.__valid_triple)
        start = 0
        while start < n_triple:
            end = min(start + batch_size, n_triple)
            yield self.__valid_triple[start:end, :]
            start = end

    def corrupted_training(self, htr):
        # [head(tail), relation, #of_total_positive_candidates, positive_instances..., negative_instances...]
        hr_tlist = list()
        hr_tweight = list()
        tr_hlist = list()
        tr_hweight = list()

        for idx in range(htr.shape[0]):
            if np.random.uniform(-1, 1) > 0:  # t r predict h
                tr_hweight.append(
                    [1. if x in self.__tr_h[htr[idx, 1]][htr[idx, 2]] else 0. for x in range(self.__n_entity)])
                tr_hlist.append([htr[idx, 1], htr[idx, 2]])
            else:  # h r predict t
                hr_tweight.append(
                    [1. if x in self.__hr_t[htr[idx, 0]][htr[idx, 2]] else 0. for x in range(self.__n_entity)])

                hr_tlist.append([htr[idx, 0], htr[idx, 2]])

        return np.asarray(hr_tlist, dtype=np.int32), np.asarray(hr_tweight, dtype=np.float32), \
               np.asarray(tr_hlist, dtype=np.int32), np.asarray(tr_hweight, dtype=np.float32)

    def __init__(self, data_dir, embed_dim=100, combination_method='simple', dropout=0.5, neg_weight=0.5, dc=5, dc_mtx=None):

        if combination_method.lower() not in ['simple', 'matrix']:
            raise NotImplementedError("ProjE does not support using %s as combination method." % combination_method)

        self.__combination_method = combination_method

        assert embed_dim % 2 == 0
        
        self.__embed_dim = embed_dim
        self.__initialized = False

        self.__trainable = list()
        self.__dropout = dropout

        with open(os.path.join(data_dir, 'entity2id.txt'), 'r', encoding='utf-8') as f:
            self.__n_entity = len(f.readlines())

        with open(os.path.join(data_dir, 'entity2id.txt'), 'r', encoding='utf-8') as f:
            self.__entity_id_map = {x.strip().split('\t')[0]: int(x.strip().split('\t')[1]) for x in f.readlines()}
            self.__id_entity_map = {v: k for k, v in self.__entity_id_map.items()}

        print("N_ENTITY: %d" % self.__n_entity)

        with open(os.path.join(data_dir, 'relation2id.txt'), 'r', encoding='utf-8') as f:
            self.__n_relation = len(f.readlines())

        with open(os.path.join(data_dir, 'relation2id.txt'), 'r', encoding='utf-8') as f:
            self.__relation_id_map = {x.strip().split('\t')[0]: int(x.strip().split('\t')[1]) for x in f.readlines()}
            self.__id_relation_map = {v: k for k, v in self.__entity_id_map.items()}

        print("N_RELATION: %d" % self.__n_relation)

        def load_triple(file_path):
            with open(file_path, 'r', encoding='utf-8') as f_triple:
                return np.asarray([[self.__entity_id_map[x.strip().split('\t')[0]],
                                    self.__entity_id_map[x.strip().split('\t')[1]],
                                    self.__relation_id_map[x.strip().split('\t')[2]]] for x in f_triple.readlines()],
                                  dtype=np.int32)

        def gen_hr_t(triple_data):
            hr_t = dict()
            for h, t, r in triple_data:
                if h not in hr_t:
                    hr_t[h] = dict()
                if r not in hr_t[h]:
                    hr_t[h][r] = set()
                hr_t[h][r].add(t)

            return hr_t

        def gen_tr_h(triple_data):
            tr_h = dict()
            for h, t, r in triple_data:
                if t not in tr_h:
                    tr_h[t] = dict()
                if r not in tr_h[t]:
                    tr_h[t][r] = set()
                tr_h[t][r].add(h)
            return tr_h

        self.__train_triple = load_triple(os.path.join(data_dir, 'train.txt'))
        print("N_TRAIN_TRIPLES: %d" % self.__train_triple.shape[0])

        self.__test_triple = load_triple(os.path.join(data_dir, 'test.txt'))
        print("N_TEST_TRIPLES: %d" % self.__test_triple.shape[0])

        self.__valid_triple = load_triple(os.path.join(data_dir, 'valid.txt'))
        print("N_VALID_TRIPLES: %d" % self.__valid_triple.shape[0])

        self.__train_hr_t = gen_hr_t(self.__train_triple)
        self.__train_tr_h = gen_tr_h(self.__train_triple)
        self.__test_hr_t = gen_hr_t(self.__test_triple)
        self.__test_tr_h = gen_tr_h(self.__test_triple)

        self.__hr_t = gen_hr_t(np.concatenate([self.__train_triple, self.__test_triple, self.__valid_triple], axis=0))
        self.__tr_h = gen_tr_h(np.concatenate([self.__train_triple, self.__test_triple, self.__valid_triple], axis=0))

        bound = 6 / math.sqrt(embed_dim)

        with tf.device('/cpu'):
            self.__ent_embedding = tf.get_variable("ent_embedding", [self.__n_entity, embed_dim],
                                                   initializer=tf.random_uniform_initializer(minval=-bound,
                                                                                             maxval=bound,
                                                                                             seed=345))
            self.__trainable.append(self.__ent_embedding)

            self.__rel_embedding = tf.get_variable("rel_embedding", [self.__n_relation, embed_dim],
                                                   initializer=tf.random_uniform_initializer(minval=-bound,
                                                                                             maxval=bound,
                                                                                             seed=346))
            self.__trainable.append(self.__rel_embedding)

            if combination_method.lower() == 'simple':
                # self.__hr_weighted_vector = tf.get_variable("simple_hr_combination_weights", [embed_dim * 2],
                #                                             initializer=tf.random_uniform_initializer(minval=-bound,
                #                                                                                       maxval=bound,
                #                                                                                       seed=445))
                # self.__tr_weighted_vector = tf.get_variable("simple_tr_combination_weights", [embed_dim * 2],
                #                                             initializer=tf.random_uniform_initializer(minval=-bound,
                #                                                                                       maxval=bound,
                #                                                                                       seed=445))
                # self.__trainable.append(self.__hr_weighted_vector)
                # self.__trainable.append(self.__tr_weighted_vector)
                self.__hr_combination_bias = tf.get_variable("combination_bias_hr",
                                                             initializer=tf.zeros([embed_dim]))
                self.__tr_combination_bias = tf.get_variable("combination_bias_tr",
                                                             initializer=tf.zeros([embed_dim]))

                self.__trainable.append(self.__hr_combination_bias)
                self.__trainable.append(self.__tr_combination_bias)

                print('dc= %d' % dc)
                self.__dc = abs(dc)
                self.__dc_mtx = dc_mtx
                self.__hr_rvec = tf.get_variable("rand_row_vector_hr", 
                                                 initializer=tf.constant(dc_mtx[:self.__dc, :], dtype=tf.float32))
                self.__tr_rvec = tf.get_variable("rand_row_vector_tr",  
                                                 initializer=tf.constant(dc_mtx[:self.__dc, :], dtype=tf.float32))

            else:
                self.__hr_combination_matrix = tf.get_variable("matrix_hr_combination_layer",
                                                               [embed_dim * 2, embed_dim],
                                                               initializer=tf.random_uniform_initializer(minval=-bound,
                                                                                                         maxval=bound,
                                                                                                         seed=555))
                self.__tr_combination_matrix = tf.get_variable("matrix_tr_combination_layer",
                                                               [embed_dim * 2, embed_dim],
                                                               initializer=tf.random_uniform_initializer(minval=-bound,
                                                                                                         maxval=bound,
                                                                                                         seed=555))
                self.__trainable.append(self.__hr_combination_matrix)
                self.__trainable.append(self.__tr_combination_matrix)
                self.__hr_combination_bias = tf.get_variable("combination_bias_hr",
                                                             initializer=tf.zeros([embed_dim]))
                self.__tr_combination_bias = tf.get_variable("combination_bias_tr",
                                                             initializer=tf.zeros([embed_dim]))

                self.__trainable.append(self.__hr_combination_bias)
                self.__trainable.append(self.__tr_combination_bias)

    @staticmethod
    def __l1_normalize(x, dim, epsilon=1e-12, name=None):
        square_sum = tf.reduce_sum(tf.abs(x), [dim], keep_dims=True)
        x_inv_norm = tf.rsqrt(tf.maximum(square_sum, epsilon))
        return tf.mul(x, x_inv_norm, name=name)

    @staticmethod
    def sampled_softmax(tensor, weights):
        max_val = tf.reduce_max(tensor * tf.abs(weights), 1, keep_dims=True)
        tensor_rescaled = tensor - max_val
        tensor_exp = tf.exp(tensor_rescaled)
        tensor_sum = tf.reduce_sum(tensor_exp * tf.abs(weights), 1, keep_dims=True)

        return (tensor_exp / tensor_sum) * tf.abs(weights)  # all ignored elements will have a prob of 0.

    def train(self, inputs, regularizer_weight=1., scope=None):
        with tf.variable_scope(scope or type(self).__name__) as scp:
            if self.__initialized:
                scp.reuse_variables()
            rel_embedding = self.__rel_embedding
            normalized_ent_embedding = self.__ent_embedding

            hr_tlist, hr_tlist_weight, tr_hlist, tr_hlist_weight = inputs

            # (?, dim)
            hr_tlist_h = tf.nn.embedding_lookup(normalized_ent_embedding, hr_tlist[:, 0])
            hr_tlist_r = tf.nn.embedding_lookup(rel_embedding, hr_tlist[:, 1])
            # (?, dim)
            tr_hlist_t = tf.nn.embedding_lookup(normalized_ent_embedding, tr_hlist[:, 0])
            tr_hlist_r = tf.nn.embedding_lookup(rel_embedding, tr_hlist[:, 1])

            if self.__combination_method.lower() == 'simple':

                # shape (?, dim)
                # hr_tlist_hr = hr_tlist_h * self.__hr_weighted_vector[
                #                            :self.__embed_dim] + hr_tlist_r * self.__hr_weighted_vector[
                #                                                              self.__embed_dim:]
                ent_embedding = tf.expand_dims(self.__ent_embedding, 0)
                ent_embedding = tf.tile(ent_embedding, [self.__dc, 1, 1])

                ###
                shp_hr_tlist_h = tf.shape(hr_tlist_h)
                
                hr_tlist_h = tf.expand_dims(hr_tlist_h, 0)
                hr_tlist_h = tf.tile(hr_tlist_h, [self.__dc, 1, 1])

                hr_tlist_r = tf.expand_dims(hr_tlist_r, 0)
                hr_tlist_r = tf.tile(hr_tlist_r, [self.__dc, 1, 1])
                
                hr_rvec = tf.expand_dims(self.__hr_rvec, 1)
                hr_rvec = tf.tile(hr_rvec, [1, shp_hr_tlist_h[0], 1])
                hr_tlist_r = tf.multiply(hr_tlist_r, hr_rvec)
                
                fft_hr_tlist_h = tf.spectral.rfft(hr_tlist_h)
                fft_hr_tlist_r = tf.spectral.rfft(hr_tlist_r)

                # shape is (dc, batch_num, embed_dim)
                hr_tlist_hr = tf.spectral.irfft( tf.multiply(tf.conj(fft_hr_tlist_h), fft_hr_tlist_r) )

                hrt_res = tf.matmul(tf.nn.dropout((hr_tlist_hr + self.__hr_combination_bias), self.__dropout),
                                    ent_embedding,
                                    transpose_b=True)
                hrt_res = tf.reduce_mean(hrt_res, 0)

                # tr_hlist_tr = tr_hlist_t * self.__tr_weighted_vector[
                #                            :self.__embed_dim] + tr_hlist_r * self.__tr_weighted_vector[
                #                                                              self.__embed_dim:]

                ###
                shp_tr_hlist_t = tf.shape(tr_hlist_t)
                
                tr_hlist_t = tf.expand_dims(tr_hlist_t, 0)
                tr_hlist_t = tf.tile(tr_hlist_t, [self.__dc, 1, 1])
                
                tr_hlist_r = tf.expand_dims(tr_hlist_r, 0)
                tr_hlist_r = tf.tile(tr_hlist_r, [self.__dc, 1, 1])
                
                tr_rvec = tf.expand_dims(self.__tr_rvec, 1)
                tr_rvec = tf.tile(tr_rvec, [1, shp_tr_hlist_t[0], 1])
                tr_hlist_r = tf.multiply(tr_hlist_r, tr_rvec)
                
                fft_tr_hlist_t = tf.spectral.rfft(tr_hlist_t)
                fft_tr_hlist_r = tf.spectral.rfft(tr_hlist_r)
                
                tr_hlist_tr = tf.spectral.irfft( tf.multiply(tf.conj(fft_tr_hlist_t), fft_tr_hlist_r) )

                trh_res = tf.matmul(tf.nn.dropout((tr_hlist_tr + self.__tr_combination_bias), self.__dropout),
                                    ent_embedding,
                                    transpose_b=True)
                trh_res = tf.reduce_mean(trh_res, 0)

                self.regularizer_loss = regularizer_loss = tf.reduce_sum(tf.abs(self.__ent_embedding)) + tf.reduce_sum(
                    tf.abs(self.__rel_embedding))
                # #tf.reduce_sum(
                #     tf.abs(self.__hr_weighted_vector)) + tf.reduce_sum(tf.abs(
                #     self.__tr_weighted_vector)) + 

            else:

                hr_tlist_hr = tf.nn.dropout(tf.tanh(tf.matmul(tf.concat(1, [hr_tlist_h, hr_tlist_r]),
                                                              self.__hr_combination_matrix) + self.__hr_combination_bias),
                                            self.__dropout)

                hrt_res = tf.matmul(hr_tlist_hr, self.__ent_embedding, transpose_b=True)

                tr_hlist_tr = tf.nn.dropout(tf.tanh(tf.matmul(tf.concat(1, [tr_hlist_t, tr_hlist_r]),
                                                              self.__tr_combination_matrix) + self.__tr_combination_bias),
                                            self.__dropout)

                trh_res = tf.matmul(tr_hlist_tr, self.__ent_embedding, transpose_b=True)

                self.regularizer_loss = regularizer_loss = tf.reduce_sum(
                    tf.abs(self.__hr_combination_matrix)) + tf.reduce_sum(tf.abs(
                    self.__tr_combination_matrix)) + tf.reduce_sum(tf.abs(self.__ent_embedding)) + tf.reduce_sum(
                    tf.abs(self.__rel_embedding))

            self.hrt_softmax = hrt_res_softmax = self.sampled_softmax(hrt_res, hr_tlist_weight)

            hrt_loss = -tf.reduce_sum(
                tf.log(tf.clip_by_value(hrt_res_softmax, 1e-10, 1.0)) * tf.maximum(0.,
                                                                                   hr_tlist_weight) / tf.reduce_sum(
                    tf.maximum(0., hr_tlist_weight), 1, keep_dims=True))

            self.trh_softmax = trh_res_softmax = self.sampled_softmax(trh_res, tr_hlist_weight)
            trh_loss = -tf.reduce_sum(
                tf.log(tf.clip_by_value(trh_res_softmax, 1e-10, 1.0)) * tf.maximum(0., tr_hlist_weight) / tf.reduce_sum(
                    tf.maximum(0., tr_hlist_weight), 1, keep_dims=True))
            return hrt_loss + trh_loss + regularizer_loss * regularizer_weight

    def test(self, inputs, scope=None):
        with tf.variable_scope(scope or type(self).__name__) as scp:
            scp.reuse_variables()

            h = tf.nn.embedding_lookup(self.__ent_embedding, inputs[:, 0])
            t = tf.nn.embedding_lookup(self.__ent_embedding, inputs[:, 1])
            r = tf.nn.embedding_lookup(self.__rel_embedding, inputs[:, 2])

            ent_mat = tf.transpose(self.__ent_embedding)
            ent_mat = tf.expand_dims(ent_mat, 0)
            ent_mat = tf.tile(ent_mat, [self.__dc, 1, 1])

            if self.__combination_method.lower() == 'simple':

                # predict tails
                # hr = h * self.__hr_weighted_vector[:self.__embed_dim] + r * self.__hr_weighted_vector[
                #                                                             self.__embed_dim:]
                shp_h = tf.shape(h)
                
                h = tf.expand_dims(h, 0)
                h = tf.tile(h, [self.__dc, 1, 1])

                hr = tf.expand_dims(r, 0)
                hr = tf.tile(hr, [self.__dc, 1, 1])
                
                hr_rvec = tf.expand_dims(self.__hr_rvec, 1)
                hr_rvec = tf.tile(hr_rvec, [1, shp_h[0], 1])
                hr = tf.multiply(hr, hr_rvec)
                                
                fft_h = tf.spectral.rfft(h)
                fft_hr = tf.spectral.rfft(hr)
                hr = tf.spectral.irfft( tf.multiply(tf.conj(fft_h), fft_hr) )

                hrt_res = tf.matmul((hr + self.__hr_combination_bias), ent_mat)
                hrt_res = tf.reduce_mean(hrt_res, 0)
                _, tail_ids = tf.nn.top_k(hrt_res, k=self.__n_entity)

                # predict heads
                # tr = t * self.__tr_weighted_vector[:self.__embed_dim] + r * self.__tr_weighted_vector[self.__embed_dim:]
                shp_t = tf.shape(t)
                
                t = tf.expand_dims(t, 0)
                t = tf.tile(t, [self.__dc, 1, 1])

                tr = tf.expand_dims(r, 0)
                tr = tf.tile(tr, [self.__dc, 1, 1])

                tr_rvec = tf.expand_dims(self.__tr_rvec, 1)
                tr_rvec = tf.tile(tr_rvec, [1, shp_t[0], 1])
                tr = tf.multiply(tr, tr_rvec)
                
                fft_t = tf.spectral.rfft(t)
                fft_tr = tf.spectral.rfft(tr)
                tr = tf.spectral.irfft( tf.multiply(tf.conj(fft_t), fft_tr) )

                trh_res = tf.matmul((tr + self.__tr_combination_bias), ent_mat)
                trh_res = tf.reduce_mean(trh_res, 0)
                _, head_ids = tf.nn.top_k(trh_res, k=self.__n_entity)

            else:

                hr = tf.matmul(tf.concat(1, [h, r]), self.__hr_combination_matrix)
                hrt_res = (tf.matmul(tf.tanh(hr + self.__hr_combination_bias), ent_mat))
                _, tail_ids = tf.nn.top_k(hrt_res, k=self.__n_entity)

                tr = tf.matmul(tf.concat(1, [t, r]), self.__tr_combination_matrix)
                trh_res = (tf.matmul(tf.tanh(tr + self.__tr_combination_bias), ent_mat))

                _, head_ids = tf.nn.top_k(trh_res, k=self.__n_entity)

            return head_ids, tail_ids


def train_ops(model: ProjE, learning_rate=0.1, optimizer_str='gradient', regularizer_weight=1.0):
    with tf.device('/cpu'):
        train_hrt_input = tf.placeholder(tf.int32, [None, 2])
        train_hrt_weight = tf.placeholder(tf.float32, [None, model.n_entity])
        train_trh_input = tf.placeholder(tf.int32, [None, 2])
        train_trh_weight = tf.placeholder(tf.float32, [None, model.n_entity])

        loss = model.train([train_hrt_input, train_hrt_weight, train_trh_input, train_trh_weight],
                           regularizer_weight=regularizer_weight)
        if optimizer_str == 'gradient':
            optimizer = tf.train.GradientDescentOptimizer(learning_rate=learning_rate)
        elif optimizer_str == 'rms':
            optimizer = tf.train.RMSPropOptimizer(learning_rate=learning_rate)
        elif optimizer_str == 'adam':
            optimizer = tf.train.AdamOptimizer(learning_rate=learning_rate)
        else:
            raise NotImplementedError("Does not support %s optimizer" % optimizer_str)

        grads = optimizer.compute_gradients(loss, model.trainable_variables)

        op_train = optimizer.apply_gradients(grads)

        return train_hrt_input, train_hrt_weight, train_trh_input, train_trh_weight, loss, op_train


def test_ops(model: ProjE):
    with tf.device('/cpu'):
        test_input = tf.placeholder(tf.int32, [None, 3])
        head_ids, tail_ids = model.test(test_input)

    return test_input, head_ids, tail_ids


def worker_func(in_queue: JoinableQueue, out_queue: Queue, hr_t, tr_h):
    while True:
        dat = in_queue.get()
        if dat is None:
            in_queue.task_done()
            continue
        testing_data, head_pred, tail_pred = dat
        out_queue.put(test_evaluation(testing_data, head_pred, tail_pred, hr_t, tr_h))
        in_queue.task_done()


def kill_process(process):
    process.terminate()


def data_generator_func(in_queue: JoinableQueue, out_queue: Queue, tr_h, hr_t, n_entity, neg_weight):
    while True:
        dat = in_queue.get()
        if dat is None:
            break
        # [head(tail), relation, #of_total_positive_candidates, positive_instances..., negative_instances...]
        hr_tlist = list()
        hr_tweight = list()
        tr_hlist = list()
        tr_hweight = list()

        htr = dat

        for idx in range(htr.shape[0]):
            if np.random.uniform(-1, 1) > 0:  # t r predict h
                tr_hweight.append(
                    [1. if x in tr_h[htr[idx, 1]][htr[idx, 2]] else y for
                     x, y in enumerate(np.random.choice([0., -1.], size=n_entity, p=[1 - neg_weight, neg_weight]))])
                tr_hlist.append([htr[idx, 1], htr[idx, 2]])
            else:  # h r predict t
                hr_tweight.append(
                    [1. if x in hr_t[htr[idx, 0]][htr[idx, 2]] else y for
                     x, y in enumerate(np.random.choice([0., -1.], size=n_entity, p=[1 - neg_weight, neg_weight]))])

                hr_tlist.append([htr[idx, 0], htr[idx, 2]])

        out_queue.put((np.asarray(hr_tlist, dtype=np.int32), np.asarray(hr_tweight, dtype=np.float32),
                       np.asarray(tr_hlist, dtype=np.int32), np.asarray(tr_hweight, dtype=np.float32)))


def test_evaluation(testing_data, head_pred, tail_pred, hr_t, tr_h):
    assert len(testing_data) == len(head_pred)
    assert len(testing_data) == len(tail_pred)

    mean_rank_h = list()
    mean_rank_t = list()
    filtered_mean_rank_h = list()
    filtered_mean_rank_t = list()

    for i in range(len(testing_data)):
        h = testing_data[i, 0]
        t = testing_data[i, 1]
        r = testing_data[i, 2]
        # mean rank

        mr = 0
        for val in head_pred[i]:
            if val == h:
                mean_rank_h.append(mr)
                break
            mr += 1

        mr = 0
        for val in tail_pred[i]:
            if val == t:
                mean_rank_t.append(mr)
            mr += 1

        # filtered mean rank
        fmr = 0
        for val in head_pred[i]:
            if val == h:
                filtered_mean_rank_h.append(fmr)
                break
            if t in tr_h and r in tr_h[t] and val in tr_h[t][r]:
                continue
            else:
                fmr += 1

        fmr = 0
        for val in tail_pred[i]:
            if val == t:
                filtered_mean_rank_t.append(fmr)
                break
            if h in hr_t and r in hr_t[h] and val in hr_t[h][r]:
                continue
            else:
                fmr += 1

    return (mean_rank_h, filtered_mean_rank_h), (mean_rank_t, filtered_mean_rank_t)

def gen_haar_mtx(k):
    H = np.array([[1., 1.], [1., -1.]])
    n = 2
    for i in iter(range(1, k)):
        H1 = np.kron(H, [1., 1.])
        H2 = np.kron(np.identity(n), [1., -1.])
        # print('H1')
        # print(H1)
        # print('H2')
        # print(H2)
        n *= 2
        H = np.vstack((H1, H2))
    return H

def gen_rand_01(n, m):
    return np.random.randint(2, size=(n, m))

def gen_rand_pn1(n, m):
    a = np.random.randint(2, size=(n, m))
    return 2*a - 1

def gen_rand_all1(n, m):
    return np.ones((n, m))

def gen_trunc_eye(n, m):
    return np.eye(n, m)

def gen_linearly_independent_vectors(n,m):
    a = np.random.randint(2,size=(1,m))
    i = 1
    while i < n :
        i = i + 1
        b = np.random.randint(2,size=(1,m))
        b = np.vstack((a,b))
        r = np.linalg.matrix_rank(b)
        while r < i :
            b = np.random.randint(2, size=(1, m))
            b = np.vstack((a, b))
            r = np.linalg.matrix_rank(b)
        a = b
    print('dc = %d' % n)
    print('dim = %d' % m)
    print('rank = %d' % np.linalg.matrix_rank(a))
    return a

def main(_):
    parser = argparse.ArgumentParser(description='ProjE.')
    parser.add_argument('--data', dest='data_dir', type=str, help="Data folder", default='./data/FB15k/')
    parser.add_argument('--lr', dest='lr', type=float, help="Learning rate", default=0.01)
    parser.add_argument("--dim", dest='dim', type=int, help="Embedding dimension", default=200)
    parser.add_argument("--dc", dest='dc', type=int, help="The number of random row vectors", default=5)
    parser.add_argument("--batch", dest='batch', type=int, help="Batch size", default=100)
    parser.add_argument("--comb", dest="combination_method", type=str, help="Combination method", default='simple')
    parser.add_argument("--worker", dest='n_worker', type=int, help="Evaluation worker", default=3)
    parser.add_argument("--generator", dest='n_generator', type=int, help="Data generator", default=10)
    parser.add_argument("--eval_batch", dest="eval_batch", type=int, help="Evaluation batch size", default=500)
    parser.add_argument("--save_dir", dest='save_dir', type=str, help="Model path", default='./')
    parser.add_argument("--load_model", dest='load_model', type=str, help="Model file", default="")
    parser.add_argument("--save_per", dest='save_per', type=int, help="Save per x iteration", default=10)
    parser.add_argument("--eval_per", dest='eval_per', type=int, help="Evaluate every x iteration", default=1)
    parser.add_argument("--max_iter", dest='max_iter', type=int, help="Max iteration", default=100)
    parser.add_argument("--summary_dir", dest='summary_dir', type=str, help="summary directory",
                        default='./ProjE_summary/')
    parser.add_argument("--keep", dest='drop_out', type=float, help="Keep prob (1.0 keep all, 0. drop all)",
                        default=0.5)
    parser.add_argument("--optimizer", dest='optimizer', type=str, help="Optimizer", default='adam')
    parser.add_argument("--prefix", dest='prefix', type=str, help="model_prefix", default='DEFAULT')
    parser.add_argument("--loss_weight", dest='loss_weight', type=float, help="Weight on parameter loss", default=1e-5)
    parser.add_argument("--neg_weight", dest='neg_weight', type=float, help="Sampling weight on negative examples",
                        default=0.5)
    parser.add_argument("--haar", dest="haar", type=int, help="5: linearly independent random 0-1 vectors. 4: truncated identity matrix. 3: all 1 matrix. 2: use random -1 +1 matrix. 1: use haar matrix; 0: use random 0-1 matrix.")

    args = parser.parse_args()

    print(args)

    if args.haar == 1:
        haar = gen_haar_mtx(8)
        assert haar.shape[1] == 256
        assert args.dim == 256
    elif args.haar == 2:
        haar = gen_rand_pn1(args.dc, args.dim)
    elif args.haar == 3:
        haar = gen_rand_all1(args.dc, args.dim)
    elif args.haar == 4:
        haar = gen_trunc_eye(args.dc, args.dim)
    elif args.haar == 5:
        haar = gen_linearly_independent_vectors(args.dc,args.dim)
    else:
        haar = gen_rand_01(args.dc, args.dim)

    model = ProjE(args.data_dir, embed_dim=args.dim, combination_method=args.combination_method,
                  dropout=args.drop_out, neg_weight=args.neg_weight, dc=args.dc, dc_mtx=haar)

    train_hrt_input, train_hrt_weight, train_trh_input, train_trh_weight, \
    train_loss, train_op = train_ops(model, learning_rate=args.lr,
                                     optimizer_str=args.optimizer,
                                     regularizer_weight=args.loss_weight)
    test_input, test_head, test_tail = test_ops(model)

    with tf.Session() as session:
        tf.initialize_all_variables().run()

        saver = tf.train.Saver()

        iter_offset = 0

        if args.load_model is not None and os.path.exists(args.load_model+'.index'):
            saver.restore(session, args.load_model)
            iter_offset = int(args.load_model.split('.')[-2].split('_')[-1]) + 1
            print("Load model from %s, iteration %d restored." % (args.load_model, iter_offset))

        total_inst = model.n_train

        # training data generator
        raw_training_data_queue = Queue()
        training_data_queue = Queue()
        data_generators = list()
        for i in range(args.n_generator):
            data_generators.append(Process(target=data_generator_func, args=(
                raw_training_data_queue, training_data_queue, model.train_tr_h, model.train_hr_t, model.n_entity, args.neg_weight)))
            data_generators[-1].start()
            atexit.register(kill_process, data_generators[-1])

        evaluation_queue = JoinableQueue()
        result_queue = Queue()
        for i in range(args.n_worker):
            worker = Process(target=worker_func, args=(evaluation_queue, result_queue, model.hr_t, model.tr_h))
            worker.start()
            atexit.register(kill_process, worker)

        # to capture final metrics after all iterations
        iterations_final = -1
        accu_filtered_mean_rank_final_h = list()
        accu_filtered_mean_rank_final_t = list()

        for data_func, test_type in zip([model.validation_data, model.testing_data], ['VALID', 'TEST']):
            accu_mean_rank_h = list()
            accu_mean_rank_t = list()
            accu_filtered_mean_rank_h = list()
            accu_filtered_mean_rank_t = list()

            evaluation_count = 0

            for testing_data in data_func(batch_size=args.eval_batch):
                head_pred, tail_pred = session.run([test_head, test_tail],
                                                   {test_input: testing_data})

                evaluation_queue.put((testing_data, head_pred, tail_pred))
                evaluation_count += 1

            for i in range(args.n_worker):
                evaluation_queue.put(None)

            print("waiting for worker finishes their work")
            evaluation_queue.join()
            print("all worker stopped.")
            while evaluation_count > 0:
                evaluation_count -= 1

                (mrh, fmrh), (mrt, fmrt) = result_queue.get()
                accu_mean_rank_h += mrh
                accu_mean_rank_t += mrt
                accu_filtered_mean_rank_h += fmrh
                accu_filtered_mean_rank_t += fmrt

            print(
                "[%s] INITIALIZATION [HEAD PREDICTION] MEAN RANK: %.1f FILTERED MEAN RANK %.1f HIT@10 %.3f FILTERED HIT@10 %.3f" %
                (test_type, np.mean(accu_mean_rank_h), np.mean(accu_filtered_mean_rank_h),
                 np.mean(np.asarray(accu_mean_rank_h, dtype=np.int32) < 10),
                 np.mean(np.asarray(accu_filtered_mean_rank_h, dtype=np.int32) < 10)))
            print(
                "[%s] INITIALIZATION                   MRR: %.3f FILTERED MRR %.3f" %
                (test_type, np.mean(np.reciprocal([x+1 for x in accu_mean_rank_h])), np.mean(np.reciprocal([x+1 for x in accu_filtered_mean_rank_h]))))

            print(
                "[%s] INITIALIZATION [TAIL PREDICTION] MEAN RANK: %.1f FILTERED MEAN RANK %.1f HIT@10 %.3f FILTERED HIT@10 %.3f" %
                (test_type, np.mean(accu_mean_rank_t), np.mean(accu_filtered_mean_rank_t),
                 np.mean(np.asarray(accu_mean_rank_t, dtype=np.int32) < 10),
                 np.mean(np.asarray(accu_filtered_mean_rank_t, dtype=np.int32) < 10)))
            print(
                "[%s] INITIALIZATION                   MRR: %.3f FILTERED MRR %.3f" %
                (test_type, np.mean(np.reciprocal([x+1 for x in accu_mean_rank_t])), np.mean(np.reciprocal([x+1 for x in accu_filtered_mean_rank_t]))))

            print('WARNING: MEAN RANK numbers are 0-based')

            if test_type == 'TEST':
                # store results in global lists
                iterations_final = 0
                accu_filtered_mean_rank_final_h = list(accu_filtered_mean_rank_h)
                accu_filtered_mean_rank_final_t = list(accu_filtered_mean_rank_t)

        iteration_start_time = timeit.default_timer()
        for n_iter in range(iter_offset, args.max_iter):
            start_time = timeit.default_timer()
            accu_loss = 0.
            accu_re_loss = 0.
            ninst = 0

            print("initializing raw training data...")
            nbatches_count = 0
            for dat in model.raw_training_data(batch_size=args.batch):
                raw_training_data_queue.put(dat)
                nbatches_count += 1
            print("raw training data initialized.")

            while nbatches_count > 0:
                nbatches_count -= 1

                hr_tlist, hr_tweight, tr_hlist, tr_hweight = training_data_queue.get()

                l, rl, _ = session.run(
                    [train_loss, model.regularizer_loss, train_op], {train_hrt_input: hr_tlist,
                                                                     train_hrt_weight: hr_tweight,
                                                                     train_trh_input: tr_hlist,
                                                                     train_trh_weight: tr_hweight})

                accu_loss += l
                accu_re_loss += rl
                ninst += len(hr_tlist) + len(tr_hlist)

                if ninst % (5000) is not None:
                    print(
                        '[%d sec](%d/%d) : %.2f -- loss : %.5f rloss: %.5f ' % (
                            timeit.default_timer() - start_time, ninst, total_inst, float(ninst) / total_inst,
                            l / (len(hr_tlist) + len(tr_hlist)),
                            args.loss_weight * (rl / (len(hr_tlist) + len(tr_hlist)))),
                        end='\r')
            print("")
            print("iter %d avg loss %.5f, time %.3f" % (n_iter, accu_loss / ninst, timeit.default_timer() - start_time))

            if n_iter % args.save_per == 0 or n_iter == args.max_iter - 1:
                save_path = saver.save(session,
                                       os.path.join(args.save_dir,
                                                    "ProjE_" + str(args.prefix) + "_" + str(n_iter) + ".ckpt"))
                print("Model saved at %s" % save_path)

            if n_iter % args.eval_per == 0 or n_iter == args.max_iter - 1:

                for data_func, test_type in zip([model.validation_data, model.testing_data], ['VALID', 'TEST']):
                    accu_mean_rank_h = list()
                    accu_mean_rank_t = list()
                    accu_filtered_mean_rank_h = list()
                    accu_filtered_mean_rank_t = list()

                    evaluation_count = 0

                    for testing_data in data_func(batch_size=args.eval_batch):
                        head_pred, tail_pred = session.run([test_head, test_tail],
                                                           {test_input: testing_data})

                        evaluation_queue.put((testing_data, head_pred, tail_pred))
                        evaluation_count += 1

                    for i in range(args.n_worker):
                        evaluation_queue.put(None)

                    print("waiting for worker finishes their work")
                    evaluation_queue.join()
                    print("all worker stopped.")
                    while evaluation_count > 0:
                        evaluation_count -= 1

                        (mrh, fmrh), (mrt, fmrt) = result_queue.get()
                        accu_mean_rank_h += mrh
                        accu_mean_rank_t += mrt
                        accu_filtered_mean_rank_h += fmrh
                        accu_filtered_mean_rank_t += fmrt

                    print(
                        "[%s] ITER %d [HEAD PREDICTION] MEAN RANK: %.1f FILTERED MEAN RANK %.1f HIT@10 %.3f FILTERED HIT@10 %.3f" %
                        (test_type, n_iter, np.mean(accu_mean_rank_h), np.mean(accu_filtered_mean_rank_h),
                         np.mean(np.asarray(accu_mean_rank_h, dtype=np.int32) < 10),
                         np.mean(np.asarray(accu_filtered_mean_rank_h, dtype=np.int32) < 10)))
                    print(
                        "[%s] ITER %d                   MRR: %.3f FILTERED MRR %.3f" %
                        (test_type, n_iter, np.mean(np.reciprocal([x+1 for x in accu_mean_rank_h])), np.mean(np.reciprocal([x+1 for x in accu_filtered_mean_rank_h]))))

                    print(
                        "[%s] ITER %d [TAIL PREDICTION] MEAN RANK: %.1f FILTERED MEAN RANK %.1f HIT@10 %.3f FILTERED HIT@10 %.3f" %
                        (test_type, n_iter, np.mean(accu_mean_rank_t), np.mean(accu_filtered_mean_rank_t),
                         np.mean(np.asarray(accu_mean_rank_t, dtype=np.int32) < 10),
                         np.mean(np.asarray(accu_filtered_mean_rank_t, dtype=np.int32) < 10)))

                    print(
                        "[%s] ITER %d                   MRR: %.3f FILTERED MRR %.3f" %
                        (test_type, n_iter, np.mean(np.reciprocal([x+1 for x in accu_mean_rank_t])), np.mean(np.reciprocal([x+1 for x in accu_filtered_mean_rank_t]))))

                    print('WARNING: MEAN RANK numbers are 0-based')

                    if test_type == 'TEST':
                        # store results in global lists
                        iterations_final = n_iter + 1  # adjust to start with 1, with 0 being the initialization
                        accu_filtered_mean_rank_final_h = list(accu_filtered_mean_rank_h)
                        accu_filtered_mean_rank_final_t = list(accu_filtered_mean_rank_t)

        # output final results to metrics.json
        print('Writing final results to metrics.json')
        time_per_iteration = round((timeit.default_timer() - iteration_start_time) / iterations_final) if iterations_final > 0 else 0  # in seconds
        filtered_mean_rank_h = np.mean(accu_filtered_mean_rank_final_h)
        filtered_mean_rank_t = np.mean(accu_filtered_mean_rank_final_t)
        filtered_hits10_h = np.mean(np.asarray(accu_filtered_mean_rank_final_h, dtype=np.int32) < 10)
        filtered_hits10_t = np.mean(np.asarray(accu_filtered_mean_rank_final_t, dtype=np.int32) < 10)
        metrics = {
            'iterations'           : iterations_final,
            'time_per_iteration'   : time_per_iteration,
            'filtered_mean_rank'   : np.mean([filtered_mean_rank_h, filtered_mean_rank_t]),
            'filtered_mean_rank_h' : filtered_mean_rank_h,
            'filtered_mean_rank_t' : filtered_mean_rank_t,
            'filtered_hits10'      : np.mean([filtered_hits10_h, filtered_hits10_t]),
            'filtered_hits10_h'    : filtered_hits10_h,
            'filtered_hits10_t'    : filtered_hits10_t
        }
        with open(os.path.join(args.save_dir, 'metrics.json'), 'w') as jsonWriter:
            json.dump(metrics, jsonWriter, indent=2)


if __name__ == '__main__':
    tf.app.run()
