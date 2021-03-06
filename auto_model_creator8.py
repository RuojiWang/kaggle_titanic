#coding=utf-8
#这个版本应该就是通过大量的实验发现第二层效果最好的应该是进行lr的超参搜索
#不论是使用tpot还是使用第二层的贝叶斯优化神经网络基本都被不加优化的lr稳压。。
#然后还在观察代码的运行过程中发现了单节点训练过程中存在过拟合的问题并给出了一种解决方案
#其余问题，诸如stacking中所包含的模型的数量以及单节点搜索的次数等等均无稳定答案，第二层不增加噪声哈。
#我将这些实验的结果形成了这个版本的代码咯，并将这个版本的结果提交咯。
import os
import sys
import random
import pickle
import datetime
import warnings
import numpy as np
import pandas as pd

sys.path.append("D:\\Workspace\\Titanic")

from sklearn import preprocessing
from sklearn.cross_validation import cross_val_score, StratifiedKFold

import torch.nn.init
import torch.nn as nn
import torch.nn.functional as F

from sklearn.neighbors import KNeighborsClassifier
from sklearn.linear_model import LogisticRegression 
from sklearn.ensemble import RandomForestClassifier

from sklearn.cross_validation import train_test_split

from sklearn.model_selection import KFold, RandomizedSearchCV

import skorch
from skorch import NeuralNetClassifier

import hyperopt
from hyperopt import fmin, tpe, hp, space_eval, rand, Trials, partial, STATUS_OK

from tpot import TPOTClassifier

from xgboost import XGBClassifier

from mlxtend.classifier import StackingCVClassifier

from sklearn.linear_model import LogisticRegression
#下面的这个kfold是实现k折交叉的功能，返回每次的indice，可以设置为shuffle但默认未设
#然后这个StratifiedKFold是返回k折交叉的迭代器，每次通过迭代器返回结果，可以设置为shuffle
#两者的区别在于前者返回indice或者索引列表后者直接返回迭代器，虽然我这一份代码两种方式都有但是让他们并存吧
#from sklearn.model_selection import KFold,StratifiedKFold

warnings.filterwarnings('ignore')

def isclose(a, b, rel_tol=1e-09, abs_tol=0.0):
    return abs(a-b) <= max(rel_tol * max(abs(a), abs(b)), abs_tol)

data_train = pd.read_csv("C:/Users/win7/Desktop/train.csv")
data_test = pd.read_csv("C:/Users/win7/Desktop/test.csv")
combine = [data_train, data_test]

for dataset in combine:
    dataset['Title'] = dataset.Name.str.extract('([A-Za-z]+)\.', expand=False)
    dataset['Title'] = dataset['Title'].replace(['Lady', 'Countess', 'Col', 'Don', 'Dr', 'Major', 'Rev', 'Sir', 'Jonkheer', 'Dona'], 'Rare')
    dataset['Title'] = dataset['Title'].replace('Mlle', 'Miss')
    dataset['Title'] = dataset['Title'].replace('Ms', 'Miss')
    dataset['Title'] = dataset['Title'].replace('Mme', 'Mrs')
    title_map = {'Mr': 1, 'Miss': 2, 'Mrs': 3, 'Master': 4, 'Rare': 5}
    dataset['Title'] = dataset['Title'].map(title_map)
    dataset['Title'] = dataset['Title'].fillna(0)

for dataset in combine:
    dataset['FamilySize'] = dataset['SibSp'] + dataset['Parch'] + 1
    dataset['FamilySizePlus'] = 0
    dataset.loc[dataset['FamilySize'] == 1, 'FamilySizePlus'] = 1
    dataset.loc[dataset['FamilySize'] == 2, 'FamilySizePlus'] = 2
    dataset.loc[dataset['FamilySize'] == 3, 'FamilySizePlus'] = 2
    dataset.loc[dataset['FamilySize'] == 4, 'FamilySizePlus'] = 2
    dataset.loc[dataset['FamilySize'] == 5, 'FamilySizePlus'] = 1
    dataset.loc[dataset['FamilySize'] == 6, 'FamilySizePlus'] = 1
    dataset.loc[dataset['FamilySize'] == 7, 'FamilySizePlus'] = 1

for dataset in combine:
    dataset['Sex'] = dataset['Sex'].map({'female': 1, 'male': 0}).astype(int)

guess_ages = np.zeros((2, 3))
for dataset in combine:
    for i in range(0, 2):
        for j in range(0, 3):
            guess_df = dataset[(dataset['Sex'] == i) & (dataset['Pclass'] == j+1)]['Age'].dropna()
            age_guess = guess_df.median()
            guess_ages[i,j] = int(age_guess / 0.5 + 0.5) * 0.5
    for i in range(0, 2):
        for j in range(0, 3):
            dataset.loc[(dataset.Age.isnull()) & (dataset.Sex == i) & (dataset.Pclass == j + 1), 'Age'] = guess_ages[i, j]
    dataset['Age'] = dataset['Age'].astype(int)
    
for dataset in combine: 
    dataset.loc[ dataset['Age'] <= 16, 'Age'] = 0 
    dataset.loc[(dataset['Age'] > 16) & (dataset['Age'] <= 32), 'Age'] = 1 
    dataset.loc[(dataset['Age'] > 32) & (dataset['Age'] <= 48), 'Age'] = 2 
    dataset.loc[(dataset['Age'] > 48) & (dataset['Age'] <= 64), 'Age'] = 3 
    dataset.loc[ dataset['Age'] > 64, 'Age'] = 4
    
#这里的mode是求解pandas.core.series.Series众数的第一个值（可能有多个众数）
freq_port = data_train.Embarked.dropna().mode()[0]
for dataset in combine:
    dataset['Embarked'] = dataset['Embarked'].fillna(freq_port)
for dataset in combine:
    dataset['Embarked'] = dataset['Embarked'].map({'S': 0, 'C': 1, 'Q': 2})

#将data_test中的fare元素所缺失的部分由已经包含的数据的中位数决定哈
data_test['Fare'].fillna(data_test['Fare'].dropna().median(), inplace=True)

for dataset in combine:
    dataset.loc[ dataset['Fare'] <= 7.91, 'Fare'] = 0
    dataset.loc[(dataset['Fare'] > 7.91) & (dataset['Fare'] <= 14.454), 'Fare'] = 1
    dataset.loc[(dataset['Fare'] > 14.454) & (dataset['Fare'] <= 31), 'Fare']   = 2
    dataset.loc[ dataset['Fare'] > 31, 'Fare'] = 3
    dataset['Fare'] = dataset['Fare'].astype(int)

for dataset in combine:
    dataset.loc[(dataset.Cabin.isnull()), 'Cabin'] = 0
    dataset.loc[(dataset.Cabin.notnull()), 'Cabin'] = 1

#尼玛给你说的这个是贡献船票，原来的英文里面根本就没有这种说法嘛
df = data_train['Ticket'].value_counts()
df = pd.DataFrame(df)
df = df[df['Ticket'] > 1]
#print(df)
df_ticket = df.index.values          #共享船票的票号
tickets = data_train.Ticket.values   #所有的船票
#print(tickets)
result = []
for ticket in tickets:
    if ticket in df_ticket:
        ticket = 1
    else:
        ticket = 0                   #遍历所有船票，在共享船票里面的为1，否则为0
    result.append(ticket)
    
df = data_train['Ticket'].value_counts()
df = pd.DataFrame(df)
df = df[df['Ticket'] > 1]
df_ticket = df.index.values          #共享船票的票号
tickets = data_train.Ticket.values   #所有的船票

result = []
for ticket in tickets:
    if ticket in df_ticket:
        ticket = 1
    else:
        ticket = 0                   #遍历所有船票，在共享船票里面的为1，否则为0
    result.append(ticket)

results = pd.DataFrame(result)
results.columns = ['Ticket_Count']
data_train = pd.concat([data_train, results], axis=1)

df = data_test['Ticket'].value_counts()
df = pd.DataFrame(df)
df = df[df['Ticket'] > 1]
df_ticket = df.index.values          
tickets = data_test.Ticket.values   
result = []
for ticket in tickets:
    if ticket in df_ticket:
        ticket = 1
    else:
        ticket = 0                   
    result.append(ticket)
results = pd.DataFrame(result)
results.columns = ['Ticket_Count']
data_test = pd.concat([data_test, results], axis=1) 

data_train_1 = data_train.copy()
data_test_1  = data_test.copy()
data_test_1 = data_test_1.drop(['PassengerId', 'Name', 'SibSp', 'Parch', 'Ticket', 'FamilySize'], axis=1)

X_train = data_train_1[['Pclass', 'Sex', 'Age', 'Fare', 'Embarked', 'Cabin', 'Title', 'FamilySizePlus', 'Ticket_Count']]
Y_train = data_train_1['Survived']

X_test = data_test_1[['Pclass', 'Sex', 'Age', 'Fare', 'Embarked', 'Cabin', 'Title', 'FamilySizePlus', 'Ticket_Count']]

X_all = pd.concat([X_train, X_test], axis=0)
#我觉得训练集和测试集需要在一起进行特征缩放，所以注释掉了原来的X_train的特征缩放咯
#用了五个月之后我发现我的特征缩放好像做错了？？所以试一下下面的特征缩放吧。。不过变量名好像可以不用修改吧
#X_all_scaled = pd.DataFrame(preprocessing.scale(X_all), columns = X_train.columns)
X_all_scaled = pd.DataFrame(preprocessing.StandardScaler().fit_transform(X_all), columns = X_train.columns)
#X_train_scaled = pd.DataFrame(preprocessing.scale(X_train), columns = X_train.columns)
#https://blog.csdn.net/CherDW/article/details/56011531讲解了几种特征缩放的区别，scale和.StandardScaler其实差不多。。
X_train_scaled = X_all_scaled[:len(X_train)]
X_test_scaled = X_all_scaled[len(X_train):]

def cal_acc(Y_train_pred, Y_train):

    count = (Y_train_pred == Y_train).sum()
    acc = count/len(Y_train)
    
    return acc
    
def cal_nnclf_acc(clf, X_train, Y_train):
    
    Y_train_pred = clf.predict(X_train.astype(np.float32))
    count = (Y_train_pred == Y_train).sum()
    acc = count/len(Y_train)
    
    return acc

def print_nnclf_acc(acc):
    
    print("the accuracy rate of the model on the whole train dataset is:", acc)
  
def print_best_params_acc(trials):
    
    trials_list =[]
    for item in trials.trials:
        trials_list.append(item)
    
    trials_list.sort(key=lambda item: item["result"]["loss"])
    
    print("best parameter is:", trials_list[0])
    print()
    
def exist_files(title):
    
    return os.path.exists(title+"_best_model.pickle")
    
def save_inter_params(trials, space_nodes, best_nodes, title):
 
    files = open(str(title+"_intermediate_parameters.pickle"), "wb")
    pickle.dump([trials, space_nodes, best_nodes], files)
    files.close()

def load_inter_params(title):
  
    files = open(str(title+"_intermediate_parameters.pickle"), "rb")
    trials, space_nodes, best_nodes = pickle.load(files)
    files.close()
    
    return trials, space_nodes ,best_nodes

#下面这个方式修改代码是最简单对于全局影响最小的方式了吧
#可能每次得到的stacked_train不一样所以保存的best_model并没有那么有意义
def save_stacked_dataset(stacked_train, stacked_test, title):
    
    files = open(str(title+"_stacked_dataset.pickle"), "wb")
    pickle.dump([stacked_train, stacked_test], files)
    files.close()
    
