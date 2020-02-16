from google.colab import drive
drive.mount("/content/drive", force_remount=True)


import torch
import torch.nn.functional as F
import argparse
import cv2
import numpy as np
from glob import glob
import matplotlib.pyplot as plt
from copy import copy
from collections import OrderedDict
from tqdm import tqdm
import random


CLS = OrderedDict({
    "background": [0, 0, 0],
    "akahara": [0,0,128],
    "madara": [0,128,0]
      })


CLS = ["akahara_imori", "fire_salamander", "ibo_imori", "madara_imori", "marble_salamander", "minamiibo_imori", "shiriken_imori", "tiger_salamander"]

class_num = len(CLS)

img_height, img_width = 128, 128  #572, 572
out_height, out_width = 128, 128  #388, 388

UNet_dropout_ratio = 0.5 # False, (0 , 1)
Lambda = 1. # Loss balance  Ldis + Lambda * L1 norm
mb_N = 8 # minibatch
iteration_N = 10000 # iteration
lr = 0.0001 # learning rate

GPU = True
torch.manual_seed(0)

# wgan hyper-parameter
n_critic = 5
    
class Flatten(torch.nn.Module):
    def forward(self, x):
        x = x.view(x.size()[0], -1)
        return x
    
class Interpolate(torch.nn.Module):
    def forward(self, x):
        x = F.interpolate(x, scale_factor=2, mode="nearest")
        return x
    
    
class UNet_block(torch.nn.Module):
    def __init__(self, dim1, dim2, name, dropout=False):
        super(UNet_block, self).__init__()

        _module = OrderedDict()

        for i in range(2):
            f = dim1 if i == 0 else dim2
            _module["unet_{}_bn{}".format(name, i+1)] = torch.nn.BatchNorm2d(f)
            _module["unet_{}_relu{}".format(name, i+1)] = torch.nn.ReLU()
            _module["unet_{}_conv{}".format(name, i+1)] = torch.nn.Conv2d(f, dim2, kernel_size=3, padding=1, stride=1)
            if dropout != False:
                _module["unet_{}_dropout{}".format(name, i+1)] = torch.nn.Dropout2d(p=dropout)
            
            
        self.module = torch.nn.Sequential(_module)

    def forward(self, x):
        x = self.module(x)
        return x

class UNet_deconv_block(torch.nn.Module):
    def __init__(self, dim1, dim2):
        super(UNet_deconv_block, self).__init__()

        self.module = torch.nn.Sequential(
            torch.nn.ConvTranspose2d(dim1, dim2, kernel_size=2, stride=2),
            torch.nn.BatchNorm2d(dim2)
        )

    def forward(self, x):
        x = self.module(x)
        return x


