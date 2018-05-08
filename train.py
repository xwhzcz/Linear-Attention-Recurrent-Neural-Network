
"""Train a model with Hyperopt, or retrain the best model in the main here."""

import os
import sys
import traceback
import uuid

import numpy as np
from hyperopt import hp, STATUS_OK, STATUS_FAIL
from sklearn import metrics
from sklearn.utils import shuffle
import torch
from torch import nn
from torch.autograd import Variable

from json_utils import load_best_hyperspace, save_json_result, print_json
from larnn import LARNN
from datasets import UCIHARDataset, OpportunityDataset


__author__ = "Guillaume Chevalier"
__license__ = "MIT License"
__copyright__ = {
    "Version 1": "Copyright 2017, Guillaume Chevalier",
    "Version 2": "Copyright 2017, Vooban Inc.",
    "Version 3": "Copyright 2018, Guillaume Chevalier"
}
__notice__ = """
    Version 1, May 27 2017 - Jul 11 2017:
        Guillaume Chevalier
        Creation of the first version of file for the creation of a custom CIFAR-10 & CIFAR-100 CNN.
        See: https://github.com/guillaume-chevalier/Hyperopt-Keras-CNN-CIFAR-100/commit/7c2f8d5cadbfe96fb3f3572d07143f8ddbaa18d4#diff-06f0ae61dbe721276333a254a24a044b

    Version 2, Jul 19 2017 - Jul 25 2017:
        Guillaume Chevalier (On behalf of Vooban Inc.)
        Adapted the file for better training and visualizations.
        See: https://github.com/Vooban/Hyperopt-Keras-CNN-CIFAR-100/commit/66c6492afa524139ba8153a8c7495cd177b08bf2#diff-6c53f5c58afef9e1fee290c207656b5e

    Version 3, May 6 2018 - May 11 2018:
        Guillaume Chevalier
        Adapted the file for the creation of its Linear Attention Recurrent Neural Network (LARNN).
        See: https://github.com/guillaume-chevalier/Linear-Attention-Recurrent-Neural-Network
"""


def optimize_model(hyperparameters, dataset, evaluation_metric):
    """Build a LARNN and train it on given dataset."""

    try:
        model, model_name, result = train(hyperparameters, dataset, evaluation_metric)

        # Save training results to disks with unique filenames
        save_json_result(model_name, dataset.NAME, result)

        # K.clear_session()
        del model

        return result

    except Exception as err:
        try:
            del model
        except:
            pass

        err_str = str(err)
        print(err_str)
        traceback_str = str(traceback.format_exc())
        print(traceback_str)
        return {
            'status': STATUS_FAIL,
            'err': err_str,
            'traceback': traceback_str
        }

    print("\n\n")