def load_stacked_dataset(title):
    
    files = open(str(title+"_stacked_dataset.pickle"), "rb")
    stacked_train, stacked_test = pickle.load(files)
    files.close()
    
    return stacked_train, stacked_test

def save_best_model(best_model, title):
    
    files = open(str(title+"_best_model.pickle"), "wb")
    pickle.dump(best_model, files)
    files.close()
    
def load_best_model(title_and_nodes):
    
    files = open(str(title_and_nodes+"_best_model.pickle"), "rb")
    best_model = pickle.load(files)
    files.close()
    
    return best_model
    
def record_best_model_acc(clf, acc, best_model, best_acc):
    
    flag = False
    
    if not isclose(best_acc, acc):
        if best_acc < acc:
            flag = True
            best_acc = acc
            best_model = clf
            
    return best_model, best_acc, flag

def create_module(input_nodes, hidden_layers, hidden_nodes, output_nodes, percentage=0.1):
    
    module_list = []
    
    if(hidden_layers==0):
        
        module_list.append(nn.Linear(input_nodes, output_nodes))
        module_list.append(nn.ReLU())
        module_list.append(nn.Softmax())
        
    else :
        module_list.append(nn.Linear(input_nodes, hidden_nodes))
        module_list.append(nn.ReLU())
        
        for i in range(0, hidden_layers):
            module_list.append(nn.Linear(hidden_nodes, hidden_nodes))
            module_list.append(nn.ReLU())
             
        module_list.append(nn.Linear(hidden_nodes, output_nodes))
        module_list.append(nn.ReLU())
        module_list.append(nn.Softmax())
        
    temp_list = []
    for i in range(0, len(module_list)):
        temp_list.append(module_list[i])
        if((i%3==2) and (i!=len(module_list)-2) and (i!=len(module_list)-1)):
            temp_list.append(nn.Dropout(percentage))
            
    model = nn.Sequential()
    for i in range(0, len(temp_list)):
        model.add_module(str(i+1), temp_list[i])
    
    return model

def init_module(clf, weight_mode, bias):
    
    for name, params in clf.named_parameters():
        if name.find("weight") != -1:
            if (weight_mode==1):
                pass
        
            elif (weight_mode==2):
                torch.nn.init.normal_(params)
        
            elif (weight_mode==3):
                torch.nn.init.xavier_normal_(params)
        
            else:
                torch.nn.init.xavier_uniform_(params)
        
        if name.find("bias") != -1:
            if (weight_mode==1):
                pass
        
            elif (weight_mode==2):
                torch.nn.init.constant_(params, bias)
        
            elif (weight_mode==3):
                torch.nn.init.constant_(params, bias)
        
            else:
                torch.nn.init.constant_(params, bias)
        
def noise_augment_data(mean, std, X_train, Y_train, columns):
    
    X_noise_train = X_train.copy()
    X_noise_train.is_copy = False
    
    row = X_train.shape[0]
    for i in range(0, row):
        for j in columns:
            X_noise_train.iloc[i,[j]] +=  random.gauss(mean, std)

    return X_noise_train, Y_train

#我有时候再想这个超参是不是有的时候应该重复两次以避免漏掉最佳的超参？？
#或者我换个角度看待这个问题：即便是这个超参是全局最优但是太容易出问题也不行吧
#所以从这个角度出发的话，我觉得用这种方式代表超参其实也是有道理的吧。
#OK，现在新的问题又产生了，现在的问题是：nn_f的函数不支持传递参数
#为了支持两次超参搜索现在有两种方式实现一种方式是写两个类似nn_f的函数，
#还有一种方式是在执行nn_f之前将其训练集的变量指向另外一个训练集就vans了
#我觉得从代码的维护性等方面来说应该后一种方式是更恰当的方式吧，反正也需要重构代码咯
#但是由于这边增加噪声的情况下，所以nn_f并不能够实现通用，看来最后还是要写两份呀
def nn_f(params):
    
    print("mean", params["mean"])
    print("std", params["std"])
    print("lr", params["lr"])
    print("optimizer__weight_decay", params["optimizer__weight_decay"])
    print("criterion", params["criterion"])
    print("batch_size", params["batch_size"])
    print("optimizer__betas", params["optimizer__betas"])
    print("bias", params["bias"])
    print("weight_mode", params["weight_mode"])
    print("patience", params["patience"])
    print("input_nodes", params["input_nodes"])
    print("hidden_layers", params["hidden_layers"])
    print("hidden_nodes", params["hidden_nodes"])
    print("output_nodes", params["output_nodes"])
    print("percentage", params["percentage"])
        
    X_noise_train, Y_noise_train = noise_augment_data(params["mean"], params["std"], X_train_scaled, Y_train, columns=[3, 4, 5, 6, 7, 8])
    
    clf = NeuralNetClassifier(lr = params["lr"],
                              optimizer__weight_decay = params["optimizer__weight_decay"],
                              criterion = params["criterion"],
                              batch_size = params["batch_size"],
                              optimizer__betas = params["optimizer__betas"],
                              module = create_module(params["input_nodes"], params["hidden_layers"], 
                                                      params["hidden_nodes"], params["output_nodes"], params["percentage"]),
                              max_epochs = params["max_epochs"],
                              callbacks=[skorch.callbacks.EarlyStopping(patience=params["patience"])],
                              device = params["device"],
                              optimizer = params["optimizer"]
                              )
    
    skf = StratifiedKFold(Y_noise_train, n_folds=5, shuffle=True, random_state=None)
    
    init_module(clf.module, params["weight_mode"], params["bias"])
    
    metric = cross_val_score(clf, X_noise_train.values.astype(np.float32), Y_noise_train.values.astype(np.longlong), cv=skf, scoring="accuracy").mean()
    
    print(metric)
    print()    
    return -metric

def nn_stacking_f(params):
    
    print("mean", params["mean"])
    print("std", params["std"])
    print("lr", params["lr"])
    print("optimizer__weight_decay", params["optimizer__weight_decay"])
    print("criterion", params["criterion"])
    print("batch_size", params["batch_size"])
    print("optimizer__betas", params["optimizer__betas"])
    print("bias", params["bias"])
    print("weight_mode", params["weight_mode"])
    print("patience", params["patience"])
    print("input_nodes", params["input_nodes"])
    print("hidden_layers", params["hidden_layers"])
    print("hidden_nodes", params["hidden_nodes"])
    print("output_nodes", params["output_nodes"])
    print("percentage", params["percentage"])
    
    #这边的columns可以加入所有的选择部分
    #但是先试一下不加和全家之间的区别呢？
    #X_noise_train, Y_noise_train = noise_augment_data(params["mean"], params["std"], stacked_train, Y_train, columns=[i for i in range(0, stacked_train.columns.size)])
    X_noise_train, Y_noise_train = noise_augment_data(params["mean"], params["std"], stacked_train, Y_train, columns=[])
    
    clf = NeuralNetClassifier(lr = params["lr"],
                              optimizer__weight_decay = params["optimizer__weight_decay"],
                              criterion = params["criterion"],
                              batch_size = params["batch_size"],
                              optimizer__betas = params["optimizer__betas"],
                              #为了不再重新创建space,space_nodes就用下面的写法吧
                              module = create_module(stacked_train.columns.size, params["hidden_layers"], 
                                                      params["hidden_nodes"], params["output_nodes"], params["percentage"]),
                              max_epochs = params["max_epochs"],
                              callbacks=[skorch.callbacks.EarlyStopping(patience=params["patience"])],
                              device = params["device"],
                              optimizer = params["optimizer"]
                              )
    
    skf = StratifiedKFold(Y_noise_train, n_folds=5, shuffle=True, random_state=None)
    
    init_module(clf.module, params["weight_mode"], params["bias"])
    
    metric = cross_val_score(clf, X_noise_train.values.astype(np.float32), Y_noise_train.values.astype(np.longlong), cv=skf, scoring="accuracy").mean()
    
    print(metric)
    print()    
    return -metric
    
def parse_nodes(trials, space_nodes):
    
    trials_list =[]
    for item in trials.trials:
        trials_list.append(item)
    trials_list.sort(key=lambda item: item['result']['loss'])
    
    best_nodes = {}
    best_nodes["title"] = space_nodes["title"][trials_list[0]["misc"]["vals"]["title"][0]]
    best_nodes["path"] = space_nodes["path"][trials_list[0]["misc"]["vals"]["path"][0]]
    best_nodes["mean"] = space_nodes["mean"][trials_list[0]["misc"]["vals"]["mean"][0]]
    best_nodes["std"] = space_nodes["std"][trials_list[0]["misc"]["vals"]["std"][0]]
    best_nodes["batch_size"] = space_nodes["batch_size"][trials_list[0]["misc"]["vals"]["batch_size"][0]]
    best_nodes["criterion"] = space_nodes["criterion"][trials_list[0]["misc"]["vals"]["criterion"][0]]
    best_nodes["max_epochs"] = space_nodes["max_epochs"][trials_list[0]["misc"]["vals"]["max_epochs"][0]]

    best_nodes["lr"] = space_nodes["lr"][trials_list[0]["misc"]["vals"]["lr"][0]] 
    best_nodes["optimizer__betas"] = space_nodes["optimizer__betas"][trials_list[0]["misc"]["vals"]["optimizer__betas"][0]]
    best_nodes["optimizer__weight_decay"] = space_nodes["optimizer__weight_decay"][trials_list[0]["misc"]["vals"]["optimizer__weight_decay"][0]]
    best_nodes["weight_mode"] = space_nodes["weight_mode"][trials_list[0]["misc"]["vals"]["weight_mode"][0]]
    best_nodes["bias"] = space_nodes["bias"][trials_list[0]["misc"]["vals"]["bias"][0]]
    best_nodes["patience"] = space_nodes["patience"][trials_list[0]["misc"]["vals"]["patience"][0]]
    best_nodes["device"] = space_nodes["device"][trials_list[0]["misc"]["vals"]["device"][0]]
    best_nodes["optimizer"] = space_nodes["optimizer"][trials_list[0]["misc"]["vals"]["optimizer"][0]]
    
    #新添加的这些元素用于控制模型的结构
    best_nodes["input_nodes"] = space_nodes["input_nodes"][trials_list[0]["misc"]["vals"]["input_nodes"][0]]
    best_nodes["hidden_layers"] = space_nodes["hidden_layers"][trials_list[0]["misc"]["vals"]["hidden_layers"][0]]
    best_nodes["hidden_nodes"] = space_nodes["hidden_nodes"][trials_list[0]["misc"]["vals"]["hidden_nodes"][0]]
    best_nodes["output_nodes"] = space_nodes["output_nodes"][trials_list[0]["misc"]["vals"]["output_nodes"][0]]
    best_nodes["percentage"] = space_nodes["percentage"][trials_list[0]["misc"]["vals"]["percentage"][0]]

    return best_nodes

