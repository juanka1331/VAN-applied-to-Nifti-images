import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.getcwd())))

import tarfile
from datetime import datetime
from shutil import copyfile
import numpy as np
import tensorflow as tf
import time
import settings
from lib.data_loader.pet_loader import load_pet_data_flat
from lib.data_loader.mri_loader import load_mri_data_flat
from lib.utils import evaluation_utils
from lib.utils import output_utils
from lib.utils import svm_utils
from lib.utils import cv_utils
from lib.utils.os_aux import create_directories
from lib.over_regions_lib import vae_over_regions
from final_scripts.benchmark import benchmark_helper as helper
from lib.utils.auc_output_handler import stringfy_auc_information
from lib import session_helper as session, timing_helper
from lib.utils.evaluation_utils import get_average_over_metrics
from lib.utils.evaluation_logger_helper  import evaluation_container_to_log_file



"""
Still need to test MRI with this script,
and completely use in a long run server
"""

session_datetime = datetime.now().isoformat()
print("Time session init: {}".format(session_datetime))

images_used = "MRI"
#images_used = "PET"

# SWAAP SETTINGS
n_folds = 10
bool_test = False
bool_log_svm_output = True
#swap_list = [2, 10, 20, 50, 80, 100, 150, 200, 250, 300]
swap_list = [2, 5, 8, 10, 20, 50, 80, 100, 150, 200, 225, 250]
swap_over = "latent_layer"

# Evaluation thresholds
#SVM_over_regions_threshold = None
SVM_over_regions_threshold = 0 # Middle value
#SMV_over_regions_threshold = None
SMV_over_regions_threshold = 0.5 # Middle value
#CMV_over_regions_threshold = None
CMV_over_regions_threshold = 0# Middle value

# SELECT REGIONS TO LOOP OVER
#regions_used = "three"
regions_used = "most_important"
list_regions = session.select_regions_to_evaluate(regions_used)
#list_regions = [1, 2, 3, 4]

# VAE SETTINGS
# Net Configuration
hyperparams = {
    "batch_size": 256,
    "learning_rate": 1E-5,
    "dropout": 0.8,
    "lambda_l2_reg": 1E-5,
    "nonlinearity": tf.nn.elu,
    "squashing": tf.nn.sigmoid,
}

# Vae session cofiguration
session_conf = {
    "bool_normalized": True,
    "max_iter": 200,
    "save_meta_bool": False,
    "show_error_iter": 10,
    "after_input_architecture": [1000, 500], # no include hidden layer
}


# PATH Settings
session_name = "VAE_session_{0}_{1}".format(swap_over, images_used)
session_path = os.path.join(settings.path_to_general_out_folder, session_name)
historical_path = os.path.join(session_path, "historical")
create_directories([session_path, historical_path])

# OUTPUT: Files initialization
loop_output_file_simple_majority_vote = os.path.join(
    session_path, "loop_output_simple_majority_vote.csv")

loop_output_file_complex_majority_vote = os.path.join(
    session_path, "loop_output_complex_majority_vote.csv")

loop_output_file_weighted_svm = os.path.join(
    session_path, "loop_output_weighted_svm.csv")

loop_output_file_timing = os.path.join(
    session_path, "loop_output_timing.csv")

evaluations_per_sample_log_file = os.path.join(
    session_path, "test_scores_evaluation_per_sample.log")

full_evaluations_per_sample_log_file = os.path.join(
    session_path, "full_scores_evaluation_per_sample.log")

loop_output_path_session_description = os.path.join(
    session_path, "session_description.csv")

tar_file_main_output_path = os.path.join(
    session_path, "{0}_{1}.tar.gz".format(historical_path, session_datetime))

roc_logs_file_path = os.path.join(session_path, "roc.logs")

list_paths_files_to_store = [loop_output_file_simple_majority_vote,
                             loop_output_file_complex_majority_vote,
                             loop_output_file_weighted_svm,
                             roc_logs_file_path,
                             loop_output_path_session_description,
                             loop_output_file_timing,
                             evaluations_per_sample_log_file]

# SESSION DESCRIPTOR ELABORATION
session_descriptor = {}
session_descriptor['meta settings'] = {
    "n_folds": n_folds,
    "bool_test": bool_test,
    "regions_used": regions_used,
    "loop_over_{}".format(swap_over): str(swap_list),
    "Support_Vector_Machine over regions threshold": SVM_over_regions_threshold,
    "Simple_Majority_Vote over regions threshold": SMV_over_regions_threshold,
    "Complex_Majority_Vote over regions threshold": CMV_over_regions_threshold
}

session_descriptor['VAE'] = {}
session_descriptor['VAE']["net configuration"] = hyperparams
session_descriptor['VAE']["session configuration"] = session_conf

