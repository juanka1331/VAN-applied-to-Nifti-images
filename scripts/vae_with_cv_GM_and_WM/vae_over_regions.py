import os
from datetime import datetime

import tensorflow as tf

from lib import session_helper as session
from lib import utils
from lib.data_loader import MRI_stack_NORAD
from lib.data_loader import mri_atlas
from lib.session_helper import generate_session_descriptor
from lib.utils import cv_utils
from lib.utils.os_aux import create_directories
from lib.vae import VAE
from scripts.vae_with_cv_GM_and_WM import session_settings


def init_session_folders(architecture, path_to_root):
    """
    This method will create inside the "out" folder a folder with the datetime
    of the execution of the neural net and with, with 3 folders inside it
    :return:
    """
    own_datetime = datetime.now().strftime(r"%d_%m_%_Y_%H:%M")
    iden_session = own_datetime + " arch: " + "_".join(map(str, (architecture)))
    path_session_folder = os.path.join(path_to_root,
                                       iden_session)

    path_to_images = os.path.join(path_session_folder, session.folder_images)
    path_to_logs = os.path.join(path_session_folder, session.folder_log)
    path_to_meta = os.path.join(path_session_folder, session.folder_meta)
    path_to_encoding_out = os.path.join(path_session_folder,
                                        session.folder_encoding_out)
    path_to_encoding_out_test = os.path.join(path_session_folder,
                                             session.folder_encoding_out_test)
    path_to_encoding_out_train = os.path.join(path_session_folder,
                                              session.folder_encoding_out_train)
    path_to_grad_desc_error = os.path.join(path_to_logs, "DescGradError")
    path_to_grad_desc_error_images = os.path.join(path_to_images,
                                                  "DescGradError")

    create_directories([path_session_folder, path_to_images,
                        path_to_logs, path_to_meta, path_to_grad_desc_error,
                        path_to_grad_desc_error_images, path_to_encoding_out,
                        path_to_encoding_out, path_to_encoding_out_test,
                        path_to_encoding_out_train])

    return path_session_folder, path_to_grad_desc_error, \
           path_to_grad_desc_error_images, path_to_encoding_out_test, \
           path_to_encoding_out_train


def auto_execute():
    # Autoenconder configuration
    hyperparams = {
        "batch_size": 16,
        "learning_rate": 5E-6,
        "dropout": 0.9,
        "lambda_l2_reg": 1E-5,
        "nonlinearity": tf.nn.elu,
        "squashing": tf.nn.sigmoid,
    }

    # Neural net architecture
    after_input_architecture = [1000, 800, 500, 100]

    # SESSION CONFIGURATION
    session_conf = {
        "cv_rate": 0.6,
        "bool_normalized": True,
        "regions_used": "68to117",
        "max_iter": 2000,
    }

    # Selecting the GM folder
    #dict_norad = stack_NORAD.get_gm_stack()
    #path_to_root = session_settings.path_GM_folder
    #path_cv_index_folder = session_settings.path_cv_folder

    # Selecting the wM folder
    dict_norad = MRI_stack_NORAD.get_wm_stack()
    path_to_root = session_settings.path_WM_folder
    path_cv_index_folder = session_settings.path_cv_folder



    execute(dict_norad, hyperparams, session_conf, after_input_architecture,
            path_to_root, path_to_cv_index_folder=path_cv_index_folder)


def execute(dict_norad, hyperparams, session_conf, after_input_architecture,
            path_to_root, path_to_cv_index_folder=None):
    """

    :param hyperparams:
    :param session_settings:
    :param path_to_root: It is the folder where the folder of the session
    is going to be generated
    :return:
    """
    region_voxels_index_per_region = mri_atlas.load_atlas_mri()

    path_session_folder, path_to_grad_desc_error, \
    path_to_grad_desc_error_images, \
    path_to_encoding_out_test, path_to_encoding_out_train \
        = init_session_folders(after_input_architecture, path_to_root)

    # Cross validation decisions
    if path_to_cv_index_folder == None:
        # initialize cv session folder
        path_to_cv = os.path.join(path_session_folder, session.fodler_cv)
        create_directories([path_to_cv])

        train_index, test_index = cv_utils.generate_and_store_train_and_test_index(
            dict_norad['stack'], session_settings['cv_rate'], path_to_cv)
    else:
        train_index, test_index = cv_utils.get_train_and_test_index_from_files(
            path_to_cv_index_folder)

    # GEMERATING SESSION DESCRIPTOR
    session_descriptor_data = {"architecture:": "input_" + "_".join(
        str(x) for x in after_input_architecture)}
    session_descriptor_data.update(hyperparams)
    session_descriptor_data.update(session_conf)
    generate_session_descriptor(path_session_folder, session_descriptor_data)

    # LIST REGIONS SELECTION
    list_regions = session.select_regions_to_evaluate(session_conf["regions_used"])

    # LOOP OVER REGIONS
    for region_selected in list_regions:
        print("Region Nº {} selected".format(region_selected))
        voxels_index = region_voxels_index_per_region[region_selected]

        # First map and the normalize to the unit the value of the pixels
        region_voxels_values = dict_norad['stack'][:, voxels_index]
        region_voxels_values_train = region_voxels_values[train_index, :]
        region_voxels_values_test = region_voxels_values[test_index, :]

        if session_conf['bool_normalized']:
            region_voxels_values, max_denormalize = \
                utils.normalize_array(region_voxels_values)

        architecture = [region_voxels_values.shape[1]]
        architecture.extend(after_input_architecture)

        tf.reset_default_graph()
        v = VAE.VAE(architecture, hyperparams,
                    path_to_session=path_session_folder)

        region_suffix = 'region_' + str(region_selected)

        v.train(region_voxels_values_train,
                max_iter=session_conf["max_iter"],
                save_bool=save_bool, suffix_files_generated=region_suffix,
                iter_to_save=500, iters_to_show_error=100)

        # Script para pintar
        print("Region {} Trained!".format(region_selected))

        session.plot_grad_desc_error_per_region(path_to_grad_desc_error,
                                                region_selected,
                                                path_to_grad_desc_error_images)


        # ENCODING PHASE
        # Encoding samples for the next step
        train_output = v.encode(region_voxels_values_train)
        test_output = v.encode(region_voxels_values_test)

        session.save_encoding_output_per_region(path_to_encoding_out_train,
                                                train_output, region_selected)
        session.save_encoding_output_per_region(path_to_encoding_out_test,
                                                test_output, region_selected)


# np.savetxt("test.mat", matriz, delimiter=",")
# np.genfromtxt("test.mat", delimiter=",")


#auto_execute()