class UNet(torch.nn.Module):
    def __init__(self):
        super(UNet, self).__init__()

        base = 32
        
        self.enc1 = UNet_block(1, base, name="enc1")
        self.enc2 = UNet_block(base, base * 2, name="enc2")
        self.enc3 = UNet_block(base * 2, base * 4, name="enc3")
        self.enc4 = UNet_block(base * 4, base * 8, name="enc4")
        self.enc5 = UNet_block(base * 8, base * 16, name="enc5")
        self.enc6 = UNet_block(base * 16, base * 16, name="enc6")
        self.enc7 = UNet_block(base * 16, base * 16, name="enc7")

        self.tconv6 = UNet_deconv_block(base * 16, base * 16)
        self.tconv5 = UNet_deconv_block(base * 16, base * 16)
        self.tconv4 = UNet_deconv_block(base * 16, base * 8)
        self.tconv3 = UNet_deconv_block(base * 8, base * 4)
        self.tconv2 = UNet_deconv_block(base * 4, base * 2)
        self.tconv1 = UNet_deconv_block(base * 2, base)

        self.dec7 = UNet_block(base * 32, base * 16, name="dec7", dropout=UNet_dropout_ratio)
        self.dec6 = UNet_block(base * 32, base * 16, name="dec6", dropout=UNet_dropout_ratio)
        self.dec5 = UNet_block(base * 32, base * 16, name="dec5", dropout=UNet_dropout_ratio)
        self.dec4 = UNet_block(base * 24, base * 8, name="dec4", dropout=UNet_dropout_ratio)
        self.dec3 = UNet_block(base * 12, base * 4, name="dec3", dropout=UNet_dropout_ratio)
        self.dec2 = UNet_block(base * 6, base * 2, name="dec2", dropout=UNet_dropout_ratio)
        self.dec1 = UNet_block(base * 3, base, name="dec1", dropout=UNet_dropout_ratio)

        self.out = torch.nn.Conv2d(base, 3, kernel_size=1, padding=0, stride=1)
        
        
    def forward(self, x):
        # Encoder block 1
        x_enc1 = self.enc1(x)
        x = F.max_pool2d(x_enc1, 2, stride=2, padding=0)
        
        # Encoder block 2
        x_enc2 = self.enc2(x)
        x = F.max_pool2d(x_enc2, 2, stride=2, padding=0)
        
        # Encoder block 3
        x_enc3 = self.enc3(x)
        x = F.max_pool2d(x_enc3, 2, stride=2, padding=0)
        
        # Encoder block 4
        x_enc4 = self.enc4(x)
        x = F.max_pool2d(x_enc4, 2, stride=2, padding=0)
        
        # Encoder block 5
        x_enc5 = self.enc5(x)
        x = F.max_pool2d(x_enc5, 2, stride=2, padding=0)
        
        # Encoder block 6
        x_enc6 = self.enc6(x)
        x = F.max_pool2d(x_enc6, 2, stride=2, padding=0)
        
        # Encoder block 7
        x_enc7 = self.enc7(x)
        x = F.max_pool2d(x_enc7, 2, stride=2, padding=0)
        
        # Decoder block 7
        x = F.interpolate(x, scale_factor=2, mode="nearest")
        x = torch.cat((x, x_enc7), dim=1)
        x = self.dec7(x)
        
        # Decoder block 6
        x = F.interpolate(x, scale_factor=2, mode="nearest")
        x = torch.cat((x, x_enc6), dim=1)
        x = self.dec6(x)
        
        # Decoder block 5
        x = F.interpolate(x, scale_factor=2, mode="nearest")
        x = torch.cat((x, x_enc5), dim=1)
        x = self.dec5(x)

        # Decoder block 4
        x = F.interpolate(x, scale_factor=2, mode="nearest")
        x = torch.cat((x, x_enc4), dim=1)
        x = self.dec4(x)

        # Decoder block 3
        x = F.interpolate(x, scale_factor=2, mode="nearest")
        x = torch.cat((x, x_enc3), dim=1)
        x = self.dec3(x)

        # Decoder block 2
        x = F.interpolate(x, scale_factor=2, mode="nearest")
        x = torch.cat((x, x_enc2), dim=1)
        x = self.dec2(x)

        # Decoder block 1
        x = F.interpolate(x, scale_factor=2, mode="nearest")
        x = torch.cat((x, x_enc1), dim=1)
        x = self.dec1(x)

        x = self.out(x)
        x = torch.tanh(x)
        #x = F.softmax(x, dim=1)
        #x = x * 2 - 1
        
        return x

    
    