#我发现了这个程序的一个BUG咯气死我了怪不得没啥好结果
def parse_trials(trials, space_nodes, num):
    
    trials_list =[]
    for item in trials.trials:
        trials_list.append(item)
    trials_list.sort(key=lambda item: item['result']['loss'])
    
    #nodes = {}nodes如果在外面那么每次更新之后都是一样的咯
    nodes_list = []
    
    for i in range(0, num):
        nodes = {}
        nodes["title"] = space_nodes["title"][trials_list[i]["misc"]["vals"]["title"][0]]
        nodes["path"] = space_nodes["path"][trials_list[i]["misc"]["vals"]["path"][0]]
        nodes["mean"] = space_nodes["mean"][trials_list[i]["misc"]["vals"]["mean"][0]]
        nodes["std"] = space_nodes["std"][trials_list[i]["misc"]["vals"]["std"][0]]
        nodes["batch_size"] = space_nodes["batch_size"][trials_list[i]["misc"]["vals"]["batch_size"][0]]
        nodes["criterion"] = space_nodes["criterion"][trials_list[i]["misc"]["vals"]["criterion"][0]]
        nodes["max_epochs"] = space_nodes["max_epochs"][trials_list[i]["misc"]["vals"]["max_epochs"][0]]
        nodes["lr"] = space_nodes["lr"][trials_list[i]["misc"]["vals"]["lr"][0]] 
        nodes["optimizer__betas"] = space_nodes["optimizer__betas"][trials_list[i]["misc"]["vals"]["optimizer__betas"][0]]
        nodes["optimizer__weight_decay"] = space_nodes["optimizer__weight_decay"][trials_list[i]["misc"]["vals"]["optimizer__weight_decay"][0]]
        nodes["weight_mode"] = space_nodes["weight_mode"][trials_list[i]["misc"]["vals"]["weight_mode"][0]]
        nodes["bias"] = space_nodes["bias"][trials_list[i]["misc"]["vals"]["bias"][0]]
        nodes["patience"] = space_nodes["patience"][trials_list[i]["misc"]["vals"]["patience"][0]]
        nodes["device"] = space_nodes["device"][trials_list[i]["misc"]["vals"]["device"][0]]
        nodes["optimizer"] = space_nodes["optimizer"][trials_list[i]["misc"]["vals"]["optimizer"][0]]
        nodes["input_nodes"] = space_nodes["input_nodes"][trials_list[i]["misc"]["vals"]["input_nodes"][0]]
        nodes["hidden_layers"] = space_nodes["hidden_layers"][trials_list[i]["misc"]["vals"]["hidden_layers"][0]]
        nodes["hidden_nodes"] = space_nodes["hidden_nodes"][trials_list[i]["misc"]["vals"]["hidden_nodes"][0]]
        nodes["output_nodes"] = space_nodes["output_nodes"][trials_list[i]["misc"]["vals"]["output_nodes"][0]]
        nodes["percentage"] = space_nodes["percentage"][trials_list[i]["misc"]["vals"]["percentage"][0]]
        
        nodes_list.append(nodes)
    return nodes_list

#这个选择最佳模型的时候存在过拟合的风险
def nn_model_train(nodes, X_train_scaled, Y_train, max_evals=10):
    
    #由于神经网络模型初始化、dropout等的问题导致网络不够稳定
    #解决这个问题的办法就是多重复计算几次，选择其中靠谱的模型
    best_acc = 0.0
    best_model = 0.0
    for j in range(0, max_evals):
        
        clf = NeuralNetClassifier(lr = nodes["lr"],
                                  optimizer__weight_decay = nodes["optimizer__weight_decay"],
                                  criterion = nodes["criterion"],
                                  batch_size = nodes["batch_size"],
                                  optimizer__betas = nodes["optimizer__betas"],
                                  module = create_module(nodes["input_nodes"], nodes["hidden_layers"], 
                                                         nodes["hidden_nodes"], nodes["output_nodes"], nodes["percentage"]),
                                  max_epochs = nodes["max_epochs"],
                                  callbacks=[skorch.callbacks.EarlyStopping(patience=nodes["patience"])],
                                  device = nodes["device"],
                                  optimizer = nodes["optimizer"]
                                  )
        init_module(clf.module, nodes["weight_mode"], nodes["bias"])
        clf.fit(X_train_scaled.astype(np.float32), Y_train.astype(np.longlong))
            
        metric = cal_nnclf_acc(clf, X_train_scaled, Y_train)
        print_nnclf_acc(metric)
        best_model, best_acc, flag = record_best_model_acc(clf, metric, best_model, best_acc)        
    
    return best_model, best_acc

#我尽量用了一点别的方式减小模型选择时候可能带来的过拟合风险吧
#为了不改变原来参数的接口或者以最小修改代价的方式修改代码我想到了下面的办法咯
#下面的修改方式比我之前想到的修改方式感觉上还要高明一些的呢。。
def nn_model_train_validate(nodes, X_train_scaled, Y_train, max_evals=10):
    
    #我觉得0.12的设置有点多了，还有很多数据没用到呢，感觉这样子设置应该会好一些的吧？
    #X_split_train, X_split_test, Y_split_train, Y_split_test = train_test_split(X_train_scaled, Y_train, test_size=0.12, stratify=Y_train)
    X_split_train, X_split_test, Y_split_train, Y_split_test = train_test_split(X_train_scaled, Y_train, test_size=0.05, stratify=Y_train)
    #由于神经网络模型初始化、dropout等的问题导致网络不够稳定
    #解决这个问题的办法就是多重复计算几次，选择其中靠谱的模型
    best_acc = 0.0
    best_model = 0.0
    for j in range(0, max_evals):
        
        clf = NeuralNetClassifier(lr = nodes["lr"],
                                  optimizer__weight_decay = nodes["optimizer__weight_decay"],
                                  criterion = nodes["criterion"],
                                  batch_size = nodes["batch_size"],
                                  optimizer__betas = nodes["optimizer__betas"],
                                  module = create_module(nodes["input_nodes"], nodes["hidden_layers"], 
                                                         nodes["hidden_nodes"], nodes["output_nodes"], nodes["percentage"]),
                                  max_epochs = nodes["max_epochs"],
                                  callbacks=[skorch.callbacks.EarlyStopping(patience=nodes["patience"])],
                                  device = nodes["device"],
                                  optimizer = nodes["optimizer"]
                                  )
        init_module(clf.module, nodes["weight_mode"], nodes["bias"])
        clf.fit(X_split_train.astype(np.float32), Y_split_train.astype(np.longlong))
            
        metric = cal_nnclf_acc(clf, X_split_test, Y_split_test)
        print_nnclf_acc(metric)
        best_model, best_acc, flag = record_best_model_acc(clf, metric, best_model, best_acc)        
    
    return best_model, best_acc

def get_oof(nodes, X_train_scaled, Y_train, X_test_scaled, n_folds = 5, max_evals = 10):
    
    """K-fold stacking"""
    num_train, num_test = X_train_scaled.shape[0], X_test_scaled.shape[0]
    oof_train = np.zeros((num_train,)) 
    oof_test = np.zeros((num_test,))
    oof_test_all_fold = np.zeros((num_test, n_folds))
    train_acc = []
    valida_acc = []

    KF = KFold(n_splits =n_folds, shuffle=True)
    for i, (train_index, valida_index) in enumerate(KF.split(X_train_scaled)):
        #划分数据集
        X_split_train, Y_split_train = X_train_scaled[train_index], Y_train[train_index]
        X_split_valida, Y_split_valida = X_train_scaled[valida_index], Y_train[valida_index]
        
        best_model, best_acc = nn_model_train(nodes, X_split_train, Y_split_train, max_evals)
            
        acc1 = cal_nnclf_acc(best_model, X_split_train, Y_split_train)
        print_nnclf_acc(acc1)
        train_acc.append(acc1)
        acc2 = cal_nnclf_acc(best_model, X_split_valida, Y_split_valida)
        print_nnclf_acc(acc2)
        valida_acc.append(acc2)
        
        oof_train[valida_index] = best_model.predict(X_split_valida.astype(np.float32))
        oof_test_all_fold[:, i] = best_model.predict(X_test_scaled.astype(np.float32))
        
    oof_test = np.mean(oof_test_all_fold, axis=1)
    
    return oof_train, oof_test, best_model

def get_oof_validate(nodes, X_train_scaled, Y_train, X_test_scaled, n_folds = 5, max_evals = 10):
    
    """K-fold stacking"""
    num_train, num_test = X_train_scaled.shape[0], X_test_scaled.shape[0]
    oof_train = np.zeros((num_train,)) 
    oof_test = np.zeros((num_test,))
    oof_test_all_fold = np.zeros((num_test, n_folds))
    train_acc = []
    valida_acc = []

    KF = KFold(n_splits =n_folds, shuffle=True)
    for i, (train_index, valida_index) in enumerate(KF.split(X_train_scaled)):
        #划分数据集
        X_split_train, Y_split_train = X_train_scaled[train_index], Y_train[train_index]
        X_split_valida, Y_split_valida = X_train_scaled[valida_index], Y_train[valida_index]
        
        best_model, best_acc = nn_model_train_validate(nodes, X_split_train, Y_split_train, max_evals)
        
        #这里输出的是最佳模型的训练集和验证集上面的结果咯
        #很容易和上面的训练过程的最后一个输出重叠
        #这三个输出结果肯定是不一样的：
        #第一个输出和第二个输出的区别在于最佳模型和普通模型在训练集上面的输出
        #第二个输出和第三个输出的区别在于最佳模型在训练集和验证集上面的输出
        acc1 = cal_nnclf_acc(best_model, X_split_train, Y_split_train)
        print_nnclf_acc(acc1)
        train_acc.append(acc1)
        acc2 = cal_nnclf_acc(best_model, X_split_valida, Y_split_valida)
        print_nnclf_acc(acc2)
        valida_acc.append(acc2)
        
        oof_train[valida_index] = best_model.predict(X_split_valida.astype(np.float32))
        oof_test_all_fold[:, i] = best_model.predict(X_test_scaled.astype(np.float32))
        
    oof_test = np.mean(oof_test_all_fold, axis=1)
    
    return oof_train, oof_test, best_model

def stacked_features(nodes_list, X_train_scaled, Y_train, X_test_scaled, folds, max_evals):
    
    input_train = [] 
    input_test = []
    nodes_num = len(nodes_list)
    
    for i in range(0, nodes_num):
        oof_train, oof_test, best_model= get_oof(nodes_list[i], X_train_scaled.values, Y_train.values, X_test_scaled.values, folds, max_evals)
        input_train.append(oof_train)
        input_test.append(oof_test)
    
    stacked_train = np.concatenate([f.reshape(-1, 1) for f in input_train], axis=1)
    stacked_test = np.concatenate([f.reshape(-1, 1) for f in input_test], axis=1)
    
    stacked_train = pd.DataFrame(stacked_train)
    stacked_test = pd.DataFrame(stacked_test)
    return stacked_train, stacked_test

def stacked_features_validate(nodes_list, X_train_scaled, Y_train, X_test_scaled, folds, max_evals):
    
    input_train = [] 
    input_test = []
    nodes_num = len(nodes_list)
    
    for i in range(0, nodes_num):
        oof_train, oof_test, best_model= get_oof_validate(nodes_list[i], X_train_scaled.values, Y_train.values, X_test_scaled.values, folds, max_evals)
        input_train.append(oof_train)
        input_test.append(oof_test)
    
    stacked_train = np.concatenate([f.reshape(-1, 1) for f in input_train], axis=1)
    stacked_test = np.concatenate([f.reshape(-1, 1) for f in input_test], axis=1)
    
    stacked_train = pd.DataFrame(stacked_train)
    stacked_test = pd.DataFrame(stacked_test)
    return stacked_train, stacked_test

