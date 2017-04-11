#!/usr/bin/env python

"""
This python script applies scikit-learn classifiers to the ATLAS RPV data
"""

import os
import argparse
import logging

import multiprocessing as mp

import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.linear_model import LogisticRegression
import sklearn.metrics
import matplotlib.pyplot as plt

def parse_args():
    """Parse the command line arguments"""
    parser = argparse.ArgumentParser('run_classifiers')
    add_arg = parser.add_argument
    add_arg('input_dir', help='Directory of npz files')
    add_arg('-p', '--num-workers', type=int, default=1,
            help='Number of concurrent worker processes')
    add_arg('--sig', default='rpv_1400_850',
            help='Signal sample to use')
    add_arg('--bkg', nargs='*',
            default=['qcd_JZ3', 'qcd_JZ4', 'qcd_JZ5', 'qcd_JZ6',
                     'qcd_JZ7', 'qcd_JZ8', 'qcd_JZ9', 'qcd_JZ10',
                     'qcd_JZ11', 'qcd_JZ12', 'rpv_1400_850'],
            help='Background sample names to use')
    add_arg('--num-sig', type=int, help='Number of signal events to use')
    add_arg('--num-bkg', type=int,
            help='Number of bkg events to use (per sample)')
    return parser.parse_args()

def get_file_keys(file_name):
    """Retrieves the list of keys from an npz file"""
    with np.load(file_name) as f:
        keys = f.keys()
    return keys

def retrieve_data(file_name, *keys):
    """
    A helper function for retrieving some specified arrays from one npz file.
    Returns a list of arrays corresponding to the requested key name list.
    """
    with np.load(file_name) as f:
        try:
            data = [f[key] for key in keys]
        except KeyError as err:
            logging.error('Requested key not found. Available keys: %s' % f.keys())
            raise
    return data

def parse_object_features(array, num_objects, default_val=0.):
    """
    Takes an array of object arrays and returns a fixed rank-2 array.
    Clips and pads each element as necessary.
    Output shape is (array.shape[0], num_objects).
    """
    # Create the output first
    length = array.shape[0]
    output_array = np.full((length, num_objects), default_val)
    # Fill the output
    for i in xrange(length):
        k = min(num_objects, array[i].size)
        output_array[i,:k] = array[i][:k]
    return output_array

def prepare_sample_features(sample_file, num_jets=4, max_events=None):
    """Load the model features from a sample file"""
    data = retrieve_data(
        sample_file, 'fatJetPt', 'fatJetEta', 'fatJetPhi', 'fatJetM')
    num_events = data[0].shape[0]
    if max_events is not None and max_events < num_events:
        data = [d[:max_events] for d in data]
    return np.hstack(parse_object_features(a, num_jets) for a in data)

def calc_fpr_tpr(y_true, y_pred):
    """Calculate false-positive and true-positive rates"""
    tp = np.logical_and(y_true, y_pred).sum()
    fp = np.logical_and(np.logical_not(y_true), y_pred).sum()
    tpr = tp / y_true.sum()
    fpr = fp / (y_true.size - y_true.sum())
    return fpr, tpr

