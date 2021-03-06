#!/usr/bin/env python

"""
This script will do pre-processing of input data
"""

import os
import argparse
import logging

import multiprocessing as mp

import numpy as np

from physics_selections import (filter_objects, filter_events,
                                select_fatjets, is_baseline_event,
                                sum_fatjet_mass, fatjet_deta12,
                                pass_sr4j, pass_sr5j,
                                is_signal_region_event)
from utils import suppress_stdout_stderr

class XsecMap():
    _xsecMap = None

    @staticmethod
    def load_xsec_map():
        return dict(np.genfromtxt('data/cross_sections.txt', dtype='O,f8'))

    @classmethod
    def get_xsec(cls, sample):
        if cls._xsecMap is None:
            cls._xsecMap = cls.load_xsec_map()
        return cls._xsecMap[sample]

def parse_args():
    """Parse the command line arguments"""
    parser = argparse.ArgumentParser('prepare_data')
    add_arg = parser.add_argument
    add_arg('input_file_list', nargs='+',
            help='Text file of input files')
    add_arg('-o', '--output-npz', help='Output numpy binary file')
    add_arg('--compress', action='store_true', help='Compress output npz')
    add_arg('-n', '--max-events', type=int,
            help='Maximum number of events to read')
    add_arg('-p', '--num-workers', type=int, default=1,
            help='Number of concurrent worker processes')
    return parser.parse_args()

def get_xsec(filename):
    """Parse the sample name from the filename and lookup its xross section"""
    sample = os.path.basename(filename).split('-')[0]
    return XsecMap.get_xsec(sample)

def get_data(files, branch_dict, **kwargs):
    """Applies root_numpy to get out a numpy array"""
    import root_numpy as rnp
    try:
        with suppress_stdout_stderr():
            tree = rnp.root2array(files, branches=branch_dict.keys(),
                                  warn_missing_tree=True, **kwargs)
    except IOError as e:
        logging.warn('WARNING: root2array gave an IOError: %s' % e)
        return None
    # Convert immutable structured array into dictionary of arrays
    data = dict()
    for (oldkey, newkey) in branch_dict.items():
        data[newkey] = tree[oldkey]
    return data

def process_events(data):
    """Applies physics selections and filtering"""

    # Object selection
    vec_select_fatjets = np.vectorize(select_fatjets, otypes=[np.ndarray])
    fatJetPt, fatJetEta = data['fatJetPt'], data['fatJetEta']
    jetIdx = vec_select_fatjets(fatJetPt, fatJetEta)

    fatJetPt, fatJetEta, fatJetPhi, fatJetM = filter_objects(
        jetIdx, fatJetPt, fatJetEta, data['fatJetPhi'], data['fatJetM'])

    # Baseline event selection
    skimIdx = np.vectorize(is_baseline_event)(fatJetPt)
    fatJetPt, fatJetEta, fatJetPhi, fatJetM = filter_events(
        skimIdx, fatJetPt, fatJetEta, fatJetPhi, fatJetM)
    num_baseline = np.sum(skimIdx)
    num_total = len(skimIdx)
    logging.info('Baseline selected events: %d / %d' % (num_baseline, num_total))

    # Calculate quantities needed for SR selection
    if num_baseline > 0:
        numFatJet = np.vectorize(lambda x: x.size)(fatJetPt)
        sumFatJetM = np.vectorize(sum_fatjet_mass)(fatJetM)
        fatJetDEta12 = np.vectorize(fatjet_deta12)(fatJetEta)

        # Signal-region event selection
        passSR4J = np.vectorize(pass_sr4j)(numFatJet, sumFatJetM, fatJetDEta12)
        passSR5J = np.vectorize(pass_sr5j)(numFatJet, sumFatJetM, fatJetDEta12)
        passSR = np.logical_or(passSR4J, passSR5J)
    else:
        numFatJet = sumFatJetM = fatJetDEta12 = np.zeros(0)
        passSR4J = passSR5J = passSR = np.zeros(0, dtype=np.bool)

    # Prepare the skimmed results
    skimData =  dict(fatJetPt=fatJetPt, fatJetEta=fatJetEta,
                     fatJetPhi=fatJetPhi, fatJetM=fatJetM,
                     sumFatJetM=sumFatJetM, passSR4J=passSR4J,
                     passSR5J=passSR5J, passSR=passSR)

    # Get the remaining unskimmed columns, and skim them
    keys = set(data.keys()) - set(skimData.keys())
    for k in keys:
        skimData[k] = data[k][skimIdx]

    # Finally, add some bookkeeping data
    skimData['totalEvents'] = np.array([num_total])
    skimData['skimEvents'] = np.array([num_baseline])

    return skimData