#这个选择最佳模型的时候存在过拟合的风险
def nn_stacking_predict(best_nodes, nodes_list, stacked_train, Y_train, stacked_test, max_evals=10):
    
    best_acc = 0.0
    best_model = 0.0

    #我已经将这份代码的best_nodes["title"]由原来的titanic改为stacked_titanic作为新版本
    if (exist_files(best_nodes["title"])):
        #在这里暂时不保存stakced_train以及stacked_test吧
        best_model = load_best_model(best_nodes["title"]+"_"+str(len(nodes_list)))
        best_acc = cal_nnclf_acc(best_model, stacked_train.values, Y_train.values)
         
    for i in range(0, max_evals):
        
        #这边不是很想用nn_model_train代替下面的函数代码
        #因为这下面的代码还涉及到预测输出的问题不好修改
        print(str(i+1)+"/"+str(max_evals)+" prediction progress have been made.")
        
        clf = NeuralNetClassifier(lr = best_nodes["lr"],
                                  optimizer__weight_decay = best_nodes["optimizer__weight_decay"],
                                  criterion = best_nodes["criterion"],
                                  batch_size = best_nodes["batch_size"],
                                  optimizer__betas = best_nodes["optimizer__betas"],
                                  module = create_module(stacked_train.columns.size, best_nodes["hidden_layers"], 
                                                         best_nodes["hidden_nodes"], best_nodes["output_nodes"], best_nodes["percentage"]),
                                  max_epochs = best_nodes["max_epochs"],
                                  callbacks = [skorch.callbacks.EarlyStopping(patience=best_nodes["patience"])],
                                  device = best_nodes["device"],
                                  optimizer = best_nodes["optimizer"]
                                  )
        
        init_module(clf.module, best_nodes["weight_mode"], best_nodes["bias"])
        
        clf.fit(stacked_train.values.astype(np.float32), Y_train.values.astype(np.longlong))
        
        metric = cal_nnclf_acc(clf, stacked_train.values, Y_train.values)
        print_nnclf_acc(metric)
        
        best_model, best_acc, flag = record_best_model_acc(clf, metric, best_model, best_acc)
    
        if (flag):
            #这个版本的best_model终于是全局的版本咯，真是开森呢。。
            save_best_model(best_model, best_nodes["title"]+"_"+str(len(nodes_list)))
            Y_pred = best_model.predict(stacked_test.values.astype(np.float32))
            
            data = {"PassengerId":data_test["PassengerId"], "Survived":Y_pred}
            output = pd.DataFrame(data = data)
            
            output.to_csv(best_nodes["path"], index=False)
            print("prediction file has been written.")
        print()
     
    print("the best accuracy rate of the model on the whole train dataset is:", best_acc)
    print()
    return best_model, Y_pred
   
#lr没有超参搜索而且没有进行过cv怎么可能会取得好成绩呢？ 
def lr_stacking_predict(stacked_train, Y_train, stacked_test, max_evals=50):
    
    best_acc = 0.0
    best_model = 0.0
       
    #这里并不需要保存最佳的模型吧，只需要将stacked_train之类的数据记录下来就行了
    for i in range(0, max_evals):
        
        print(str(i+1)+"/"+str(max_evals)+" prediction progress have been made.")
        
        #这边是不是需要加入一些随机化的因素或者其他因素？？
        clf = LogisticRegression()        
        clf.fit(stacked_train, Y_train)
        
        metric = cal_nnclf_acc(clf, stacked_train.values, Y_train.values)
        print_nnclf_acc(metric)
        
        best_model, best_acc, flag = record_best_model_acc(clf, metric, best_model, best_acc)
    
        if (flag):
            #这个版本的best_model终于是全局的版本咯，真是开森呢。。
            save_best_model(best_model, best_nodes["title"]+"_"+str(len(nodes_list)))
            Y_pred = best_model.predict(stacked_test.values.astype(np.float32))
            
            data = {"PassengerId":data_test["PassengerId"], "Survived":Y_pred}
            output = pd.DataFrame(data = data)
            
            output.to_csv(best_nodes["path"], index=False)
            print("prediction file has been written.")
        print()
     
    print("the best accuracy rate of the model on the whole train dataset is:", best_acc)
    print()
    return best_model, Y_pred

#lr进行了超参搜索选出最好的结果进行预测咯 
def lr_stacking_cv_predict(stacked_train, Y_train, stacked_test, max_evals=2000):
    
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=max_evals)
    random_search.fit(stacked_train, Y_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)

    save_best_model(random_search.best_estimator_, best_nodes["title"]+"_"+str(len(nodes_list)))
    Y_pred = random_search.best_estimator_.predict(stacked_test.values.astype(np.float32))
            
    data = {"PassengerId":data_test["PassengerId"], "Survived":Y_pred}
    output = pd.DataFrame(data = data)
            
    output.to_csv(best_nodes["path"], index=False)
    print("prediction file has been written.")
     
    print("the best accuracy rate of the model on the whole train dataset is:", best_acc)
    print()
    return random_search.best_estimator_, Y_pred

#lr进行了超参搜索选出最好的结果进行预测咯 
def lr_stacking_cv_predict_path(stacked_train, Y_train, stacked_test, path, max_evals=2000):
    
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=max_evals)
    random_search.fit(stacked_train, Y_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)

    #save_best_model(random_search.best_estimator_, best_nodes["title"]+"_"+str(len(nodes_list)))
    Y_pred = random_search.best_estimator_.predict(stacked_test.values.astype(np.float32))
            
    data = {"PassengerId":data_test["PassengerId"], "Survived":Y_pred}
    output = pd.DataFrame(data = data)
            
    output.to_csv(path, index=False)
    print("prediction file has been written.")
     
    print("the best accuracy rate of the model on the whole train dataset is:", best_acc)
    print()
    return random_search.best_estimator_, Y_pred

def tpot_stacking_predict(stacked_train, Y_train, stacked_test, generations=100, population_size=100):
    
    tpot = TPOTClassifier(generations=generations, population_size=population_size, verbosity = 2)
    tpot.fit(stacked_train, Y_train)
    best_acc = tpot.score(stacked_train, Y_train)
    Y_pred = tpot.predict(stacked_test)
    best_model = tpot
         
    save_best_model(best_model.fitted_pipeline_, best_nodes["title"]+"_"+"tpot")
    Y_pred = best_model.predict(stacked_test)
            
    data = {"PassengerId":data_test["PassengerId"], "Survived":Y_pred}
    output = pd.DataFrame(data = data)
            
    output.to_csv(best_nodes["path"], index=False)
    print("prediction file has been written.")
            
    print("the best accuracy rate of the model on the whole train dataset is:", best_acc)
    print()
    return best_model, Y_pred
    
#现在直接利用经验参数值进行搜索咯，这样可以节约计算资源
space = {"title":hp.choice("title", ["stacked_titanic"]),
         "path":hp.choice("path", ["C:/Users/win7/Desktop/Titanic_Prediction.csv"]),
         "mean":hp.choice("mean", [0]),
         "std":hp.choice("std", [0.10]),
         "max_epochs":hp.choice("max_epochs",[400]),
         "patience":hp.choice("patience", [4,5,6,7,8,9,10]),
         "lr":hp.choice("lr", [0.00001, 0.00002, 0.00003, 0.00004, 0.00005, 0.00006, 0.00007, 0.00008, 0.00009, 0.00010,
                               0.00011, 0.00012, 0.00013, 0.00014, 0.00015, 0.00016, 0.00017, 0.00018, 0.00019, 0.00020,
                               0.00021, 0.00022, 0.00023, 0.00024, 0.00025, 0.00026, 0.00027, 0.00028, 0.00029, 0.00030,
                               0.00031, 0.00032, 0.00033, 0.00034, 0.00035, 0.00036, 0.00037, 0.00038, 0.00039, 0.00040,
                               0.00041, 0.00042, 0.00043, 0.00044, 0.00045, 0.00046, 0.00047, 0.00048, 0.00049, 0.00050,
                               0.00051, 0.00052, 0.00053, 0.00054, 0.00055, 0.00056, 0.00057, 0.00058, 0.00059, 0.00060,
                               0.00061, 0.00062, 0.00063, 0.00064, 0.00065, 0.00066, 0.00067, 0.00068, 0.00069, 0.00070,
                               0.00071, 0.00072, 0.00073, 0.00074, 0.00075, 0.00076, 0.00077, 0.00078, 0.00079, 0.00080,
                               0.00081, 0.00082, 0.00083, 0.00084, 0.00085, 0.00086, 0.00087, 0.00088, 0.00089, 0.00090,
                               0.00091, 0.00092, 0.00093, 0.00094, 0.00095, 0.00096, 0.00097, 0.00098, 0.00099, 0.00100,
                               0.00101, 0.00102, 0.00103, 0.00104, 0.00105, 0.00106, 0.00107, 0.00108, 0.00109, 0.00110,
                               0.00111, 0.00112, 0.00113, 0.00114, 0.00115, 0.00116, 0.00117, 0.00118, 0.00119, 0.00120,
                               0.00121, 0.00122, 0.00123, 0.00124, 0.00125, 0.00126, 0.00127, 0.00128, 0.00129, 0.00130,
                               0.00131, 0.00132, 0.00133, 0.00134, 0.00135, 0.00136, 0.00137, 0.00138, 0.00139, 0.00140,
                               0.00141, 0.00142, 0.00143, 0.00144, 0.00145, 0.00146, 0.00147, 0.00148, 0.00149, 0.00150,
                               0.00151, 0.00152, 0.00153, 0.00154, 0.00155, 0.00156, 0.00157, 0.00158, 0.00159, 0.00160]),  
         "optimizer__weight_decay":hp.choice("optimizer__weight_decay",[0.000]),  
         "criterion":hp.choice("criterion", [torch.nn.NLLLoss, torch.nn.CrossEntropyLoss]),

         "batch_size":hp.choice("batch_size", [64, 128, 256, 512, 1024]),
         "optimizer__betas":hp.choice("optimizer__betas",
                                      [[0.88, 0.9991], [0.88, 0.9993], [0.88, 0.9995], [0.88, 0.9997], [0.88, 0.9999],
                                       [0.90, 0.9991], [0.90, 0.9993], [0.90, 0.9995], [0.90, 0.9997], [0.90, 0.9999],
                                       [0.92, 0.9991], [0.92, 0.9993], [0.92, 0.9995], [0.92, 0.9997], [0.92, 0.9999]]),
         "input_nodes":hp.choice("input_nodes", [9]),
         "hidden_layers":hp.choice("hidden_layers", [0, 1, 2, 3, 4, 5, 6, 7, 8]), 
         "hidden_nodes":hp.choice("hidden_nodes", [5, 10, 15, 20, 25, 30, 35, 40, 
                                                   45, 50, 55, 60, 65, 70, 75, 80, 
                                                   85, 90, 95, 100, 105, 110, 115]), 
         "output_nodes":hp.choice("output_nodes", [2]),
         "percentage":hp.choice("percentage", [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45]),
         "weight_mode":hp.choice("weight_mode", [1]),
         "bias":hp.choice("bias", [0]),
         "device":hp.choice("device", ["cpu"]),
         "optimizer":hp.choice("optimizer", [torch.optim.Adam])
         }