def main():
    """Main execution function"""

    # Parse command line
    args = parse_args()

    # Setup logging
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    logging.info('Initializing')

    logging.info('Configuration: %s' % args)

    sig_sample = args.sig
    bkg_samples = args.bkg
    path_prep = lambda s: os.path.join(args.input_dir, s + '.npz')
    sig_file = path_prep(sig_sample)
    bkg_files = map(path_prep, bkg_samples)

    logging.info('Sig file: %s' % sig_file)
    logging.info('Bkg files: %s' % bkg_files)

    sample_files = [sig_file] + bkg_files
    sig_features = prepare_sample_features(sig_file, max_events=args.num_sig)
    bkg_features = [prepare_sample_features(f, max_events=args.num_bkg)
                    for f in bkg_files]
    sample_features = [sig_features] + bkg_features
    sample_events = [sf.shape[0] for sf in sample_features]
    sample_labels = [1.] + [0.]*len(bkg_files)
    # Retrieve the analysis SR flag
    sample_passSR = [retrieve_data(f, 'passSR')[0][:nevt]
                     for (f, nevt) in zip(sample_files, sample_events)]

    # Merge the feature vectors
    X = np.concatenate(sample_features)
    y = np.concatenate([np.full(nevt, l) for (nevt, l) in
                        zip(sample_events, sample_labels)])
    passSR = np.concatenate(sample_passSR)

    logging.info('X-y shapes: %s, %s' % (X.shape, y.shape))
    logging.info('True fraction: %s' % y.mean())

    # Split into training and test samples
    X_train, X_test, y_train, y_test, passSR_train, passSR_test = (
        train_test_split(X, y, passSR))

    # Evaluate the existing analysis cuts
    logging.info('Classification report for SR:\n%s' %
        sklearn.metrics.classification_report(
            y_test, passSR_test, target_names=['Background', 'Signal']))

    # Calculate TPR and FPR for passSR
    sr_fpr, sr_tpr = calc_fpr_tpr(y_test, passSR_test)
    logging.info('SR FPR: %f, TPR: %f' % (sr_fpr, sr_tpr))

    lr_clf = make_pipeline(StandardScaler(), LogisticRegression())
    lr_clf.fit(X_train, y_train)
    logging.info('Classification report for Logistic Regression:\n%s' %
        sklearn.metrics.classification_report(y_test, lr_clf.predict(X_test),
                                              target_names=['QCD', 'RPV']))
    logging.info('Train set accuracy: %f' % lr_clf.score(X_train, y_train))
    logging.info('Test set accuracy: %f' % lr_clf.score(X_test, y_test))

    from sklearn.tree import DecisionTreeClassifier
    dt_clf = make_pipeline(StandardScaler(), DecisionTreeClassifier())
    dt_clf.fit(X_train, y_train)
    logging.info('Classification report for Decision Tree:\n%s' %
        sklearn.metrics.classification_report(y_test, dt_clf.predict(X_test),
                                              target_names=['QCD', 'RPV']))
    logging.info('Train set accuracy: %f' % dt_clf.score(X_train, y_train))
    logging.info('Test set accuracy: %f' % dt_clf.score(X_test, y_test))

    from sklearn.ensemble import RandomForestClassifier
    rf_clf = make_pipeline(StandardScaler(), RandomForestClassifier())
    rf_clf.fit(X_train, y_train)
    logging.info('Classification report for Random Forest:\n%s' %
        sklearn.metrics.classification_report(y_test, rf_clf.predict(X_test),
                                              target_names=['QCD', 'RPV']))
    logging.info('Train set accuracy: %f' % rf_clf.score(X_train, y_train))
    logging.info('Test set accuracy: %f' % rf_clf.score(X_test, y_test))

    from sklearn.ensemble import GradientBoostingClassifier
    bdt_clf = make_pipeline(StandardScaler(), GradientBoostingClassifier())
    bdt_clf.fit(X_train, y_train)
    logging.info('Classification report for Gradient Boosted Tree:\n%s' %
        sklearn.metrics.classification_report(y_test, bdt_clf.predict(X_test),
                                              target_names=['QCD', 'RPV']))
    logging.info('Train set accuracy: %f' % bdt_clf.score(X_train, y_train))
    logging.info('Test set accuracy: %f' % bdt_clf.score(X_test, y_test))

    from sklearn.neural_network import MLPClassifier
    mlp_clf = make_pipeline(StandardScaler(), MLPClassifier())
    mlp_clf.fit(X_train, y_train)
    logging.info('Classification report for MLP:\n%s' %
        sklearn.metrics.classification_report(y_test, mlp_clf.predict(X_test),
                                              target_names=['QCD', 'RPV']))
    logging.info('Train set accuracy: %f' % mlp_clf.score(X_train, y_train))
    logging.info('Test set accuracy: %f' % mlp_clf.score(X_test, y_test))

    # Plot the ROC curves
    rocFig = plt.figure()
    classifiers = [lr_clf, rf_clf, bdt_clf, mlp_clf]
    clf_names = ['LR', 'RF', 'BDT', 'MLP']
    # Plot the SR point
    plt.plot(sr_fpr, sr_tpr, 's', label='Ana SR')
    # Plot the classifiers
    for clf, clfname in zip(classifiers, clf_names):
        probs = clf.predict_proba(X_test)[:,1]
        fpr, tpr, _ = sklearn.metrics.roc_curve(y_test, probs)
        auc = sklearn.metrics.auc(fpr, tpr)
        label = clfname + ', AUC=%.3f' % auc
        plt.plot(fpr, tpr, label=label)
    plt.legend(loc=0)
    plt.xlim((0, 0.50))
    plt.xlabel('False positive rate')
    plt.ylabel('True positive rate')

    # Save the figure
    rocFig.savefig('sklearn_roc.png')

if __name__ == '__main__':
    main()
