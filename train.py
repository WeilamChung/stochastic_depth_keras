from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np
np.random.seed(2 ** 10)

from six.moves import range

from keras.datasets import cifar10
from keras.layers import Input, Dense, Layer, merge, Activation, Flatten, Lambda
from keras.layers.convolutional import Convolution2D, AveragePooling2D
from keras.layers.normalization import BatchNormalization
from keras.models import Model
from keras.optimizers import SGD
from keras.callbacks import Callback
from keras.preprocessing.image import ImageDataGenerator
from keras.utils import np_utils
import keras.backend as K


batch_size = 16
nb_classes = 10
nb_epoch = 500
N = 18

death_mode = "lin_decay"  # or uniform
death_rate = 0.5

img_rows, img_cols = 32, 32
img_channels = 3

(X_train, y_train), (X_test, y_test) = cifar10.load_data()
print('X_train shape:', X_train.shape)
print(X_train.shape[0], 'train samples')
print(X_test.shape[0], 'test samples')

X_train = X_train.astype('float32')
X_test = X_test.astype('float32')

X_train -= X_train.mean(axis=0)
X_train *= 1 / X_train.std(axis=0)
X_test -= X_test.mean(axis=0)
X_test *= 1 / X_test.std(axis=0)

# convert class vectors to binary class matrices
Y_train = np_utils.to_categorical(y_train, nb_classes)
Y_test = np_utils.to_categorical(y_test, nb_classes)


class Switch(Layer):
    def __init__(self, condition, **kwargs):
        self.condition = condition
        super(Switch, self).__init__(**kwargs)

    def call(self, x, mask=None):
        assert len(x) == 2
        return K.switch(self.condition, x[0], x[1])

    def get_output_shape_for(self, input_shape):
        return input_shape[0]


class Padding(Layer):
    def __init__(self, pad_shape, axis, **kwargs):
        self.pad_shape = pad_shape
        self.axis = axis
        super(Padding, self).__init__(**kwargs)

    def call(self, x, mask=None):
        ones = K.ones(self.pad_shape)
        return K.concatenate([x, ones], axis=self.axis)

    def get_output_shape_for(self, input_shape):
        output_shape = list(input_shape)
        output_shape[self.axis] += self.pad_shape[self.axis]
        return tuple(output_shape)


add_tables = []

inputs = Input(shape=(img_channels, img_rows, img_cols))

net = Convolution2D(16, 3, 3, border_mode="same")(inputs)
net = BatchNormalization()(net)
net = Activation("relu")(net)


def residual_drop(x, input_shape, output_shape, strides=(1, 1)):
    global add_tables

    nb_filter = output_shape[0]
    conv = Convolution2D(nb_filter, 3, 3, subsample=strides, border_mode="same")(x)
    conv = BatchNormalization()(conv)
    conv = Activation("relu")(conv)
    conv = Convolution2D(nb_filter, 3, 3, border_mode="same")(conv)
    conv = BatchNormalization()(conv)

    if strides[0] >= 2:
        x = AveragePooling2D(strides)(x)

    if (output_shape[0] - input_shape[0]) > 0:
        pad_shape = (batch_size,
                     output_shape[0] - input_shape[0],
                     output_shape[1],
                     output_shape[2])
        x = Padding(pad_shape=pad_shape, axis=1)(x)

    _death_rate = K.variable(death_rate)
    train_phase = K.equal(K.learning_phase(), 1)
    scale = K.switch(train_phase, K.ones(1), K.ones(1) - _death_rate)
    conv = Lambda(lambda c: scale * c)(conv)

    out = merge([conv, x], mode="sum")
    out = Activation("relu")(out)

    gate = K.variable(1, dtype="uint8")
    add_tables += [{"death_rate": _death_rate, "gate": gate}]
    return Switch(gate)([out, x])


for i in range(N):
    net = residual_drop(net, input_shape=(16, 32, 32), output_shape=(16, 32, 32))

net = residual_drop(
    net,
    input_shape=(16, 32, 32),
    output_shape=(32, 16, 16),
    strides=(2, 2)
)
for i in range(N - 1):
    net = residual_drop(
        net,
        input_shape=(32, 16, 16),
        output_shape=(32, 16, 16)
    )

net = residual_drop(
    net,
    input_shape=(32, 16, 16),
    output_shape=(32, 8, 8),
    strides=(2, 2)
)
for i in range(N - 1):
    net = residual_drop(
        net,
        input_shape=(32, 8, 8),
        output_shape=(32, 8, 8)
    )

pool = AveragePooling2D((8, 8))(net)
flatten = Flatten()(pool)

predictions = Dense(10, activation="softmax")(flatten)
model = Model(input=inputs, output=predictions)

sgd = SGD(lr=0.5, decay=1e-4, momentum=0.9, nesterov=True)
model.compile(optimizer=sgd, loss="categorical_crossentropy")


def open_all_gates():
    for t in add_tables:
        K.set_value(t["gate"], 1)


# setup death rate
for i, tb in enumerate(add_tables):
    if death_mode == "uniform":
        K.set_value(tb["death_rate"], death_rate)
    elif death_mode == "lin_decay":
        K.set_value(tb["death_rate"], i / len(add_tables) * death_rate)
    else:
        raise


class GatesUpdate(Callback):
    def on_batch_begin(self, batch, logs={}):
        open_all_gates()

        rands = np.random.uniform(size=len(add_tables))
        for t, rand in zip(add_tables, rands):
            if rand < K.get_value(t["death_rate"]):
                K.set_value(t["gate"], 0)

    def on_epoch_end(self, epoch, logs={}):
        open_all_gates()  # for validation


datagen = ImageDataGenerator(
    featurewise_center=False,
    samplewise_center=False,
    featurewise_std_normalization=False,
    samplewise_std_normalization=False,
    zca_whitening=False,
    rotation_range=0,
    width_shift_range=0.,
    height_shift_range=0.,
    horizontal_flip=True,
    vertical_flip=False)
datagen.fit(X_train)

# fit the model on the batches generated by datagen.flow()
model.fit_generator(datagen.flow(
    X_train, Y_train,
    batch_size=batch_size),
    samples_per_epoch=X_train.shape[0],
    nb_epoch=nb_epoch,
    validation_data=(X_test, Y_test))