space_nodes = {"title":["stacked_titanic"],
               "path":["C:/Users/win7/Desktop/Titanic_Prediction.csv"],
               "mean":[0],
               "std":[0.10],
               "max_epochs":[400],
               "patience":[4,5,6,7,8,9,10],
               "lr":[0.00001, 0.00002, 0.00003, 0.00004, 0.00005, 0.00006, 0.00007, 0.00008, 0.00009, 0.00010,
                     0.00011, 0.00012, 0.00013, 0.00014, 0.00015, 0.00016, 0.00017, 0.00018, 0.00019, 0.00020,
                     0.00021, 0.00022, 0.00023, 0.00024, 0.00025, 0.00026, 0.00027, 0.00028, 0.00029, 0.00030,
                     0.00031, 0.00032, 0.00033, 0.00034, 0.00035, 0.00036, 0.00037, 0.00038, 0.00039, 0.00040,
                     0.00041, 0.00042, 0.00043, 0.00044, 0.00045, 0.00046, 0.00047, 0.00048, 0.00049, 0.00050,
                     0.00051, 0.00052, 0.00053, 0.00054, 0.00055, 0.00056, 0.00057, 0.00058, 0.00059, 0.00060,
                     0.00061, 0.00062, 0.00063, 0.00064, 0.00065, 0.00066, 0.00067, 0.00068, 0.00069, 0.00070,
                     0.00071, 0.00072, 0.00073, 0.00074, 0.00075, 0.00076, 0.00077, 0.00078, 0.00079, 0.00080,
                     0.00081, 0.00082, 0.00083, 0.00084, 0.00085, 0.00086, 0.00087, 0.00088, 0.00089, 0.00090,
                     0.00091, 0.00092, 0.00093, 0.00094, 0.00095, 0.00096, 0.00097, 0.00098, 0.00099, 0.00100,
                     0.00101, 0.00102, 0.00103, 0.00104, 0.00105, 0.00106, 0.00107, 0.00108, 0.00109, 0.00110,
                     0.00111, 0.00112, 0.00113, 0.00114, 0.00115, 0.00116, 0.00117, 0.00118, 0.00119, 0.00120,
                     0.00121, 0.00122, 0.00123, 0.00124, 0.00125, 0.00126, 0.00127, 0.00128, 0.00129, 0.00130,
                     0.00131, 0.00132, 0.00133, 0.00134, 0.00135, 0.00136, 0.00137, 0.00138, 0.00139, 0.00140,
                     0.00141, 0.00142, 0.00143, 0.00144, 0.00145, 0.00146, 0.00147, 0.00148, 0.00149, 0.00150,
                     0.00151, 0.00152, 0.00153, 0.00154, 0.00155, 0.00156, 0.00157, 0.00158, 0.00159, 0.00160],
               "optimizer__weight_decay":[0.000],
               "criterion":[torch.nn.NLLLoss, torch.nn.CrossEntropyLoss],
               "batch_size":[64, 128, 256, 512, 1024],
               "optimizer__betas":[[0.88, 0.9991], [0.88, 0.9993], [0.88, 0.9995], [0.88, 0.9997], [0.88, 0.9999],
                                   [0.90, 0.9991], [0.90, 0.9993], [0.90, 0.9995], [0.90, 0.9997], [0.90, 0.9999],
                                   [0.92, 0.9991], [0.92, 0.9993], [0.92, 0.9995], [0.92, 0.9997], [0.92, 0.9999]],
               "input_nodes":[9],
               "hidden_layers":[0, 1, 2, 3, 4, 5, 6, 7, 8], 
               "hidden_nodes":[5, 10, 15, 20, 25, 30, 35, 40, 
                               45, 50, 55, 60, 65, 70, 75, 80, 
                               85, 90, 95, 100, 105, 110, 115], 
               "output_nodes":[2],
               "percentage":[0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45],
               "weight_mode":[1],
               "bias":[0],
               "device":["cpu"],
               "optimizer":[torch.optim.Adam]
               }

#其实本身不需要best_nodes主要是为了快速测试
#不然每次超参搜索的best_nodes效率太低了吧
best_nodes = {"title":"stacked_titanic",
              "path":"C:/Users/win7/Desktop/Titanic_Prediction.csv",
              "mean":0,
              "std":0.1,
              "max_epochs":400,
              "patience":5,
              "lr":0.00010,
              "optimizer__weight_decay":0.005,
              "criterion":torch.nn.CrossEntropyLoss,
              "batch_size":128,
              "optimizer__betas":[0.86, 0.999],
              "input_nodes":9,
              "hidden_layers":3, 
              "hidden_nodes":60, 
              "output_nodes":2,
              "percentage":0.15,
              "weight_mode":1,
              "bias":0.0,
              "device":"cpu",
              "optimizer":torch.optim.Adam
              }

"""
#下面的模型居然取得了85.29%的正确率，我真的是看到了希望了，看来stacking才是王道呀
#the best accuracy rate of the model on the whole train dataset is: 0.8529741863075196
#有的地方有.values有的地方又没有这个感觉很凌乱还是都用吧
#其实我早就应该知道的，直接把stacked_train之类的变成df吧
algo = partial(tpe.suggest, n_startup_jobs=10)
#好像这边重复增加超参节点结果居然没有改变耶？5个节点结果差不多的效果
#感觉直接增加重复的次数是能够得到最大的提升的意思咯，我试一下提升比较有限吧
#增加计算次数提升不是很明显，但是增加节点数目提升还是有点明显哦
nodes_list = [best_nodes, best_nodes, best_nodes, best_nodes, best_nodes, 
              best_nodes, best_nodes, best_nodes, best_nodes, best_nodes,
              best_nodes, best_nodes, best_nodes, best_nodes, best_nodes,
              best_nodes, best_nodes, best_nodes, best_nodes, best_nodes,
              best_nodes, best_nodes, best_nodes, best_nodes, best_nodes]
stacked_train, stacked_test = stacked_features(nodes_list, X_train_scaled, Y_train, X_test_scaled, 5, 10)
stacked_trials = Trials()
#既然最后还是分裂为两个版本所以这些不需要了吧
#X_train_f = stacked_train
#Y_train_f = Y_train
#下面的这个写法不行，因为我是真的可能使用以前的trials，如果修改了就不好了吧
#space["input_nodes"]=hp.choice("input_nodes", [len(nodes_list)])
best_stacked_params = fmin(nn_stacking_f, space, algo=algo, max_evals=20, trials=stacked_trials)
print_best_params_acc(stacked_trials)
best_nodes = parse_nodes(stacked_trials, space_nodes)
save_inter_params(stacked_trials, space_nodes, best_nodes, "stacked_titanic")
#下面这函数一直报错，花了我很多的时间才知道是之前存储的stacked_titanic_best_model的问题
nn_stacking_predict(best_nodes, nodes_list, stacked_train, Y_train, stacked_test, 20)
"""

"""
#反正现在要被保存的东西都在下面这里了吧，可以参考这里设置的数字
start_time = datetime.datetime.now()
algo = partial(tpe.suggest, n_startup_jobs=10)

nodes_list = [best_nodes, best_nodes]
stacked_train, stacked_test = stacked_features(nodes_list, X_train_scaled, Y_train, X_test_scaled, 5, 20)

stacked_trials = Trials()
#这个数字可以再开大一点吧，除了节点数目其余数字可以不开大了。
best_stacked_params = fmin(nn_stacking_f, space, algo=algo, max_evals=20, trials=stacked_trials)
print_best_params_acc(stacked_trials)
best_nodes = parse_nodes(stacked_trials, space_nodes)
save_inter_params(stacked_trials, space_nodes, best_nodes, "stacked_titanic")
save_stacked_dataset(stacked_train, stacked_test, "stacked_titanic")

nn_stacking_predict(best_nodes, nodes_list, stacked_train, Y_train, stacked_test, 20)
end_time = datetime.datetime.now()
print("time cost", (end_time - start_time))
"""

"""
#下面的保存版本的代码算是调通实现了吧
start_time = datetime.datetime.now()

nodes_list = [best_nodes, best_nodes]
stacked_train, stacked_test = stacked_features(nodes_list, X_train_scaled, Y_train, X_test_scaled, 5, 5)

stacked_trials = Trials()
algo = partial(tpe.suggest, n_startup_jobs=10)
best_stacked_params = fmin(nn_stacking_f, space, algo=algo, max_evals=5, trials=stacked_trials)

best_nodes = parse_nodes(stacked_trials, space_nodes)
save_inter_params(stacked_trials, space_nodes, best_nodes, "stacked_titanic")
save_stacked_dataset(stacked_train, stacked_test, "stacked_titanic")

nn_stacking_predict(best_nodes, nodes_list, stacked_train, Y_train, stacked_test, 5)
end_time = datetime.datetime.now()
print("time cost", (end_time - start_time))

stacked_train, stacked_test = load_stacked_dataset("stacked_titanic")
best_model = load_best_model("stacked_titanic_2")
print(cal_nnclf_acc(best_model, stacked_train.values, Y_train.values))
"""

"""
#下面的保存版本的代码算是调通实现了吧
#下面的这份代码算是一个完整的版本吧，但是下面的代码存在一个问题
#第二层一般不再使用比较简单的模型这样能够防止过拟合咯
start_time = datetime.datetime.now()

trials = Trials()
algo = partial(tpe.suggest, n_startup_jobs=10)
best_params = fmin(nn_f, space, algo=algo, max_evals=7000, trials=trials)

best_nodes = parse_nodes(trials, space_nodes)
save_inter_params(trials, space_nodes, best_nodes, "titanic")

nodes_list = parse_trials(trials, space_nodes, 9)
#nodes_list = [best_nodes, best_nodes, best_nodes, best_nodes,
#              best_nodes, best_nodes, best_nodes, best_nodes, best_nodes]
stacked_train, stacked_test = stacked_features(nodes_list, X_train_scaled, Y_train, X_test_scaled, 5, 25)

stacked_trials = Trials()
algo = partial(tpe.suggest, n_startup_jobs=10)
best_stacked_params = fmin(nn_stacking_f, space, algo=algo, max_evals=100, trials=stacked_trials)

best_nodes = parse_nodes(stacked_trials, space_nodes)
save_inter_params(stacked_trials, space_nodes, best_nodes, "stacked_titanic")
save_stacked_dataset(stacked_train, stacked_test, "stacked_titanic")

nn_stacking_predict(best_nodes, nodes_list, stacked_train, Y_train, stacked_test, 50)
end_time = datetime.datetime.now()
print("time cost", (end_time - start_time))
"""

"""
stacked_train, stacked_test = load_stacked_dataset("stacked_titanic")
best_model = load_best_model("stacked_titanic_2")
print(cal_nnclf_acc(best_model, stacked_train.values, Y_train.values))
"""

"""
#下面的代码算是7000次测试的前戏吧，这样就可以无忧的执行大计算咯
start_time = datetime.datetime.now()

trials = Trials()
algo = partial(tpe.suggest, n_startup_jobs=10)
best_params = fmin(nn_f, space, algo=algo, max_evals=10, trials=trials)

best_nodes = parse_nodes(trials, space_nodes)
save_inter_params(trials, space_nodes, best_nodes, "titanic")

nodes_list = parse_trials(trials, space_nodes, 9)
#nodes_list = [best_nodes, best_nodes, best_nodes, best_nodes,
#              best_nodes, best_nodes, best_nodes, best_nodes, best_nodes]
stacked_train, stacked_test = stacked_features(nodes_list, X_train_scaled, Y_train, X_test_scaled, 5, 10)
save_stacked_dataset(stacked_train, stacked_test, "stacked_titanic")

lr_stacking_predict(stacked_train, Y_train, stacked_test, 10)

end_time = datetime.datetime.now()
print("time cost", (end_time - start_time))
"""

"""
#下面的代码应该是类似最终实现的版本咯
#这个版本在第二层还是要使用逻辑回归咯
#lr在大计算中表现的结果确实太糟糕，
#感觉只能够在第二层使用tpot或者
start_time = datetime.datetime.now()

trials = Trials()
algo = partial(tpe.suggest, n_startup_jobs=10)
best_params = fmin(nn_f, space, algo=algo, max_evals=7000, trials=trials)

best_nodes = parse_nodes(trials, space_nodes)
save_inter_params(trials, space_nodes, best_nodes, "titanic")

nodes_list = parse_trials(trials, space_nodes, 9)
#nodes_list = [best_nodes, best_nodes, best_nodes, best_nodes,
#              best_nodes, best_nodes, best_nodes, best_nodes, best_nodes]
stacked_train, stacked_test = stacked_features(nodes_list, X_train_scaled, Y_train, X_test_scaled, 5, 50)
save_stacked_dataset(stacked_train, stacked_test, "stacked_titanic")

lr_stacking_predict(stacked_train, Y_train, stacked_test, 100)

end_time = datetime.datetime.now()
print("time cost", (end_time - start_time))
"""