file_session_descriptor = open(loop_output_path_session_description, "w")
output_utils.print_recursive_dict(session_descriptor,
                                  file=file_session_descriptor)
file_session_descriptor.close()


# Loading data // Initialize
n_samples = 0
patient_labels = None
dic_regions_to_flatten_voxels_pet = None
dic_regions_to_flatten_voxels_mri_gm = None
dic_regions_to_flatten_voxels_mri_wm = None

if images_used == "PET":
    dic_regions_to_flatten_voxels_pet, patient_labels, n_samples = \
        load_pet_data = load_pet_data_flat(list_regions)
elif images_used == "MRI":
    dic_regions_to_flatten_voxels_mri_gm, dic_regions_to_flatten_voxels_mri_wm, \
        patient_labels, n_samples = load_mri_data_flat(list_regions)


# STATISTICS HOLDER

# RESULTS CONTAINER //  Initialize
list_averages_svm_weighted = []
list_averages_simple_majority_vote = []
list_averages_decision_net = []
list_averages_complex_majority_vote = []
list_averages_timing = []

# Curva ROC info
roc_logs_file = open(roc_logs_file_path, "w")
auc_header = "{0}; fold; evaluation; test|train; " \
             "false_positive_rate; true_positive_rate;" \
            "threshold ".format(swap_over)
roc_logs_file.write("{}\n".format(auc_header))

# Container to store evaluation information
dic_container_evaluations = {
    "SVM": {},
    "SMV": {},
    "CMV": {},
}

# Structure to store the kfold sample distribution in each swap value
k_fold_container = {}