class UNet2(torch.nn.Module):
    def __init__(self):
        super(UNet2, self).__init__()

        base = 16
        
        self.enc1 = torch.nn.Sequential()
        for i in range(2):
            f = 3 if i == 0 else base
            self.enc1.add_module("enc1_{}".format(i+1), torch.nn.Conv2d(f, base, kernel_size=3, padding=1, stride=1))
            self.enc1.add_module("enc1_relu_{}".format(i+1), torch.nn.ReLU())
            self.enc1.add_module("enc1_bn_{}".format(i+1), torch.nn.BatchNorm2d(base))

        self.enc2 = torch.nn.Sequential()
        for i in range(2):
            f = base if i == 0 else base * 2
            self.enc2.add_module("enc2_{}".format(i+1), torch.nn.Conv2d(f, base*2, kernel_size=3, padding=1, stride=1))
            self.enc2.add_module("enc2_relu_{}".format(i+1), torch.nn.ReLU())
            self.enc2.add_module("enc2_bn_{}".format(i+1), torch.nn.BatchNorm2d(base*2))

        self.enc3 = torch.nn.Sequential()
        for i in range(2):
            f = base*2 if i == 0 else base*4
            self.enc3.add_module("enc3_{}".format(i+1), torch.nn.Conv2d(f, base*4, kernel_size=3, padding=1, stride=1))
            self.enc3.add_module("enc3_relu_{}".format(i+1), torch.nn.ReLU())
            self.enc3.add_module("enc3_bn_{}".format(i+1), torch.nn.BatchNorm2d(base*4))

        self.enc4 = torch.nn.Sequential()
        for i in range(2):
            f = base*4 if i == 0 else base*8
            self.enc4.add_module("enc4_{}".format(i+1), torch.nn.Conv2d(f, base*8, kernel_size=3, padding=1, stride=1))
            self.enc4.add_module("enc4_relu_{}".format(i+1), torch.nn.ReLU())
            self.enc4.add_module("enc4_bn_{}".format(i+1), torch.nn.BatchNorm2d(base*8))

        self.enc5 = torch.nn.Sequential()
        for i in range(2):
            f = base*8 if i == 0 else base*16
            self.enc5.add_module("enc5_{}".format(i+1), torch.nn.Conv2d(f, base*16, kernel_size=3, padding=1, stride=1))
            self.enc5.add_module("enc5_relu_{}".format(i+1), torch.nn.ReLU())
            self.enc5.add_module("enc5_bn_{}".format(i+1), torch.nn.BatchNorm2d(base*16))

        self.tconv4 = torch.nn.ConvTranspose2d(base*16, base*8, kernel_size=2, stride=2)
        self.tconv4_bn = torch.nn.BatchNorm2d(base*8)

        self.dec4 = torch.nn.Sequential()
        for i in range(2):
            f = base*16 if i == 0 else base*8
            self.dec4.add_module("dec4_{}".format(i+1), torch.nn.Conv2d(f, base*8, kernel_size=3, padding=1, stride=1))
            self.dec4.add_module("dec4_relu_{}".format(i+1), torch.nn.ReLU())
            self.dec4.add_module("dec4_bn_{}".format(i+1), torch.nn.BatchNorm2d(base*8))
        

        self.tconv3 = torch.nn.ConvTranspose2d(base*8, base*4, kernel_size=2, stride=2)
        self.tconv3_bn = torch.nn.BatchNorm2d(base*4)

        self.dec3 = torch.nn.Sequential()
        for i in range(2):
            f = base*8 if i == 0 else base*4
            self.dec3.add_module("dec3_{}".format(i+1), torch.nn.Conv2d(f, base*4, kernel_size=3, padding=1, stride=1))
            self.dec3.add_module("dec3_relu_{}".format(i+1), torch.nn.ReLU())
            self.dec3.add_module("dec3_bn_{}".format(i+1), torch.nn.BatchNorm2d(base*4))

        self.tconv2 = torch.nn.ConvTranspose2d(base*4, base*2, kernel_size=2, stride=2)
        self.tconv2_bn = torch.nn.BatchNorm2d(base*2)

        self.dec2 = torch.nn.Sequential()
        for i in range(2):
            f = base*4 if i == 0 else base*2
            self.dec2.add_module("dec2_{}".format(i+1), torch.nn.Conv2d(f, base*2, kernel_size=3, padding=1, stride=1))
            self.dec2.add_module("dec2_relu_{}".format(i+1), torch.nn.ReLU())
            self.dec2.add_module("dec2_bn_{}".format(i+1), torch.nn.BatchNorm2d(base*2))

        self.tconv1 = torch.nn.ConvTranspose2d(base*2, base, kernel_size=2, stride=2)
        self.tconv1_bn = torch.nn.BatchNorm2d(base)

        self.dec1 = torch.nn.Sequential()
        for i in range(2):
            f = base*2 if i == 0 else base
            self.dec1.add_module("dec1_{}".format(i+1), torch.nn.Conv2d(f, base, kernel_size=3, padding=1, stride=1))
            self.dec1.add_module("dec1_relu_{}".format(i+1), torch.nn.ReLU())
            self.dec1.add_module("dec1_bn_{}".format(i+1), torch.nn.BatchNorm2d(base))

        self.out = torch.nn.Conv2d(base, class_num, kernel_size=1, padding=0, stride=1)
        
        
    def forward(self, x):
        # block conv1
        x_enc1 = self.enc1(x)
        x = F.max_pool2d(x_enc1, 2, stride=2, padding=0)
        
        # block conv2
        x_enc2 = self.enc2(x)
        x = F.max_pool2d(x_enc2, 2, stride=2, padding=0)
        
        # block conv31
        x_enc3 = self.enc3(x)
        x = F.max_pool2d(x_enc3, 2, stride=2, padding=0)
        
        # block conv4
        x_enc4 = self.enc4(x)
        x = F.max_pool2d(x_enc4, 2, stride=2, padding=0)
        
        # block conv5
        x = self.enc5(x)
        x = self.tconv4_bn(self.tconv4(x))

        x = torch.cat((x, x_enc4), dim=1)
        x = self.dec4(x)

        x = self.tconv3_bn(self.tconv3(x))

        x = torch.cat((x, x_enc3), dim=1)
        x = self.dec3(x)

        x = self.tconv2_bn(self.tconv2(x))
        x = torch.cat((x, x_enc2), dim=1)
        x = self.dec2(x)

        x = self.tconv1_bn(self.tconv1(x))
        x = torch.cat((x, x_enc1), dim=1)
        x = self.dec1(x)

        x = self.out(x)
        x = torch.tanh(x)
        #x = F.softmax(x, dim=1)
        #x = x * 2 - 1
        
        return x
    