def filter_delphes_to_numpy(root_file, max_events=None):
    """Processes one file by converting to numpy and applying filtering"""

    # Branch name remapping for convenience
    branch_dict = {
        'Event.Number' : 'eventNumber',
        'Event.ProcessID' : 'proc',
        'Tower.Eta' : 'clusEta',
        'Tower.Phi' : 'clusPhi',
        'Tower.E' : 'clusE',
        'Tower.Eem' : 'clusEM',
        'FatJet.PT' : 'fatJetPt',
        'FatJet.Eta' : 'fatJetEta',
        'FatJet.Phi' : 'fatJetPhi',
        'FatJet.Mass' : 'fatJetM',
        'Track.PT' : 'trackPt',
        'Track.Eta' : 'trackEta',
        'Track.Phi' : 'trackPhi',
    }

    # Convert the data to numpy
    logging.info('Now processing: %s' % root_file)
    data = get_data(root_file, branch_dict, treename='Delphes',
                    stop=max_events)
    if data is None:
        return None

    # Apply physics
    logging.info('Applying event selection')
    skimData = process_events(data)

    # Add the file name to the (meta) data
    skimData['inputFile'] = np.array([os.path.basename(root_file)], dtype='O')

    # Add the cross section for this file
    skimData['xsec'] = np.array([get_xsec(root_file)])

    return skimData

def merge_results(dicts):
    """Merge a list of dictionaries with numpy arrays"""
    dicts = filter(None, dicts)
    # First, get the list of unique keys
    keys = set(key for d in dicts for key in d.keys())
    result = dict()
    for key in keys:
        arrays = [d[key] for d in dicts]
        result[key] = np.concatenate([d[key] for d in dicts])
    return result

def process_files_parallel(input_files, num_workers, max_events=None):
    """Process the input files in parallel with MP"""
    # Create a pool of workers
    logging.info('Starting process pool of %d workers' % num_workers)
    pool = mp.Pool(processes=num_workers)
    # Convert to numpy structure in parallel
    parallel_results = [pool.apply_async(filter_delphes_to_numpy,
                                         (f, max_events))
                        for f in input_files]
    task_data = [r.get() for r in parallel_results]
    pool.close()
    pool.join()
    # Merge the results from each task
    logging.info('Merging results from parallel tasks')
    return merge_results(task_data)

def main():
    """Main execution function"""
    args = parse_args()

    # Logging
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    logging.info('Initializing')

    # Get the input file list
    input_files = []
    for input_list in args.input_file_list:
        with open(input_list) as f:
            input_files.extend(map(str.rstrip, f.readlines()))
    logging.info('Processing %i input files' % len(input_files))

    # Parallel processing
    data = process_files_parallel(input_files, args.num_workers,
                                  args.max_events)

    # Abort gracefully if no events survived skimming
    if data['skimEvents'].sum() == 0:
        logging.info('No events selected by filter. Exiting.')
        return

    # Write results to npz file
    if args.output_npz is not None:
        logging.info('Writing to file %s' % args.output_npz)
        logging.info('Output keys: %s' % data.keys())
        if args.compress:
            np.savez_compressed(args.output_npz, **data)
        else:
            np.savez(args.output_npz, **data)

    # TODO: finish picking up stuff from below

    # Signal region flags
    #passSR4J = data['passSR4J']
    #passSR5J = data['passSR5J']
    #passSR = data['passSR']

    # Print some summary information
    #logging.info('SR4J selected events: %d / %d' % (np.sum(passSR4J), tree.size))
    #weight = data['weight']
    #if weight is not None:
    #    logging.info('SR4J weighted events: %f' % np.sum(weight[passSR4J]))
    #logging.info('SR5J selected events: %d / %d' % (np.sum(passSR5J), tree.size))
    #if weight is not None:
    #    logging.info('SR5J weighted events: %f' % np.sum(weight[passSR5J]))
    #logging.info('SR selected events: %d / %d' % (np.sum(passSR), tree.size))
    #if weight is not None:
    #    logging.info('SR weighted events: %f' % (np.sum(weight[passSR])))

    logging.info('Done!')

if __name__ == '__main__':
    main()