def train(hyperparameters, dataset, evaluation_metric):
    """Build the deep CNN model and train it."""

    hyperparameters['hidden_size'] = int(hyperparameters['hidden_size']) # Sanitizing params
    print("LARNN with hyperparameters:")
    print_json(hyperparameters)

    # Build model
    model = Model(
        hyperparameters,
        input_size=dataset.INPUT_FEATURES_SIZE,
        output_size=dataset.OUTPUT_CLASSES_SIZE)
    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=hyperparameters['learning_rate'],
        weight_decay=hyperparameters['l2_weight_reg'])

    # Metadata kept
    train_accuracies = []
    train_f1_scores = []
    train_losses = []
    validation_accuracies = []
    validation_f1_scores = []
    validation_losses = []

    # Train on shuffled examples in batch for each epoch
    for epoch in range(hyperparameters['training_epochs']):
        print("Training epoch {}:".format(epoch))
        shuffled_X, shuffled_Y = shuffle(dataset.X_train, dataset.Y_train, random_state=epoch*42)
        nb_examples = dataset.X_train.shape[0]
        for step, (start, end) in enumerate(
                zip(range(0, nb_examples, hyperparameters['batch_size']),
                    range(hyperparameters['batch_size'], nb_examples + 1, hyperparameters['batch_size']))):
            X = shuffled_X[start:end]
            Y = shuffled_Y[start:end]

            # Train
            model.train()
            optimizer.zero_grad()
            inputs = Variable(torch.from_numpy(X).float().transpose(1, 0))
            targets = Variable(torch.from_numpy(Y).long())
            outputs, _ = model(inputs, state=None)  # Truncated BPTT not used.
            loss = criterion(outputs, targets)
            loss.backward()
            optimizer.step()

            # Train metrics
            train_accuracies.append(metrics.accuracy_score(
                Y, outputs.argmax(-1)))
            train_f1_scores.append(metrics.f1_score(
                Y, outputs.argmax(-1), average="weighted"))
            train_losses.append(loss.data.item())

            # Print occasionnaly
            if step % 3 == 0 and step !=0:
                print("    Training step {}: accuracy={}, f1={}, loss={}".format(
                    step, train_accuracies[-1], train_f1_scores[-1], train_losses[-1]))

                break  # TODO: remove for full training.

        # Validation
        model.eval()
        inputs = Variable(torch.from_numpy(dataset.X_test).float().transpose(1, 0))
        targets = Variable(torch.from_numpy(dataset.Y_test).long())
        outputs, _ = model(inputs, state=None)
        loss = criterion(outputs, targets)
        optimizer.zero_grad()

        # Validation metrics
        validation_accuracies.append(metrics.accuracy_score(
            dataset.Y_test, outputs.argmax(-1)))
        validation_f1_scores.append(metrics.f1_score(
            dataset.Y_test, outputs.argmax(-1), average="weighted"))
        validation_losses.append(loss.data.item())

        # Print
        print("        Validation: accuracy={}, f1={}, loss={}".format(
            validation_accuracies[-1], validation_f1_scores[-1], validation_losses[-1]))

        if epoch > 10:
            break  # TODO: remove for full training.

    # Aggregate data for serialization
    history = {
        'train_accuracies': train_accuracies,
        'train_f1_scores': train_f1_scores,
        'train_losses': train_losses,
        'validation_accuracies': validation_accuracies,
        'validation_f1_scores': validation_f1_scores,
        'validation_losses': validation_losses
    }

    # Create "result" for Hyperopt and serialization
    full_metric_name = 'validation_{}'.format(evaluation_metric)
    max_score = max(history[full_metric_name])
    model_name = "model_{}_{}".format(str(max_score), str(uuid.uuid4())[:5])
    print("Model name: {}".format(model_name))
    print("HISTORY:")
    print_json(history)
    result = {
        # Note: 'loss' in Hyperopt means 'score', so we use something else it's not the real loss.
        'loss': -max_score,
        'true_loss': -max_score,
        'true_loss_variance': np.var(history[full_metric_name][-10:]),  # Note that the "-10" is in epochs count.
        'real_best_loss': min(validation_losses),  # This is the only "loss" literally-speaking. Others are hyperopt losses for `fmin` meta-optimization.
        # "Best" metrics throughout training:
        'best_train_accuracy': max(train_accuracies),
        'best_train_f1_score': max(train_f1_scores),
        'best_validation_accuracy': max(validation_accuracies),
        'best_validation_f1_score': max(validation_f1_scores),
        # Misc:
        'model_name': model_name,
        'dataset_name': dataset.NAME,
        'space': hyperparameters,
        'history': history,
        'status': STATUS_OK
    }
    print("RESULT:")
    print_json(result)
    return model, model_name, result


dataset_name_to_class = {
    'UCIHAR': UCIHARDataset,
    'Opportunity': OpportunityDataset}
dataset_name_to_evaluation_metric = {
    'UCIHAR': "accuracies",
    'Opportunity': "f1_scores"}