class Discriminator(torch.nn.Module):
    def __init__(self):
        self.base = 32
        
        super(Discriminator, self).__init__()
        
        self.module = torch.nn.Sequential(OrderedDict({
            "conv1": torch.nn.Conv2d(4, self.base, kernel_size=5, padding=2, stride=2),
            "bn1": torch.nn.BatchNorm2d(self.base),
            "relu1": torch.nn.LeakyReLU(0.2),
            "conv2": torch.nn.Conv2d(self.base, self.base * 2, kernel_size=5, padding=2, stride=2),
            "bn2": torch.nn.BatchNorm2d(self.base * 2),
            "relu2": torch.nn.LeakyReLU(0.2),
            "conv3": torch.nn.Conv2d(self.base * 2, self.base * 4, kernel_size=5, padding=2, stride=2),
            "bn3": torch.nn.BatchNorm2d(self.base * 4),
            "relu3": torch.nn.LeakyReLU(0.2),
            "conv4": torch.nn.Conv2d(self.base * 4, self.base * 8, kernel_size=5, padding=2, stride=2),
            "bn4": torch.nn.BatchNorm2d(self.base * 8),
            "relu4": torch.nn.LeakyReLU(0.2),
            "flatten": Flatten(),
            "linear1": torch.nn.Linear((img_height // 16) * (img_width // 16) * self.base * 8, 1),
            "sigmoid": torch.nn.Sigmoid(),
        }))

    def forward(self, x):
        x = self.module(x)
        return x
    
    
    
# get train data
def data_path_load(path):
    paths = []
    
    num = 0
    
    # each directory
    for dir_path in glob(path + "/*"):
        # get image file by extension jpg, jpeg, png
        _paths = glob(dir_path + "/*.jp*g") + glob(dir_path + "/*.png")
        # get image number
        _num = len(_paths)
        paths += _paths
        num += _num
        print("load :", dir_path, " , N :", _num)
            
    print("total :", num)
    
    return paths



def data_load(paths, hf=False, vf=False):
    imgs = []
    edges = []
    
    for path in paths:
        # read image
        img = cv2.imread(path)
        # resize image
        img = cv2.resize(img, (img_width, img_height))
        # get gray
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # get canny
        edge = cv2.Canny(gray, 100, 150)
        # transpose BGR to RGB
        img = img[..., ::-1]
        
        # horizontal flip
        if (random.random() < 0.5) and hf:
            img = img[:, ::-1]
            edge = edge[:, ::-1]
        
        # vertical flip
        if (random.random() < 0.5) and vf:
            img = img[::-1]
            edge = edge[::-1]
            
        # add data
        imgs += [img]
        edges += [edge]
        
    # list -> np.array
    imgs = np.array(imgs, dtype=np.float32)
    edges = np.array(edges, dtype=np.float32)
    
    # normalize [0, 255] to [-1, 1]
    imgs = imgs / 127.5 - 1.
    edges = edges / 127.5 - 1.
    
    # add channel dimension
    edges = np.expand_dims(edges, axis=1)
    
    # transpose dimension [mb, h, w, c] -> [mb, c, h, w]
    imgs = imgs.transpose(0, 3, 1, 2)
    
    return edges, imgs
            
    
    


# train
def train():
    # GPU
    device = torch.device("cuda" if GPU else "cpu")

    # model
    # generator
    G = UNet().to(device)
    opt_G = torch.optim.Adam(G.parameters(), lr=lr, betas=(0.5, 0.999))
    
    D = Discriminator().to(device)
    opt_D = torch.optim.Adam(D.parameters(), lr=lr, betas=(0.5, 0.999))
    
    G.train()
    D.train()

    #imgs, gts, paths = data_load('drive/My Drive/Colab Notebooks/' + 'datasets/', hf=True, vf=True)
    paths = data_path_load('drive/My Drive/Colab Notebooks/datasets/')

    # training
    mbi = 0
    train_N = len(paths)
    train_ind = np.arange(train_N)
    np.random.seed(0)
    np.random.shuffle(train_ind)
                          
    loss_fn = torch.nn.BCELoss()
    loss_l1 = torch.nn.L1Loss()

    # prepare label for Discriminator
    one = torch.FloatTensor([1])
    mone = one * -1

    if GPU:
        one = one.cuda()
        minus_one = mone.cuda()
    
    for i in range(iteration_N):
        if mbi + mb_N > train_N:
            mb_ind = copy(train_ind[mbi:])
            np.random.shuffle(train_ind)
            mb_ind = np.hstack((mb_ind, train_ind[: (mb_N - (train_N - mbi))]))
            mbi = mb_N - (train_N - mbi)
        else:
            mb_ind = train_ind[mbi : mbi+mb_N]
            mbi += mb_N
            
        opt_G.zero_grad()

        for _ in range(n_critic):
        
            opt_D.zero_grad()
            imgs, gts = data_load([paths[mb_index] for mb_index in mb_ind], hf=True)
                
            x = torch.tensor(imgs, dtype=torch.float).to(device)
            y = torch.tensor(gts, dtype=torch.float).to(device)
            
            
            # Discirminator training
            Gx = G(x)
                            
            fake_x = torch.cat([Gx, x], dim=1)
                            
            loss_D_fake = loss_fn(D(fake_x), torch.ones(mb_N, dtype=torch.float).to(device))
            #loss_D_fake.backward(retain_graph=True)
            
            real_x = torch.cat([y, x], dim=1)
            
            loss_D_real = loss_fn(D(real_x), torch.zeros(mb_N, dtype=torch.float).to(device))
            #loss_D_real.backward()
            
            loss_D = loss_D_real + loss_D_fake

            #----
            # Gradient Penalty
            #---
            # sample epsilon from [0, 1]
            epsilon = np.random.random() #np.random.uniform(0, 1, 1)

            # sample x_hat 
            x_hat = (epsilon * real_x + (1 - epsilon) * fake_x).requires_grad_(True)

            # gradient penalty
            Dx_hat = D(x_hat)
            musk = torch.ones_like(Dx_hat)
            gradients = torch.autograd.grad(Dx_hat, x_hat, grad_outputs=musk,
                                retain_graph=True, create_graph=True,
                                allow_unused=True)[0]
            gradients = gradients.view(-1, 1)
            gradient_penalty = Lambda * ((gradients.norm(2, dim=1) - 1) ** 2).mean()

            # loss backpropagation
            #loss_D_real.backward(one, retain_graph=True)
            #loss_D_fake.backward(minus_one, retain_graph=True)
            loss_D_real.backward(retain_graph=True)
            loss_D_fake.backward(retain_graph=True)
            gradient_penalty.backward(retain_graph=True)
                          
            opt_D.step()
            
        # UNet training
        loss_G_fake = loss_fn(D(fake_x), torch.zeros(mb_N, dtype=torch.float).to(device))
        #loss_G_fake.backward(retain_graph=True)
        
        loss_G_l1 = Lambda * loss_l1(Gx, x)
        #loss_G_l1.backward()
        loss_G = loss_G_fake + loss_G_l1
        loss_G.backward()
        
        opt_G.step()


        
        
        if (i+1) % 10 == 0:
            print("iter : ", i+1, ", loss D : ", loss_D.item(), 'loss GP : ', gradient_penalty.item(), ", loss G :", loss_G.item())
            
        if (i+1) % 10000 == 0:
            torch.save(G.state_dict(), 'drive/My Drive/Colab Notebooks/pix2pix.pt')

    torch.save(G.state_dict(), 'drive/My Drive/Colab Notebooks/pix2pix.pt')

    
# test
def test():
    device = torch.device("cuda" if GPU else "cpu")
    model = UNet().to(device)
    model.eval()
    model.load_state_dict(torch.load('drive/My Drive/Colab Notebooks/pix2pix.pt'))

    #xs, ts, paths = data_load('drive/My Drive/Colab Notebooks/'  + 'datasets/')
    paths = data_path_load('drive/My Drive/Colab Notebooks/datasets/')

    for i in range(40):
        # get data
        path = paths[i]
        imgs, ts = data_load([path])
        
        x = torch.tensor(imgs, dtype=torch.float).to(device)
        
        # predict image
        pred = model(x)
    
        # change type torch.tensor -> numpy
        pred = pred.detach().cpu().numpy()[0]

        # visualize
        # [-1, 1] -> [0, 255]
        out = (pred + 1) * 127.5
        # clipping to [0, 255]
        out = np.clip(out, 0, 255)
        # exchange dimension [c, h, w] -> [h, w, c]
        out = out.transpose(1,2,0).astype(np.uint8)

        print("in {}".format(path))
        
        # for display
        # [mb, c, h, w] and [-1, 1] -> [h, w] and [0, 1]
        edge_img = (imgs[0, 0] + 1) / 2.
        # [mb, c, h, w] and [-1, 1] -> [h, w, c] and [0, 1]
        original_img = (ts[0].transpose(1, 2, 0) + 1) / 2.
        
        
        plt.subplot(1, 3, 1)
        plt.imshow(edge_img, cmap="gray")
        plt.subplot(1, 3, 2)
        plt.imshow(out)
        plt.subplot(1, 3, 3)
        plt.imshow(original_img)
        plt.show()
    

def arg_parse():
    parser = argparse.ArgumentParser(description='CNN implemented with Keras')
    parser.add_argument('--train', dest='train', action='store_true')
    parser.add_argument('--test', dest='test', action='store_true')
    args = parser.parse_args()
    return args

# main
train()
test()