"""
#就目前的结果而言，下面的代码应该是目前能够取得的最佳效果之一
#这份代码主要的问题就是计算量过大了，涉及到了两次超参搜索
start_time = datetime.datetime.now()

trials = Trials()
algo = partial(tpe.suggest, n_startup_jobs=10)
best_params = fmin(nn_f, space, algo=algo, max_evals=7000, trials=trials)

best_nodes = parse_nodes(trials, space_nodes)
save_inter_params(trials, space_nodes, best_nodes, "titanic")

#nodes_list = parse_trials(trials, space_nodes, 9)
nodes_list = [best_nodes, best_nodes, best_nodes, best_nodes,
              best_nodes, best_nodes, best_nodes, best_nodes, best_nodes]
stacked_train, stacked_test = stacked_features(nodes_list, X_train_scaled, Y_train, X_test_scaled, 5, 70)
save_stacked_dataset(stacked_train, stacked_test, "stacked_titanic")

stacked_trials = Trials()
algo = partial(tpe.suggest, n_startup_jobs=10)
best_stacked_params = fmin(nn_stacking_f, space, algo=algo, max_evals=7000, trials=stacked_trials)

best_nodes = parse_nodes(stacked_trials, space_nodes)
save_inter_params(stacked_trials, space_nodes, best_nodes, "stacked_titanic")

nn_stacking_predict(best_nodes, nodes_list, stacked_train, Y_train, stacked_test, 1000)
end_time = datetime.datetime.now()
print("time cost", (end_time - start_time))
"""

"""
#下面这份代码的模板能够避免第二次超参搜索
#具体地说就是将第一次的搜索之后stacking之后的输出
#作为新的特征，然后是用tpot的方式对这个特征进行搜索咯
#这样做的好处在于一方面能够节约第二次超参搜索的时间
#并且避免了第一次stacking之前做特征工程的时间咯
start_time = datetime.datetime.now()

trials = Trials()
algo = partial(tpe.suggest, n_startup_jobs=10)
best_params = fmin(nn_f, space, algo=algo, max_evals=2, trials=trials)

best_nodes = parse_nodes(trials, space_nodes)
save_inter_params(trials, space_nodes, best_nodes, "titanic")

#nodes_list = parse_trials(trials, space_nodes, 9)
nodes_list = [best_nodes, best_nodes, best_nodes, best_nodes,
              best_nodes, best_nodes, best_nodes, best_nodes, best_nodes]
stacked_train, stacked_test = stacked_features(nodes_list, X_train_scaled, Y_train, X_test_scaled, 5, 1)
save_stacked_dataset(stacked_train, stacked_test, "stacked_titanic")

tpot_stacking_predict(stacked_train, Y_train, stacked_test, generations=3, population_size=3)
end_time = datetime.datetime.now()
print("time cost", (end_time - start_time))
"""

"""
#下面这份代码的模板是我目前最新的实践结果
#主要成果在于发现了第二层使用随机超参搜索的逻辑回归验证集效果最好
#除此之前将节点数目修改为了三个，感觉三四个节点的情况超参逻辑回归要好一点。。
#然后就进行逻辑回归的超参搜索咯，目前的版本大致是这个样子的。
start_time = datetime.datetime.now()

trials = Trials()
algo = partial(tpe.suggest, n_startup_jobs=10)
best_params = fmin(nn_f, space, algo=algo, max_evals=2, trials=trials)

best_nodes = parse_nodes(trials, space_nodes)
save_inter_params(trials, space_nodes, best_nodes, "titanic")

#nodes_list = parse_trials(trials, space_nodes, 9)
nodes_list = [best_nodes, best_nodes, best_nodes]
stacked_train, stacked_test = stacked_features_validate(nodes_list, X_train_scaled, Y_train, X_test_scaled, 5, 1)
save_stacked_dataset(stacked_train, stacked_test, "stacked_titanic")

lr_stacking_cv_predict(stacked_train, Y_train, stacked_test, max_evals=10)
end_time = datetime.datetime.now()
print("time cost", (end_time - start_time))
"""

"""
#下面对于这多种设置参数的方式进行一点比较咯
#经过对下列的实验数据的分析最后我可以得到的结果大致是：
#1）使用stacked_features_validate系列函数比使用stacked_features的结果泛化性能更好。
#可以在固定搜索次数和节点数目的情况下，分别比较训练集和验证集上面的结果。
#具体的，对比每组数据（4行）的上下2行得到该结论，_validate赢得了75%的battle。
#2）使用9个节点得到的结果泛化性能更好。
#根据上面1）的结果，我们只考虑使用stacked_features_validate系列函数的情况。
#可以在固定搜索次数的情况下，分别比较训练集和验证集上面的结果。
#具体的，对比相同搜索次数的每组数据（4行）的后两行，9个节点赢得了64%的battle。
#3)设置20次左右的搜索次数得到的结果泛化性能更好。
#根据上面1）的结果，我们只考虑使用stacked_features_validate系列函数的情况。
#可以在固定搜索节点数目的情况下，分别比较训练集和验证集上面的结果。
#具体的，对比相同节点数目的每组数据（4行）的后两行，20次搜索次数赢得了40%的battle。
#这个结果其实也能比较符合我的常识啦，顺便想到是否可以通过无脑增加节点数目，进一步提高模型性能呢？
#但是现在还是先把提交一次这个部分的答案吧
0.8229854689564069 #10 2
0.8432835820895522
0.8229854689564069 
0.8507462686567164

0.8190224570673712 #10 3
0.8507462686567164
0.8203434610303831
0.8283582089552238

0.8163804491413474 #10 9
0.835820895522388
0.8322324966974901
0.8582089552238806

0.8044914134742405 #15 2
0.8059701492537313
0.809775429326288
0.7985074626865671

0.8124174372523117 #15 3
0.8432835820895522
0.8190224570673712
0.8432835820895522

0.8269484808454426 #15 9
0.8059701492537313
0.8322324966974901
0.8507462686567164

0.809775429326288  #20 2
0.8432835820895522
0.8137384412153237
0.8582089552238806

0.808454425363276  #20 3
0.8208955223880597
0.8163804491413474
0.8507462686567164

0.8177014531043593 #20 9
0.8507462686567164
0.8243064729194187
0.8507462686567164

0.8124174372523117 #25 2
0.8507462686567164
0.8243064729194187
0.7985074626865671

0.8124174372523117 #25 3
0.8134328358208955
0.8269484808454426
0.8283582089552238

0.821664464993395  #25 9
0.8283582089552238
0.8044914134742405
0.835820895522388

0.8124174372523117 #30 2
0.8208955223880597
0.8137384412153237
0.835820895522388

0.8203434610303831 #30 3
0.8582089552238806
0.8163804491413474
0.8432835820895522

0.8229854689564069 #30 9
0.8283582089552238
0.8269484808454426
0.835820895522388

0.8031704095112285 #40 2
0.8134328358208955
0.8177014531043593
0.8507462686567164

0.809775429326288 #40 3
0.835820895522388
0.8203434610303831
0.7985074626865671

0.8203434610303831 #40 9
0.8432835820895522
0.8229854689564069
0.835820895522388

0.8229854689564069 #50 2
0.8507462686567164
0.808454425363276
0.8134328358208955

0.809775429326288  #50 3
0.8507462686567164
0.8137384412153237
0.8208955223880597

0.8124174372523117 #50 9
0.8208955223880597
0.8137384412153237
0.8432835820895522

0.8190224570673712 #10 2
0.835820895522388
0.8256274768824307
0.8432835820895522

0.8269484808454426 #10 3
0.8432835820895522
0.8229854689564069
0.8432835820895522

0.821664464993395 #10 9
0.8134328358208955
0.821664464993395
0.8507462686567164

0.8058124174372523 #15 2
0.8059701492537313
0.8150594451783355
0.8432835820895522

0.8190224570673712 #15 3
0.8432835820895522
0.821664464993395
0.835820895522388

0.821664464993395 #15 9
0.835820895522388
0.8243064729194187
0.8507462686567164

0.8229854689564069 #20 2
0.835820895522388
0.8203434610303831
0.8507462686567164

0.8110964332892999 #20 3
0.835820895522388
0.821664464993395
0.8507462686567164

0.8348745046235139 #20 9
0.835820895522388
0.8243064729194187
0.835820895522388

0.8203434610303831 #25 2
0.8432835820895522
0.8229854689564069
0.8432835820895522

0.8256274768824307 #25 3
0.8432835820895522
0.8163804491413474
0.8432835820895522

0.8163804491413474 #25 9
0.835820895522388
0.8282694848084544
0.8582089552238806

0.8177014531043593 #30 2
0.8283582089552238
0.8229854689564069
0.835820895522388

0.821664464993395 #30 3
0.8432835820895522
0.8163804491413474
0.835820895522388

0.8243064729194187 #30 9
0.8283582089552238
0.821664464993395
0.8507462686567164

0.809775429326288 #40 2
0.835820895522388
0.8243064729194187
0.8432835820895522

0.8190224570673712 #40 3
0.835820895522388
0.8229854689564069
0.8507462686567164

0.8190224570673712 #40 9
0.835820895522388
0.8295904887714664
0.8507462686567164

0.8124174372523117 #50 2
0.8432835820895522
0.8137384412153237
0.8432835820895522

0.8243064729194187 #50 3
0.835820895522388
0.821664464993395
0.835820895522388

0.8269484808454426 #50 9
0.835820895522388
0.8309114927344782
0.8507462686567164

0.809775429326288 #10 2
0.8432835820895522
0.8058124174372523
0.8731343283582089

0.8071334214002642 #10 3
0.835820895522388
0.8110964332892999
0.8059701492537313

0.8124174372523117 #10 9
0.8582089552238806
0.8177014531043593
0.8656716417910447

0.7965653896961691 #15 2
0.8283582089552238
0.808454425363276
0.8656716417910447

0.8058124174372523 #15 3
0.8134328358208955
0.8071334214002642
0.8656716417910447

0.8163804491413474 #15 9
0.8432835820895522
0.8177014531043593
0.8731343283582089

0.8005284015852048 #20 2
0.8432835820895522
0.8044914134742405
0.8283582089552238

0.8005284015852048 #20 3
0.8283582089552238
0.8058124174372523
0.8432835820895522

0.808454425363276 #20 9
0.835820895522388
0.8229854689564069
0.8955223880597015

0.8124174372523117 #25 2
0.8507462686567164
0.809775429326288
0.8955223880597015

0.8163804491413474 #25 3
0.835820895522388
0.8124174372523117
0.8283582089552238

0.8150594451783355 #25 9
0.8432835820895522
0.8229854689564069
0.8656716417910447

0.7952443857331571 #30 2
0.8283582089552238
0.809775429326288
0.8656716417910447

0.797886393659181 #30 3
0.8507462686567164
0.8137384412153237
0.8731343283582089

0.8163804491413474 #30 9
0.8507462686567164
0.8137384412153237
0.8283582089552238

0.808454425363276 #40 2
0.835820895522388
0.8124174372523117
0.8731343283582089

0.8005284015852048 #40 3
0.8582089552238806
0.8150594451783355
0.8656716417910447

0.8018494055482166 #40 9
0.8134328358208955
0.8163804491413474
0.8731343283582089

0.8018494055482166 #50 2
0.8432835820895522
0.8071334214002642
0.8582089552238806

0.8018494055482166 #50 3
0.835820895522388
0.8110964332892999
0.8955223880597015

0.8005284015852048 #50 9
0.8507462686567164
0.8124174372523117
0.8656716417910447
0:01:01.036000
0:00:59.328000
0:01:11.324000
0:01:21.080000
0:03:11.706000
0:03:35.800000
0:01:09.927000
0:01:15.608000
0:01:41.031000
0:01:59.046000
0:04:41.152000
0:05:42.269000
0:01:38.817000
0:01:43.759000
0:02:15.487000
0:02:46.119000
0:06:41.585000
0:07:48.558000
0:01:52.730000
0:02:09.998000
0:02:44.340000
0:03:13.784000
0:08:09.269000
0:09:18.320000
0:02:14.262000
0:02:35.690000
0:03:17.989000
0:03:53.021000
0:09:30.888000
0:11:38.177000
0:02:55.834000
0:03:35.436000
0:04:27.828000
0:04:56.958000
0:12:35.626000
0:15:17.071000
0:03:32.802000
0:04:29.402000
0:05:16.358000
0:06:18.673000
0:15:38.714000
0:21:00.186000
0:02:16.943000
0:01:13.049000
0:28:06.127000
0:02:04.685000
0:26:41.104000
0:42:17.889000
0:01:47.003000
0:01:20.782000
0:02:02.756000
0:02:07.362000
0:05:23.097000
0:06:20.684000
0:01:51.909000
0:02:01.008000
0:02:40.518000
0:02:44.263000
0:08:20.829000
0:08:14.327000
0:02:33.741000
0:02:16.814000
0:02:49.753000
0:03:51.996000
0:08:58.892000
0:09:26.862000
0:02:23.332000
0:02:42.087000
0:04:09.962000
0:04:06.387000
0:13:06.709000
0:14:19.890000
0:04:47.461000
0:04:32.189000
0:06:07.393000
0:05:15.960000
0:16:44.893000
0:18:05.323000
0:04:41.006000
0:05:00.005000
0:06:53.932000
0:07:58.066000
0:21:05.414000
1:35:07.151000
0:01:17.445000
0:01:09.652000
0:01:36.260000
0:01:39.595000
0:05:02.515000
0:04:05.018000
0:01:35.942000
0:01:29.495000
0:02:17.501000
0:02:04.382000
0:06:09.385000
0:05:41.100000
0:02:04.288000
0:02:03.620000
0:02:58.933000
0:02:42.720000
0:08:17.956000
0:08:15.752000
0:02:23.091000
0:02:27.732000
0:03:30.934000
0:03:31.905000
0:09:57.719000
0:09:27.064000
0:02:45.791000
0:02:42.309000
0:04:16.229000
0:03:53.813000
0:12:08.489000
0:12:18.239000
0:03:37.014000
0:03:39.887000
0:05:28.294000
0:05:18.012000
0:16:00
0:16:12.020000
0:04:39.607000
0:04:59.264000
0:06:54.907000
0:06:44.193000
0:19:50.746000
0:18:52.312000
files = open("titanic_intermediate_parameters_2018-11-13060058.pickle", "rb")
trials, space_nodes, best_nodes = pickle.load(files)
files.close()

best_nodes = parse_nodes(trials, space_nodes)

train_acc = []
valida_acc = []
time_cost = []

algo = partial(tpe.suggest, n_startup_jobs=10)
 
for i in range(0, 3):
    
    X_split_train, X_split_test, Y_split_train, Y_split_test = train_test_split(X_train_scaled, Y_train, test_size=0.15, stratify=Y_train)
    
    #下面全是超参搜索十次的结果呢
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用两个节点的stacking
    nodes_list = [best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 10)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用两个节点的stacking
    nodes_list = [best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 10)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用三个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 10)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用三个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 10)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用九个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 10)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    #下面是使用stacking的部分，使用九个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 10)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))


    #下面全是超参搜索十五次的结果呢
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用两个节点的stacking
    nodes_list = [best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 15)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用两个节点的stacking
    nodes_list = [best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 15)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用三个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 15)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用三个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 15)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用九个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 15)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用九个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 15)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))



    #下面全是超参搜索二十次的结果呢
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用两个节点的stacking
    nodes_list = [best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 20)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用两个节点的stacking
    nodes_list = [best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 20)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用三个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 20)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用三个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 20)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用九个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 20)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用九个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 20)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))
    
    
    #下面全是超参搜索二十五次的结果呢
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用两个节点的stacking
    nodes_list = [best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 25)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用两个节点的stacking
    nodes_list = [best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 25)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用三个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 25)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用三个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 25)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用九个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 25)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用九个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 25)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))

    
    
    #下面全是超参搜索三十次的结果呢
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用两个节点的stacking
    nodes_list = [best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 30)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用两个节点的stacking
    nodes_list = [best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 30)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用三个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 30)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用三个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 30)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用九个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 30)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用九个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 30)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))



    #下面全是超参搜索四十次的结果呢
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用两个节点的stacking
    nodes_list = [best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 40)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用两个节点的stacking
    nodes_list = [best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 40)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用三个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 40)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用三个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 40)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用九个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 40)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用九个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 40)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))



    #下面全是超参搜索五十次的结果呢
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用两个节点的stacking
    nodes_list = [best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 50)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用两个节点的stacking
    nodes_list = [best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 50)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用三个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 50)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用三个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 50)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用九个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 50)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
    start_time = datetime.datetime.now()
    #下面是使用stacking的部分，使用九个节点的stacking
    nodes_list = [best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 50)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))

for i in range(0, len(train_acc)):
    print(train_acc[i])
    print(valida_acc[i])

for i in range(0, len(time_cost)):
    print(time_cost[i])
"""

