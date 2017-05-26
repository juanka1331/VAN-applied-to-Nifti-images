import tensorflow as tf
import os
from lib import cv_utils
from lib.mri import stack_NORAD
from lib import session_helper as session
from scripts.vae_with_kfolds import session_settings
from scripts.vae_with_kfolds import vae_over_regions_kfolds
from lib.mri.stack_NORAD import load_patients_labels
from lib import svm_utils
from lib.evaluation_utils import simple_evaluation_output
from lib.evaluation_utils import get_average_over_metrics
from lib import evaluation_utils
from lib import output_utils
from shutil import copyfile
import numpy as np
import tarfile
from datetime import datetime
from lib.neural_net.manual_layer_decision_net import DecisionNeuralNet as \
    DecisionNeuralNet_leaky_relu_3layers_with_sigmoid
from lib.neural_net.decision_neural_net import DecisionNeuralNet


session_datetime = datetime.now().isoformat()
print("Time session init: {}".format(session_datetime))

# Meta settings.
n_folds = 10
bool_test = False
regions_used = "most_important"

# Vae settings
# Net Configuration
middle_architecture = [1000, 500]
latent_code_dim_list = [2, 5, 8, 10, 25, 50, 75, 100, 125, 150, 175, 200]
list_regions = session.select_regions_to_evaluate(regions_used)

hyperparams_vae = {
    "batch_size": 16,
    "learning_rate": 1E-5,
    "dropout": 0.9,
    "lambda_l2_reg": 1E-5,
    "nonlinearity": tf.nn.elu,
    "squashing": tf.nn.sigmoid,
}

# Vae session cofiguration
vae_session_conf = {
    "bool_normalized": True,
    "max_iter": 100,
    "save_meta_bool": False,
    "show_error_iter": 10,
}

# DECISION NET CONFIGURATION
decision_net_session_conf = {
    "decision_net_tries": 1,
    "field_to_select_try": "area under the curve",
    "max_iter": 50,
    "threshould_prefixed_to_0.5": True,
}

HYPERPARAMS_decision_net = {
    "batch_size": 200,
    "learning_rate": 1E-6,
    "lambda_l2_reg": 0.000001,
    "dropout": 1,
    "nonlinearity": tf.nn.relu,
}

# Selecting the GM folder
path_to_root_GM = session_settings.path_GM_folder
path_to_root_WM = session_settings.path_WM_folder
# Loading the stack of images
dict_norad_gm = stack_NORAD.get_gm_stack()
dict_norad_wm = stack_NORAD.get_wm_stack()
patient_labels = load_patients_labels()

# OUTPUT: Files initialization
k_fold_output_file_simple_majority_vote = os.path.join(
    session_settings.path_kfolds_session_folder,
    "loop_output_simple_majority_vote.csv")

k_fold_output_file_complex_majority_vote = os.path.join(
    session_settings.path_kfolds_session_folder,
    "loop_output_complex_majority_vote.csv")

k_fold_output_file_decision_net = os.path.join(
    session_settings.path_kfolds_session_folder,
    "loop_output_decision_net.csv")

k_fold_output_file_weighted_svm = os.path.join(
    session_settings.path_kfolds_session_folder,
    "loop_output_weighted_svm.csv")

k_fold_output_path_session_description = os.path.join(
    session_settings.path_kfolds_session_folder,
    "session_description.csv")


# SESSION DESCRIPTOR ELLABORATION
session_descriptor = {}
session_descriptor['meta settings'] = {"n_folds": n_folds,
                                       "bool_test": bool_test,
                                       "regions_used": regions_used}
session_descriptor['VAE'] = {}
session_descriptor['Decision net'] = {}
session_descriptor['VAE']["net configuration"] = hyperparams_vae
session_descriptor['VAE']["net configuration"][
    "architecture"] = "input_" + "_".join(
    str(x) for x in middle_architecture)
session_descriptor['VAE']["session configuration"] = vae_session_conf
session_descriptor['Decision net'][
    "net configuration"] = HYPERPARAMS_decision_net
session_descriptor['Decision net']["net configuration"]['architecture'] = \
    "[nºregions, nºregions/2, 1]"
session_descriptor['Decision net']['session_conf'] = decision_net_session_conf

file_session_descriptor = open(k_fold_output_path_session_description, "w")
output_utils.print_recursive_dict(session_descriptor,
                                  file=file_session_descriptor)
file_session_descriptor.close()


for latent_dim in latent_code_dim_list
    # OUTPUT SETTINGS
    # OUTPUT: List of dictionaries
    complex_majority_vote_k_folds_results_train = []
    complex_majority_vote_k_folds_results_test = []

    simple_majority_vote_k_folds_results_train = []
    simple_majority_vote_k_folds_results_test = []

    decision_net_k_folds_results_train = []
    decision_net_vote_k_folds_results_test = []

    svm_weighted_regions_k_folds_results_train = []
    svm_weighted_regions_k_folds_results_test = []
    svm_weighted_regions_k_folds_coefs = []

    cv_utils.generate_k_fold(session_settings.path_kfolds_folder,
                             dict_norad_gm['stack'], n_folds)

    for k_fold_index in range(1, n_folds + 1, 1):
        vae_output = {}

        train_index, test_index = cv_utils.get_train_and_test_index_from_k_fold(
            session_settings.path_kfolds_folder, k_fold_index, n_folds)

        Y_train = patient_labels[train_index]
        Y_test = patient_labels[test_index]
        Y_train = np.row_stack(Y_train)
        Y_test = np.row_stack(Y_test)

        print("Kfold {} Selected".format(k_fold_index))
        print("Number test samples {}".format(len(test_index)))
        print("Number train samples {}".format(len(train_index)))

        voxels_values = {}
        voxels_values['train'] = dict_norad_gm['stack'][train_index, :]
        voxels_values['test'] = dict_norad_gm['stack'][test_index, :]

        print("Train over GM regions")
        middle_architecture.extend([latent_dim])
        vae_output['gm'] = vae_over_regions_kfolds.execute(voxels_values,
                                                           hyperparams_vae,
                                                           vae_session_conf,
                                                           middle_architecture,
                                                           path_to_root_GM,
                                                           list_regions)

        voxels_values = {}
        voxels_values['train'] = dict_norad_wm['stack'][train_index, :]
        voxels_values['test'] = dict_norad_wm['stack'][test_index, :]

        print("Train over WM regions")
        vae_output['wm'] = vae_over_regions_kfolds.execute(voxels_values,
                                                           hyperparams_vae,
                                                           vae_session_conf,
                                                           middle_architecture,
                                                           path_to_root_WM,
                                                           list_regions)