for swap_variable_index in swap_list:

    print("Evaluating the system with a {0} of {1} ".format(
        swap_over, swap_variable_index))

    session_conf["after_input_architecture"].append(swap_variable_index)

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

    # initializing evaluation container
    dic_container_evaluations["SVM"][swap_variable_index] = {}
    dic_container_evaluations["SMV"][swap_variable_index] = {}
    dic_container_evaluations["CMV"][swap_variable_index] = {}

    # Different timing dict per class NOR/AD
    if images_used == "MRI":
        timing = {
            "MRI_GM_neuralnet": [],
            "MRI_WM_neuralnet": [],
        }
    elif images_used == "PET":
        timing = {
            "PET": [],
        }

    # Generate Kfold for the next iteration and registry it
    k_fold_dict = cv_utils.generate_k_folder_in_dict(n_samples, n_folds)
    k_fold_container[swap_variable_index] = k_fold_dict

    for k_fold_index in range(0, n_folds, 1):
        print("Kfold {} Selected".format(k_fold_index))
        vae_output = {}

        # Structure the data Dic["test|"train"] -> Samples (Known the kfold)
        if images_used == "MRI":
            reg_to_group_to_images_dict_mri_gm = \
                cv_utils.restructure_dictionary_based_on_cv(
                    dict_train_test_index=k_fold_dict[k_fold_index],
                    region_to_img_dict=dic_regions_to_flatten_voxels_mri_gm)

            reg_to_group_to_images_dict_mri_wm = \
                cv_utils.restructure_dictionary_based_on_cv(
                    dict_train_test_index=k_fold_dict[k_fold_index],
                    region_to_img_dict=dic_regions_to_flatten_voxels_mri_wm)

        if images_used == "PET":
            reg_to_group_to_images_dict_pet = \
                cv_utils.restructure_dictionary_based_on_cv(
                    dict_train_test_index=k_fold_dict[k_fold_index],
                    region_to_img_dict=dic_regions_to_flatten_voxels_pet)

        Y_train, Y_test = \
            cv_utils.get_test_and_train_labels_from_kfold_dict_entry(
                k_fold_entry=k_fold_dict[k_fold_index],
                patient_labels=patient_labels)

        if bool_test:
            print("Kfold {} Selected".format(k_fold_index))
            print("Number test samples {}".format(len(Y_test)))
            print("Number train samples {}".format(len(Y_train)))

        # MRI Auto-encoder Extract of features
        if images_used == "MRI":

            print("Training MRI regions over GM")
            time_reference = time.time()

            vae_output['gm'] = vae_over_regions.execute_without_any_logs(
                region_to_flat_voxels_train_dict=
                reg_to_group_to_images_dict_mri_gm["train"],
                hyperparams=hyperparams,
                session_conf=session_conf,
                list_regions=list_regions,
                path_to_root=None,
                region_to_flat_voxels_test_dict=
                reg_to_group_to_images_dict_mri_gm["test"],
                explicit_iter_per_region=[])

            timing["MRI_GM_neuralnet"].append(time.time() - time_reference)
         #   print("Not converging regions GM {}".format(str(
         #       regions_whose_net_not_converge_gm)))

            print("Training MRI regions over WM")

            time_reference = time.time()
            vae_output['wm'] = vae_over_regions.execute_without_any_logs(
                region_to_flat_voxels_train_dict=
                reg_to_group_to_images_dict_mri_wm["train"],
                hyperparams=hyperparams,
                session_conf=session_conf,
                list_regions=list_regions,
                path_to_root=None,
                region_to_flat_voxels_test_dict=
                reg_to_group_to_images_dict_mri_wm["test"],
                explicit_iter_per_region=[]
            )

            timing["MRI_WM_neuralnet"].append(time.time() - time_reference)

            #print("Not converging regions GM {}".format(
            #    str(regions_whose_net_not_converge_wm)))

            #regions_whose_net_not_converge = \
            #    regions_whose_net_not_converge_gm + \
            #    [x for x in regions_whose_net_not_converge_wm
            #     if x not in regions_whose_net_not_converge_gm]

            #print("Not converging total regions {}".format(
            #    str(regions_whose_net_not_converge)))

            #available_regions = [region for region in list_regions
            #                     if region not in regions_whose_net_not_converge]

            #if len(available_regions) == 0:
            #    print("No one region neural net converges successfully,"
            #          "The parameters used should be changed. Exiting")
            #    sys.exit(0)

            # [patient x region]

            train_score_matriz, test_score_matriz = \
                svm_utils.svm_mri_over_vae_output(
                vae_output, Y_train, Y_test, list_regions, bool_test=bool_test)

        if images_used == "PET":
            print("Train PET over regions")
            time_reference = time.time()

            vae_output = vae_over_regions.execute_without_any_logs(
                region_to_flat_voxels_train_dict=
                reg_to_group_to_images_dict_pet["train"],
                hyperparams=hyperparams,
                session_conf=session_conf,
                list_regions=list_regions,
                path_to_root=None,
                region_to_flat_voxels_test_dict=reg_to_group_to_images_dict_pet[
                    "test"],
                explicit_iter_per_region=[]
            )

            timing["PET"].append(time.time() - time_reference)

           # print("Not converging total regions {}".format(
           #     str(regions_whose_net_not_converge))
           # available_regions = [region for region in list_regions
           #                      if region not in regions_whose_net_not_converge]
           # if len(available_regions) == 0:
           #     print("No one region neural net converges successfully,"
           #           "The parameters used should be changed. Exiting")
           #     sys.exit(0)

            train_score_matriz, test_score_matriz = svm_utils.svm_pet_over_vae_output(
                vae_output, Y_train, Y_test, list_regions,
                bool_test=bool_test)
        # End Auto-encoder Process. Extraction of Feature

        data = helper.organize_data(
            test_score_matriz, Y_test, train_score_matriz, Y_train)

        # COMPLEX MAJORITY VOTE

        if bool_test:
            print("\nMatriz svm scores -> shapes, before complex majority vote")
            print("train matriz [patients x region]: " + str(
                train_score_matriz.shape))
            print("test matriz scores [patient x region]: " + str(
                test_score_matriz.shape))

        complex_output_dic_test, complex_output_dic_train, roc_dic, \
            CMV_means_activation_dic = \
            evaluation_utils.complex_majority_vote_evaluation(
                data, bool_test=bool_test,
                threshold_fixed=CMV_over_regions_threshold)

        # Adding logs about means activation:
        dic_container_evaluations["CMV"][swap_variable_index][k_fold_index] = \
            CMV_means_activation_dic

        # Adding roc results to log file
        roc_test_string, roc_train_string = stringfy_auc_information(
            swap_over=swap_variable_index,
            k_fold_index=k_fold_index,
            evaluation="Complex_Majority_Vote",
            roc_dic=roc_dic)
        roc_logs_file.write("{}\n".format(roc_train_string))
        roc_logs_file.write("{}\n".format(roc_test_string))

        # Adding results to kfolds output
        complex_majority_vote_k_folds_results_train.append(
            complex_output_dic_train)
        complex_majority_vote_k_folds_results_test.append(
            complex_output_dic_test)

        if bool_test:
            print("\nMatriz svm scores -> shapes, after complex majority vote")
            print("train matriz [patients x regions]: " + str(
                train_score_matriz.shape))
            print("test matriz scores [patients x regions]: " + str(
                test_score_matriz.shape))

        print("Complex Majority Vote Test: " + str(complex_output_dic_test))
        print("Complex Majority Vote Train: " + str(complex_output_dic_train))

        # SIMPLE MAJORITY VOTE

        simple_output_dic_train, simple_output_dic_test, roc_dic, \
            SMV_means_activation_dic = \
            evaluation_utils.simple_majority_vote(
                data, bool_test=False,
                threshold_fixed=SMV_over_regions_threshold)

        # Adding logs about means activation:
        dic_container_evaluations["SMV"][swap_variable_index][k_fold_index] = \
            SMV_means_activation_dic

        roc_test_string, roc_train_string = stringfy_auc_information(
            swap_over=swap_variable_index,
            k_fold_index=k_fold_index,
            evaluation="Simple_Majority_Vote",
            roc_dic=roc_dic)
        roc_logs_file.write("{}\n".format(roc_train_string))
        roc_logs_file.write("{}\n".format(roc_test_string))

        print("Simple Majority Vote Test: " + str(simple_output_dic_test))
        print("Simple Majority Vote Train: " + str(simple_output_dic_train))

        simple_majority_vote_k_folds_results_train.append(
            simple_output_dic_train)
        simple_majority_vote_k_folds_results_test.append(
            simple_output_dic_test)

        # SVM weighted REGIONS RESULTS
        print("DECISION WEIGHTING SVM OUTPUTS")

        # SVM Evaluation
        weighted_output_dic_test, weighted_output_dic_train, \
            aux_dic_regions_weight_coefs, roc_dic, \
            evaluation_sample_scores = \
            evaluation_utils.weighted_svm_decision_evaluation(
                    data, list_regions,
                    bool_test=bool_test,
                    threshold_fixed=SVM_over_regions_threshold)

        # Evaluations Logging
        dic_container_evaluations["SVM"][swap_variable_index][k_fold_index] = \
            evaluation_sample_scores

        # Roc logging
        roc_test_string, roc_train_string = stringfy_auc_information(
            swap_over=swap_variable_index,
            k_fold_index=k_fold_index,
            evaluation="SVM_weighted",
            roc_dic=roc_dic)
        roc_logs_file.write("{}\n".format(roc_train_string))
        roc_logs_file.write("{}\n".format(roc_test_string))

        # Appending results
        svm_weighted_regions_k_folds_results_train.append(
            weighted_output_dic_train)
        svm_weighted_regions_k_folds_results_test.append(
            weighted_output_dic_test)
        svm_weighted_regions_k_folds_coefs.append(aux_dic_regions_weight_coefs)

        print("SVM classification Test: " + str(weighted_output_dic_test))
        print("SVM classification Train: " + str(weighted_output_dic_train))
    # KFOLD LOOP ENDED

    # Evaluation of K-folds results of an swap value
    # Extra field, swap over property
    extra_field = {swap_over: str(swap_variable_index)}

    # Timing scripts, mean over kfolds results

    average_timing = timing_helper.get_averages_timing_dict_per_images_used(
        timing_dict=timing,
        images_used=images_used
    )
    average_timing.update(extra_field)

    # GET AVERAGE RESULTS OVER METRICS
    averages_simple_majority_vote = get_average_over_metrics(
        simple_majority_vote_k_folds_results_test)
    averages_simple_majority_vote.update(extra_field)

    averages_complex_majority_vote = get_average_over_metrics(
        complex_majority_vote_k_folds_results_test)
    averages_complex_majority_vote.update(extra_field)

    averages_svm_weighted = get_average_over_metrics(
        svm_weighted_regions_k_folds_results_test)
    averages_svm_weighted.update(extra_field)

    # Appending results of each evaluation mode
    list_averages_svm_weighted.append(averages_svm_weighted)
    list_averages_simple_majority_vote.append(averages_simple_majority_vote)
    list_averages_complex_majority_vote.append(averages_complex_majority_vote)
    list_averages_timing.append(average_timing)

# Outputs files
# simple majority
output_utils.print_dictionary_with_header(
    loop_output_file_simple_majority_vote,
    list_averages_simple_majority_vote)
# complex majority
output_utils.print_dictionary_with_header(
    loop_output_file_complex_majority_vote,
    list_averages_complex_majority_vote)

output_utils.print_dictionary_with_header(
    loop_output_file_weighted_svm,
    list_averages_svm_weighted)

output_utils.print_dictionary_with_header(
    loop_output_file_timing,
    list_averages_timing)

roc_logs_file.close()

evaluation_container_to_log_file(
    path_file_test_out=evaluations_per_sample_log_file ,
    path_file_full_out = full_evaluations_per_sample_log_file,
    evaluation_container=dic_container_evaluations,
    k_fold_container = k_fold_container,
    swap_variable_list=swap_list,
    n_samples=n_samples)

# Tarfile to group the results
tar = tarfile.open(tar_file_main_output_path, "w:gz")
for file in list_paths_files_to_store:
    tar.add(file)
tar.close()