"""
#得到的是这种结果我也觉得很绝望呀，到底怎么做才能够迅猛的提升泛化性能呢
0.8375165125495376
0.7985074626865671
0.8414795244385733
0.8059701492537313
0.8348745046235139
0.8208955223880597
0.8467635402906208
0.8134328358208955
0.845442536327609
0.7761194029850746
0.8428005284015853
0.7985074626865671
0:14:31.484134
0:33:30.003851
0:14:53.732157
0:30:45.822111
0:12:12.323397
0:28:13.400339
files = open("titanic_intermediate_parameters_2018-11-13060058.pickle", "rb")
trials, space_nodes, best_nodes = pickle.load(files)
files.close()

best_nodes = parse_nodes(trials, space_nodes)

train_acc = []
valida_acc = []
time_cost = []

algo = partial(tpe.suggest, n_startup_jobs=10)
 
for i in range(0, 3):
    
    X_split_train, X_split_test, Y_split_train, Y_split_test = train_test_split(X_train_scaled, Y_train, test_size=0.15, stratify=Y_train)
    
    #下面是11节点20次搜索的结果呢
    start_time = datetime.datetime.now()
    nodes_list = [best_nodes, best_nodes, best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes, best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 20)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))
    
    
    #下面是45节点20次搜索的结果呢
    start_time = datetime.datetime.now()
    nodes_list = [best_nodes, best_nodes, best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 20)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
for i in range(0, len(train_acc)):
    print(train_acc[i])
    print(valida_acc[i])

for i in range(0, len(time_cost)):
    print(time_cost[i])
"""    

"""
#所以其实我又产生了另外的想法好像无脑增加节点的方式似乎可以获得更好的结果吧？
#但是今天还是先把结果提交了再说其他的事情吧。下面的代码就是我的提交结果的版本咯
#提交了四次左右，最后获得的成绩大概是23%左右大致是24%吧。。没有我想象中那么完美呀。。
#现在突然很想去参加比赛，我觉得完整的做一场比赛应该就能够能够得到很多的提升吧
start_time = datetime.datetime.now()

files = open("titanic_intermediate_parameters_2018-11-13060058.pickle", "rb")
trials, space_nodes, best_nodes = pickle.load(files)
files.close()
best_nodes = parse_nodes(trials, space_nodes)

nodes_list = [best_nodes, best_nodes]
stacked_train, stacked_test = stacked_features_validate(nodes_list, X_train_scaled, Y_train, X_test_scaled, 5, 20)
save_stacked_dataset(stacked_train, stacked_test, "stacked_titanic")

lr_stacking_cv_predict(stacked_train, Y_train, stacked_test, max_evals=2000)
end_time = datetime.datetime.now()
print("time cost", (end_time - start_time))
"""

"""
#下面的实验结果如下：
#我好像真的找到了有效的办法提升模型的准确率咯，就是修改模型的折数
#当进行预测的时候将折数由5修改为20折就vans了。具体的实验结果如下：
#结果明显的提升是因为数据的使用率上升，使用率由原来只有0.95*4/5=0.76变为0.95*19/20=0.9026
#我在想到底有没有必要修改为50折呢，如果改为50使用率则为0.95*49/50=0.931
#然后我觉得节点数目应该还是保持为45个吧，我不知道为什么节点数目越多确实结果越好呀。。。
#所以晚上修改为50折以及51个节点看看到底会发生什么样子的结果呢，我还蛮期待的咧~~~~~~~~~~~~~~
#0.8309114927344782
#0.8059701492537313
#0.8348745046235139
#0.8208955223880597
#0:59:54.911778
#2:10:25.467866
files = open("titanic_intermediate_parameters_2018-11-13060058.pickle", "rb")
trials, space_nodes, best_nodes = pickle.load(files)
files.close()

best_nodes = parse_nodes(trials, space_nodes)

train_acc = []
valida_acc = []
time_cost = []

algo = partial(tpe.suggest, n_startup_jobs=10)
 
for i in range(0, 1):
    
    X_split_train, X_split_test, Y_split_train, Y_split_test = train_test_split(X_train_scaled, Y_train, test_size=0.15, stratify=Y_train)
    
    #下面是11节点20次搜索的结果呢
    start_time = datetime.datetime.now()
    nodes_list = [best_nodes, best_nodes, best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes, best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 20, 20)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))
    
    
    #下面是45节点20次搜索的结果呢
    start_time = datetime.datetime.now()
    nodes_list = [best_nodes, best_nodes, best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes, best_nodes, best_nodes,
                  best_nodes, best_nodes, best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 20, 20)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
for i in range(0, len(train_acc)):
    print(train_acc[i])
    print(valida_acc[i])

for i in range(0, len(time_cost)):
    print(time_cost[i])
"""

