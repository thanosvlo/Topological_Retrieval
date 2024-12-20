import torch
import pickle
import pandas as pd
import numpy as np
import json
import random
from torch.utils.data import Dataset, DataLoader
import torchvision
from collections import defaultdict as ddict
from data.graph_dataset import BatchedDataset
from torch.utils.data.dataset import T_co
import glob
import os
from PIL import Image
from tqdm import tqdm
import cv2
from model import get_model
from torchvision import transforms
from PIL import Image
import copy

class TreeDataset(Dataset):
    def __getitem__(self, index) -> T_co:
        raise NotImplementedError

    def __init__(self,json_path,**kwargs):
        super(TreeDataset, self).__init__()
        self.json_path = json_path
        with open(self.json_path, "r") as fobj:
            self.data = json.load(fobj)
        self.database = self.data["database"]
        self.taxonomy = self.data["taxonomy"]

        # Dataset Hparams
        self.negs = kwargs.get('negs',50)
        self.batch_size = kwargs.get('batch_size',28)
        self.ndproc = kwargs.get('ndproc',4) # 'Number of data loading processes'
        self.burnin = kwargs.get('burnin',20)
        self.dampening = kwargs.get('dampening',0.75) # 'Sample dampening during burnin'
        self.neg_multiplier = kwargs.get('neg_multiplier',1.0)


        self.idx, self.objects, self.weights = self.get_hypernymity()
        # self.idx_2, self.objects_2, self.weights_2 = self.load_edge_list(path = '../others/poincare-embeddings-master/wordnet/mammal_closure.csv')

    def get_graph_dataset(self):
        data = BatchedDataset(self.idx, self.objects, self.weights, self.negs, self.batch_size,
                          self.ndproc, self.burnin > 0, self.dampening)
        data.neg_multiplier = self.neg_multiplier
        return data

    def get_hypernymity(self):
        hypers = []
        for item in self.taxonomy:
            hypers.append((item['nodeName'], item['parentName']))
        df = pd.DataFrame(hypers,columns=['child','parent'])
        idx, objects = pd.factorize(df[['child', 'parent']].values.reshape(-1),na_sentinel=None)
        idx = idx.reshape(-1, 2).astype('int')
        weights = np.ones([len(idx)])
        return idx, objects.tolist(), weights


class VideoDataset(TreeDataset):
    def __init__(self, json_path, video_path, csv_path, class_idx_path, args, window=10, mode='training'):
        super(VideoDataset, self).__init__(json_path)
        self.args = args
        self.video_path = video_path

        self.dataframe = pd.read_csv(csv_path)
        self.df = self.dataframe[self.dataframe['subset']==mode]
        self.df = self.df.drop(self.df[self.df['end_frame'] == self.df['start_frame']].index, axis=0)
        self.df = self.df.drop(self.df[(self.df['end_frame'] - self.df['start_frame'])<window].index, axis=0)
        self.df = self.df.reset_index(drop=True)
        with open(class_idx_path, 'rb') as f_in:
            self.class_to_index = pickle.load(f_in)

        self.window = window
        self.df['idxs'] = self.window_df()

        self.targets = get_targets(self.args,len(self.objects))


        self.to_use = self.flatten_df()
        self.to_use.to_csv('./data/activity_net_{}_window_{}.csv'.format(mode,self.args.window))


    def __len__(self):
        return len(self.df)

    def __getitem__(self, item):
        entry = self.df.loc[item]
        label = self.class_to_index[entry['label']]
        frames = []
        target = self.targets[self.objects.index(entry['label'])]
        for i in entry['idxs']:
            # img = Image.open(os.path.join(self.video_path,'v_{}'.format(entry['video_id']),str(label),'frame_{}.png'.format(i)))
            img = cv2.imread(os.path.join(self.video_path,'v_{}'.format(entry['video_id']),str(label),'frame_{}.png'.format(i)))
            # img = img[np.newaxis,...]
            img = img/255
            img = np.transpose(img,(2,0,1))

            # img = self.preprocess(img)

            frames.append(torch.from_numpy(img))
            # frames.append(torch.cat([img,img,img]))

        frames = torch.stack(frames,0)
        return frames, target , entry['label'], entry['video_id']

    def flatten_df(self):
        to_use = pd.DataFrame()
        for (j, row) in tqdm(self.df.iterrows(), total=len(self.df)):
            _row = copy.deepcopy(row)
            for idx in row['idxs']:
                _row['idxs'] = idx
                _row['target'] = self.targets[self.objects.index(row['label'])]
                to_use = to_use.append(_row,ignore_index=True)
        return to_use

    def window_df(self):
        id = []
        for (j,x) in tqdm(self.df.iterrows(),total=len(self.df)):
            duration_frames = x['end_frame'] - x['start_frame']
            if self.window == -1:
                step = 1
            else:
                step = int(duration_frames  / self.window)

            idxs_ = list(range(max(x['start_frame']+int(step/2),1), x['end_frame'], step))

            id.append(idxs_[:self.window])

        return id


def get_targets(args, N=273):
    args.sparse = True
    args.margin = 0.1
    model = get_model(args, N)
    state = torch.load(args.targets_path)
    model.load_state_dict(state['model'])
    return model.lt.weight

class FeatureVideoDataset(Dataset):
    def __init__(self,features_path, mode, args):
        super(FeatureVideoDataset, self).__init__()
        self.args = args
        self.train = torch.load(features_path.format('train'))
        self.val = torch.load(features_path.format('val'))

        # sum = 0
        # val =  [self.val[item]['target'] for item in self.val]
        # train  =  [self.train[item]['target'] for item in self.train]
        # for valitem in tqdm(val):
        #
        #     for item in train:
        #
        #         if torch.allclose(valitem, item):
        #             print('yes')
        #             sum += 1
        # print(sum)
        if mode=='training':
            self.to_read = self.train
        else:
            self.to_read = self.val
        if mode == 'testing':
            self.db = get_targets(args)

    def __getitem__(self, item):
        return self.to_read[item]['features'], self.to_read[item]['target'][0,:]

    def __len__(self):
        return len(self.to_read)

# FOR TESTING
if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('--json_path', type=str, default="../hyperbolic_action-master/activity_net.v1-3.json")
    parser.add_argument('--video_path', type=str, default="/data/Activity_net/processed_jpg")
    parser.add_argument('--csv_path', type=str, default='./activity_net.csv')
    parser.add_argument('--class_idx_path', type=str, default='./class_indx.pkl')

    args = parser.parse_args()
    # dataset = TreeDataset(args.json_path)
    dataset = VideoDataset(args.json_path, args.video_path, args.csv_path, args.class_idx_path)
    data = dataset.__getitem__(0)


