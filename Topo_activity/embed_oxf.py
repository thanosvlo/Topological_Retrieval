import torch
from torch.utils.data import DataLoader
import os
import numpy as np

import logging
import argparse
from tqdm import tqdm
from model import VideoModel, OxfordModel

from utils import get_dataloader
from losses import get_losses
from checkpoint import LocalCheckpoint
import torchvision
import sys
import argparse
import geoopt
from torch.utils.tensorboard import SummaryWriter
from geoopt.manifolds.stereographic.manifold import PoincareBall
from data.oxford_dataloader import TrainOxfordBuildings, ValOxfordBuildings

def run_train(args):
    writer = SummaryWriter(args.logdir,flush_secs=1)

    train_dataset = TrainOxfordBuildings(args)
    val_dataset = ValOxfordBuildings(args)

    train_loader = get_dataloader(train_dataset, True, args.workers,batch_size=args.batch_size)
    val_loader = get_dataloader(val_dataset, True, args.workers,batch_size=args.batch_size)

    model = OxfordModel(args.manifold, args.dim, args.nf, args.z_dim, args)
    model = model.to(args.device)

    loss = get_losses(args)

    # if args.manifold == 'poincare':
    #     optimizer = geoopt.optim.RiemannianSGD(model.parameters(),lr=args.lr)
    # else:
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr, amsgrad=True)

        # Set Up Checkpoint
    checkpoint = LocalCheckpoint(
        args.checkpoint,
        include_in_all={'conf': vars(args).copy()},
        start_fresh=True
    )
    state = checkpoint.initialize({'epoch': 0, 'model': model.state_dict()})
    model.load_state_dict(state['model'])
    args.epoch_start = state['epoch']

    # Training Loop
    train_loss = 0
    val_loss = 0
    best_loss = 1e10

    for epoch in range(args.epochs):
        train_epoch_loss = 0
        for (i,frames) in tqdm(enumerate(train_loader), total=len(train_loader)):
            frames = frames.to(args.device).float()

            optimizer.zero_grad()
            preds = model(frames)

            _loss = loss(preds,frames)
            _loss.backward()
            optimizer.step()
            train_epoch_loss += _loss.item()
            if i ==0 :
                grid = torchvision.utils.make_grid(frames)
                grid_pr = torchvision.utils.make_grid(preds)
                writer.add_image('Train/Input',grid,epoch)
                writer.add_image('Train/Pred',grid_pr,epoch)

        train_epoch_loss = train_epoch_loss / len(train_loader)
        train_loss += train_epoch_loss
        writer.add_scalar('Train/loss', train_epoch_loss, epoch)

        # Now run Validation
        with torch.no_grad():
            val_epoch_loss = 0
            for (i, (frames)) in tqdm(enumerate(val_loader), total=len(val_loader)):
                frames = frames.to(args.device).float()

                preds = model(frames)

                _loss = loss(preds, frames)
                val_epoch_loss += _loss.item()
                if i == 0:
                    grid = torchvision.utils.make_grid(frames)
                    grid_pr = torchvision.utils.make_grid(preds)
                    writer.add_image('Val/Input', grid, epoch)
                    writer.add_image('Val/Pred', grid_pr, epoch)
            val_epoch_loss = val_epoch_loss / len(val_loader)
            val_loss += val_epoch_loss

        logging.info('Epoch {}: Train Loss: {}; Val Loss: {}'.format(epoch, train_epoch_loss, val_epoch_loss))
        writer.add_scalar('Test/loss', val_epoch_loss, epoch)

        checkpoint.path = f'{args.checkpoint}.{epoch}'
        checkpoint.save({
            'model': model.state_dict(),
            'epoch': epoch,
            'val loss': val_epoch_loss,
            'train loss': train_epoch_loss
        })

        if val_epoch_loss <= best_loss:
            logging.info('**Epoch {}: Train Loss: {}; Val Loss: {} **'.format(epoch, train_epoch_loss, val_epoch_loss))
            best_loss = val_epoch_loss
            checkpoint.path = f'{args.checkpoint}.best'
            checkpoint.save({
                'model': model.state_dict(),
                'epoch': epoch,
                'val loss': val_epoch_loss,
                'train loss': train_epoch_loss
            })


if __name__=='__main__':
    parser = argparse.ArgumentParser(formatter_class=argparse.RawTextHelpFormatter)
    # Exp Logging
    parser.add_argument('--exp_name', type=str, default='oxford_buildings_z_dim_10_depth_3_n_f_16')
    parser.add_argument('--exp_root', type=str, default='./experiments')
    parser.add_argument('--restore', type=bool, default=False)
    parser.add_argument('--train', type=bool, default=True)

    # Model
    parser.add_argument('--manifold', type=str, default='euclidean', help='poincare, euclidean')
    parser.add_argument('--loss', type=str, default='ae', help='only_topology, hybrid, euclidean, ae')
    parser.add_argument('--dim', type=int, default=10)
    parser.add_argument('--nf', type=int, default=16)
    parser.add_argument('--z_dim', type=int, default=10)
    parser.add_argument('--depth', type=int, default=3)
    parser.add_argument('--c', type=float, default=1)

    # Dataset
    parser.add_argument('--image_path', type=str, default="/vol/medic01/users/av2514/Pycharm_projects/Datasets/Oxford_Buildings/images")
    parser.add_argument('--gt_path', type=str, default="/vol/medic01/users/av2514/Pycharm_projects/Datasets/Oxford_Buildings/ground_truth")

    # Optimization
    parser.add_argument('--batch_size',type=int,default=32)
    parser.add_argument('--lr', type=float, default=5e-4, help='Learning rate')
    parser.add_argument('--beta1', type=float, default=0.9, help='first parameter of Adam (default: 0.9)')
    parser.add_argument('--beta2', type=float, default=0.999, help='second parameter of Adam (default: 0.900)')
    parser.add_argument('--epochs', type=int, default=100)
    # OS
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--workers', type=int, default=0)
    parser.add_argument('--train_threads', type=int, default=1,
                        help='Number of threads to use in training')
    parser.add_argument('--gpu', type=str, default='0')
    args = parser.parse_args()

    # GPU utils
    os.environ["CUDA_VISIBLE_DEVICES"] = args.gpu
    args.cuda = torch.cuda.is_available()
    args.device = torch.device("cuda" if args.cuda else "cpu")

    # Seeds
    if args.seed == -1: args.seed = int(torch.randint(0, 2 ** 32 - 1, (1,)).item())
    print('seed', args.seed)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    torch.manual_seed(args.seed)
    torch.backends.cudnn.deterministic = True

    if args.restore:
        oldrunId = args.exp_name
        args.exp_name = args.exp_name + '_cont'

    args.logdir = os.path.join(args.exp_root, args.exp_name)
    if not os.path.exists(args.logdir):
        os.makedirs(args.logdir)

    args.checkpoint = os.path.join(args.logdir, args.exp_name)
    log_level = logging.INFO
    log = logging.getLogger('lorentz')
    logging.basicConfig(level=log_level, format='%(message)s', stream=sys.stdout)
    args.log = log

    if args.train:
        run_train(args)
    else:
        raise NotImplementedError

