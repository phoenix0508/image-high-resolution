import tensorflow as tf
from keras.layers import concatenate, Input, Activation, Add, Conv2D, Lambda, UpSampling2D
from keras.models import Model


def make_model(arch_params, patch_size):
    """ Returns the model.

    Used to select the model.
    """

    return RDN(arch_params, patch_size)


class RDN:
    def __init__(self, arch_params={}, patch_size=None, c_dim=3, kernel_size=3, upscaling='ups'):
        self.params = arch_params
        self.C = self.params['C']
        self.D = self.params['D']
        self.G = self.params['G']
        self.G0 = self.params['G0']
        self.scale = self.params['x']
        self.patch_size = patch_size
        self.c_dim = c_dim
        self.kernel_size = kernel_size
        self.upscaling = upscaling
        self.model = self._build_rdn()
        self.model.name = 'generator'
        self.name = 'rdn'

    def _upsampling_block(self, input_layer):
        """ Upsampling block for old weights. """

        x = Conv2D(self.c_dim * self.scale ** 2, kernel_size=3, padding='same', name='UPN3')(
            input_layer
        )
        return UpSampling2D(size=self.scale, name='UPsample')(x)

    def _pixel_shuffle(self, input_layer):
        """ PixelShuffle implementation of the upscaling layer. """

        x = Conv2D(self.c_dim * self.scale ** 2, kernel_size=3, padding='same', name='UPN3')(
            input_layer
        )
        return Lambda(
            lambda x: tf.depth_to_space(x, block_size=self.scale, data_format='NHWC'),
            name='PixelShuffle',
        )(x)

    def _UPN(self, input_layer):
        """ Upscaling layers. With old weights use _upsampling_block instead of _pixel_shuffle. """

        x = Conv2D(64, kernel_size=5, strides=1, padding='same', name='UPN1')(input_layer)
        x = Activation('relu', name='UPN1_Relu')(x)
        x = Conv2D(32, kernel_size=3, padding='same', name='UPN2')(x)
        x = Activation('relu', name='UPN2_Relu')(x)
        if self.upscaling == 'shuffle':
            return self._pixel_shuffle(x)
        elif self.upscaling == 'ups':
            return self._upsampling_block(x)
        else:
            raise ValueError('Invalid choice of upscaling layer.')

    def _RDBs(self, input_layer):
        """RDBs blocks.

        Args:
            input_layer: input layer to the RDB blocks (e.g. the second convolutional layer F_0).

        Returns:
            concatenation of RDBs output feature maps with G0 feature maps.
        """
        rdb_concat = list()
        rdb_in = input_layer
        for d in range(1, self.D + 1):
            x = rdb_in
            for c in range(1, self.C + 1):
                F_dc = Conv2D(
                    self.G, kernel_size=self.kernel_size, padding='same', name='F_%d_%d' % (d, c)
                )(x)
                F_dc = Activation('relu', name='F_%d_%d_Relu' % (d, c))(F_dc)
                # concatenate input and output of ConvRelu block
                # x = [input_layer,F_11(input_layer),F_12([input_layer,F_11(input_layer)]), F_13..]
                x = concatenate([x, F_dc], axis=3, name='RDB_Concat_%d_%d' % (d, c))
            # 1x1 convolution (Local Feature Fusion)
            x = Conv2D(self.G0, kernel_size=1, name='LFF_%d' % (d))(x)
            # Local Residual Learning F_{i,LF} + F_{i-1}
            rdb_in = Add(name='LRL_%d' % (d))([x, rdb_in])
            rdb_concat.append(rdb_in)

        assert len(rdb_concat) == self.D

        return concatenate(rdb_concat, axis=3, name='LRLs_Concat')

    def _build_rdn(self):
        LR_input = Input(shape=(self.patch_size, self.patch_size, 3), name='LR')
        F_m1 = Conv2D(self.G0, kernel_size=self.kernel_size, padding='same', name='F_m1')(LR_input)
        F_0 = Conv2D(self.G0, kernel_size=self.kernel_size, padding='same', name='F_0')(F_m1)
        FD = self._RDBs(F_0)
        # Global Feature Fusion
        # 1x1 Conv of concat RDB layers -> G0 feature maps
        GFF1 = Conv2D(self.G0, kernel_size=1, padding='same', name='GFF_1')(FD)
        GFF2 = Conv2D(self.G0, kernel_size=self.kernel_size, padding='same', name='GFF_2')(GFF1)
        # Global Residual Learning for Dense Features
        FDF = Add(name='FDF')([GFF2, F_m1])
        # Upscaling
        FU = self._UPN(FDF)
        # Compose SR image
        SR = Conv2D(self.c_dim, kernel_size=self.kernel_size, padding='same', name='SR')(FU)

        return Model(inputs=LR_input, outputs=SR)