"""
#今天有一件事情让我觉得特别的高兴呀，就是不同节点训练出的神经网络stacking出来的结果居然进入了前17%
#看来之前的问题果然是选择了相同的节点进行stacking的缘故咯，毕竟我修改了交叉验证的折数居然都还是不行
#我仔细推敲一下选择相同的节点stacking不行，主要是不满足分类器差异越大效果越好的规则吧
#但是我自己实验的时候相同节点stacking的实验结果明显比不同节点stacking的结果更好呀
#如果真的说原因的话，应该是说相同节点的stacking可能训练出来的是同一类，所以提升比较有限
#但是不同节点的stacking可能是不同类型或者不同视角的分类器，所以在有更明显的提升咯
#也可以从数据集的角度描述这个问题，可能是最佳节点在公开的训练集上表现是最好的
#但是当预测的数据是未知数据集时，原来的模型的表现就比较一般啦，也就是泛化性能比较有限的意思吧。
#所以多个best_nodes在公开的数据集上面的预测效果是最好的，但是在其他数据集上就不好说咯。。
#所以按照这个解释的话，我超参搜索的次数好像对于提交结果影响不是特别大咯？
#可能影响更大的会是节点数目咯，但是节点数目怎么确定呢我觉得还蛮难知道的，说不定45比7个节点好呢？？
#至于交叉验证的折数和单个节点训练的次数我觉得50和25已经差不多了，改成60和25我觉得也是可以的吧。。
start_time = datetime.datetime.now()

files = open("titanic_intermediate_parameters_2018-11-13060058.pickle", "rb")
trials, space_nodes, best_nodes = pickle.load(files)
files.close()

#best_nodes = parse_nodes(trials, space_nodes)
#nodes_list = [best_nodes, best_nodes, best_nodes, best_nodes, best_nodes]

nodes_list = parse_trials(trials, space_nodes, 7)

stacked_train, stacked_test = stacked_features_validate(nodes_list, X_train_scaled, Y_train, X_test_scaled, 50, 25)
save_stacked_dataset(stacked_train, stacked_test, "stacked_titanic")

lr_stacking_cv_predict(stacked_train, Y_train, stacked_test, max_evals=2000)
end_time = datetime.datetime.now()
print("time cost", (end_time - start_time))
"""

"""
#如果上面的代码在最后的预测阶段出现问题，应该是best_nodes内部的path设置的问题咯
#可以用下面的最原始的方法去解决，也可以在开始预测的时候设置一下best_nodes的path咯
stacked_train, stacked_test = load_stacked_dataset("stacked_titanic")
lr_stacking_cv_predict_path(stacked_train, Y_train, stacked_test, "C:/Users/win7/Desktop/ssss.csv", 2000)
"""

"""
#他妈的kaggle上面的45个不同节点结果并不是特别好呀，但是还是追平了我同的节点最好记录，说不通节点可能真的好些。
#mmp真的郁闷，我在家里面的机器计算了十个小时得到的结果居然就是这个样子的吗，我还以为可以无脑加节点提升正确率呢
#所以现在怎么做呢？准备试试其他节点的计算结果咯？看来还是要期待更多的超参搜索带来惊喜咯？？？
#那因为之前是7个节点25次的计算，我这次试试7个节点20次的计算结果如何呢？？结果也就是78.9%左右的正确率
#我现在家里面的电脑在计算27个节点的20次计算，感觉可能应该比45次好一些，但是我觉得肯定不是这个比赛尝试的终点。
#我觉得最佳节点数应该是3到9之间吧，然后最佳计算次数应该是20次到25次之间吧，说不定上次的17%的结果并不是因为
#我找到了不同的节点的原因，反而可能是因为节点数目的增加导致的准确率的下降咯，我之前有实验确实表明较少的节点效果更好。
#他妈的五个不同的节点计算出来的结果还是并不是特别理想呀，我感觉可能问题出在前面的代码或者超参搜索太少的问题上吧。。
#卧槽我觉得最佳的方式还是要使用五个节点左右吧，而且第二个参数20确实是比25要好一些的说呢。。之前使用的25次所以不太好？？
#我刚才使用的五个相同节点的结果已经追平了使用七个不同节点的结果咯，或许试试使用五个不同节点效果如何呢？
#我刚才使用的五个不同节点的结果已经追平了使用七个不同节点的结果咯，这些结果都是我的最佳结果，所以说最佳节点数真是3到11吧。。
#3个节点的数据在验证集上面居然是历史最低的结果，我有时候感觉挺困惑的，可能是因为超参搜索或者以前的代码有问题的缘故吧。
#我查了一下自己的代码感觉好像前面的特征缩放似乎是做错咯？我改一下再用神经网络计算一下看看能否获得更好的结果呢。
start_time = datetime.datetime.now()

files = open("titanic_intermediate_parameters_2018-11-13060058.pickle", "rb")
trials, space_nodes, best_nodes = pickle.load(files)
files.close()

#best_nodes = parse_nodes(trials, space_nodes)
#nodes_list = [best_nodes, best_nodes, best_nodes]

nodes_list = parse_trials(trials, space_nodes, 7)

stacked_train, stacked_test = stacked_features_validate(nodes_list, X_train_scaled, Y_train, X_test_scaled, 50, 20)
save_stacked_dataset(stacked_train, stacked_test, "stacked_titanic")

lr_stacking_cv_predict(stacked_train, Y_train, stacked_test, max_evals=2000)
end_time = datetime.datetime.now()
print("time cost", (end_time - start_time))
"""

"""
#如果上面的代码在最后的预测阶段出现问题，应该是best_nodes内部的path设置的问题咯
#可以用下面的最原始的方法去解决，也可以在开始预测的时候设置一下best_nodes的path咯
stacked_train, stacked_test = load_stacked_dataset("stacked_titanic")
lr_stacking_cv_predict_path(stacked_train, Y_train, stacked_test, "C:/Users/win7/Desktop/ssss.csv", 2000)
"""

"""
#我在kaggle上面看了几个kernel了，感觉特征工程之类的并没有太多特殊的地方
#而且我还遇到过没有用规则化处理就直接进行预测最后居然还得到了3%的结果。。
#我仔细看了一下用的都是统计学习的方法而且没有使用诸如贝叶斯优化之类的技术也没有新的特征创建也没有stacking。。
#我觉得以后特征工程这些东西肯定是要做的，只是说我使用神经网络只需要做初级的特征处理而已。。
#我看了这么久感觉好像真的没看到任何的黑魔法，难道说我的超参搜索次数太少了吗。。我觉得特征也没啥问题呀
#我现在感觉答案可能在周志华的论文或者是神经网络的书籍里面？我很好奇神经网络做kaggle到底要怎么做呢？？
#现在我已经找到了一个主要原因就是因为stacking的时候使用了相同的节点，换成使用不同的节点只有提升比较明显
#我觉得剩下的就是看一下周志华神经网络stacking的论文或者做一次神经网络的比赛，看看那些人是如何选择最佳模型的。。
#明天找了两篇周志华的论文试一下效果到底如何哦，然后就准备进入到下一个阶段了，开始正式着手比赛的相关事情咯。。
#我在kaggle的讨论板块上看到了几篇使用神经网络进行计算的，还是有人在验证集上面获得了82%的准确率咯，我觉得还是蛮屌的
#现在主要就是实验一下周志华的算法咯，然后完成之后就专心对比这几篇kernel上面给出的结果（只有一篇能用的吧）。。
#泰坦尼克号的性别上面完全可以使用one-hot编码，然后年龄上面的完全可以采用新的办法进行处理。。以及自助法构建数据集咯~
#昨天花了很多时间解决论文中部分没有理解的内容，现在开始准备尝试使用周志华提出的一些算法跑一下结果呢。然后就是kernel咯
#周志华的GASEN就是使用自助法构建数据集然后再使用遗传算法对于权重进行遗传优化并配合论文提出的一些观点选择吧，没有代码
#我了个大去我居然在南京大学的周志华的实验室中找到了相关GASEN的代码，具体网址在http://lamda.nju.edu.cn/CH.Data.ashx
#但是找到的是个.m文件也就是matlab写出来的文件，感觉还是不用尝试这个了，直接试一下gcForest算了吧。。居然需要Jason定义格式
#我靠看了一下github上面的和gcForest的代码（和南京大学实验室官网上面的一样哈），这个模型其实是用于图像处理滴，果然我还是用不了哈~~
#下面的问题就剩下研究那篇kernel并实现它的思路，然后修改一下数据处理流程和最佳模型的选择过程咯，这波我必上top5%吧？
#还有一个任务忘记说明了，那就是尝试一下在上回30000次的超参搜索的基础上进行一次模拟呢，看是否能够取得进展？？我觉得很悬吧
#那么现在的第一个实验就是对比30000次和7000次超参搜索的区别如何咯？下面的实验结果表明30000次的搜索并没有特别的优势！！
0.8295904887714664 #7000的5节点和7节点
0.8283582089552238
0.8150594451783355
0.7611940298507462

0.8322324966974901 #30000的5节点和7节点
0.8283582089552238
0.8229854689564069
0.835820895522388

0.8361955085865258 #7000的5节点和7节点
0.8059701492537313
0.8282694848084544
0.7985074626865671

0.8361955085865258 #30000的5节点和7节点
0.8059701492537313
0.8256274768824307
0.8059701492537313

0.8177014531043593 #7000的5节点和7节点
0.8507462686567164
0.8229854689564069
0.8507462686567164

0.8229854689564069 #30000的5节点和7节点
0.7985074626865671
0.821664464993395
0.835820895522388

0.8295904887714664 #7000的5节点和7节点
0.7761194029850746
0.8163804491413474
0.8059701492537313

0.8282694848084544 #30000的5节点和7节点
0.7910447761194029
0.8401585204755614
0.8134328358208955

0.8177014531043593 #7000的5节点和7节点
0.8656716417910447
0.8163804491413474
0.8656716417910447

0.8190224570673712 #30000的5节点和7节点
0.8582089552238806
0.8269484808454426
0.8656716417910447
0:05:46.275789
0:08:11.426164
0:06:22.540562
0:08:23.461150
0:05:52.514654
0:07:55.374074
0:05:57.565987
0:08:47.199617
0:06:21.070278
0:08:59.520213
0:06:49.348075
0:09:56.742219
0:05:42.945352
0:07:45.442673
0:06:51.764660
0:09:11.695433
0:05:44.663761
0:08:37.155880
0:06:30.736413
0:08:59.267996

train_acc = []
valida_acc = []
time_cost = []

algo = partial(tpe.suggest, n_startup_jobs=10)
 
for i in range(0, 5):
    
    X_split_train, X_split_test, Y_split_train, Y_split_test = train_test_split(X_train_scaled, Y_train, test_size=0.15, stratify=Y_train)

    #上面是7000次的实验结果咯
    start_time = datetime.datetime.now()
    files = open("titanic_intermediate_parameters_2018-11-13060058.pickle", "rb")
    trials, space_nodes, best_nodes = pickle.load(files)
    files.close()
    best_nodes = parse_nodes(trials, space_nodes)
    nodes_list = [best_nodes, best_nodes, best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 20)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))
    
    #下面是30000次的实验结果咯
    start_time = datetime.datetime.now()
    files = open("titanic_intermediate_parameters_2018-12-12142114.pickle", "rb")
    trials, space_nodes, best_nodes = pickle.load(files)
    files.close()
    best_nodes = parse_nodes(trials, space_nodes)
    nodes_list = [best_nodes, best_nodes, best_nodes, best_nodes, best_nodes]
    stacked_train, stacked_test = stacked_features_validate(nodes_list, X_split_train, Y_split_train, X_split_test, 5, 20)
    #下面是进行超参搜索的lr咯
    clf = LogisticRegression()
    param_dist = {"penalty": ["l1", "l2"],
                  "C": np.linspace(0.001, 100000, 10000),
                  "fit_intercept": [True, False],
                  #"solver": ["newton-cg", "lbfgs", "liblinear", "sag"]
                  }
    random_search = RandomizedSearchCV(clf, param_distributions=param_dist, n_iter=2000)
    random_search.fit(stacked_train, Y_split_train)
    best_acc = random_search.best_estimator_.score(stacked_train, Y_split_train)
    lr_pred = random_search.best_estimator_.predict(stacked_test)
    test_acc = cal_acc(lr_pred, Y_split_test)
    train_acc.append(best_acc)
    valida_acc.append(test_acc)
    end_time = datetime.datetime.now()
    time_cost.append((end_time - start_time))    
    
for i in range(0, len(train_acc)):
    print(train_acc[i])
    print(valida_acc[i])

for i in range(0, len(time_cost)):
    print(time_cost[i])
"""