def get_optimizer(dataset_name):
    _dataset = dataset_name_to_class[dataset_name]()
    _evaluation_metric = dataset_name_to_evaluation_metric[dataset_name]

    # Returns a callable for Hyperopt Optimization (for `fmin`):
    return lambda hyperparameters: (
        optimize_model(hyperparameters, _dataset, _evaluation_metric)
    )


class Model(nn.Module):
    HYPERPARAMETERS_SPACE = {
        # This loguniform scale will multiply the learning rate, so as to make
        # it vary exponentially, in a multiplicative fashion rather than in
        # a linear fashion, to handle his exponentialy varying nature:
        'learning_rate': 0.001 * hp.loguniform('learning_rate_mult', -0.5, 0.5),
        # L2 weight decay:
        'l2_weight_reg': 0.005 * hp.loguniform('l2_weight_reg_mult', -1.3, 1.3),
        # Number of loops on the whole train dataset
        'training_epochs': 80,
        # Number of examples fed per training step
        'batch_size': 64,

        # The dropout on the hidden unit on top of each LARNN cells
        'dropout_drop_proba': hp.uniform('dropout_drop_proba', 0.1, 0.5),
        # Let's multiply the "default" number of hidden units:
        'hidden_size': 42 * hp.loguniform('hidden_size_mult', -0.6, 0.6),
        # Use batch normalisation at more places?
        'use_BN': True,
        # Number of layers, either stacked or residualy stacked:
        'num_layers': hp.choice('num_layers', [2, 3]),
        # Use residual connections for the 2nd (stacked) layer?
        'is_stacked_residual': hp.choice('is_stacked_residual', [False, True]),
        # How the new attention is placed in the LSTM
        'larnn_mode': hp.choice('attention_type', [
            'concat',  # Attention will be concatenated to x and h.
            'residual',  # Attention will be added to x and h.
            'layer'  # Attention will be computed from a layer with x and h.
        ]),
        # Wheter or not to use Positional Encoding similar to the one used in https://arxiv.org/abs/1706.03762
        'use_positional_encoding': hp.choice('use_positional_encoding', [False, True])
    }

    def __init__(self, hyperparameters, input_size, output_size):
        super().__init__()
        self.hyperparameters = hyperparameters

        hidden_size = self.hyperparameters['hidden_size']
        self._in = nn.Linear(input_size, hidden_size)
        # self.larnn = LARNN(
        #     input_size=self.hyperparameters['input_size'],
        #     hidden_size=self.hyperparameters['hidden_size'],
        #     num_layers=self.hyperparameters['num_layers'],
        #     is_stacked_residual=self.hyperparameters['is_stacked_residual'],
        #     larnn_mode=self.hyperparameters['larnn_mode'],
        #     use_positional_encoding=self.hyperparameters['use_positional_encoding'],
        #     dropout=self.hyperparameters['dropout_drop_proba']
        # )
        self._larnn = nn.LSTM(
            input_size=hidden_size,
            hidden_size=hidden_size,
            num_layers=self.hyperparameters['num_layers'],
            dropout=self.hyperparameters['dropout_drop_proba'])
        self._out = nn.Linear(hidden_size, output_size)

    def forward(self, input, state=None):
        hidden = self._in(input)  # Change number of features with a linear
        hidden, state = self._larnn(hidden, state)  # Deep LARNNing a lot here
        output = hidden[-1]  # Keep only last item of time series sequence axis
        output = self._out(output)  # Reshape with a linear for categories
        return output, state  # Returned state could be used for Truncated BPTT


if __name__ == "__main__":
    """Take the best hyperparameters and re-train on them."""

    dataset_name = 'UCIHAR'
    space_best_model = load_best_hyperspace(dataset_name)

    if space_best_model is None:
        print("You haven't found good hyperparameters yet. Run `hyperopt_optimize.py` first.")
        sys.exit(1)

    # Train the model.
    model, model_name, result = optimize_model(dataset_name)(space_best_model)

    # Prints training results to disks with unique filenames
    print("Model Name:", model_name)
    print("Training results (only printed here, not saved):")
    print_json(result)