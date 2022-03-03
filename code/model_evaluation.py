from aifc import Error
import numpy as np
import scipy.io
import xlsxwriter
from model import CAN, CAN_3D, PTS_CAN, TS_CAN, Hybrid_CAN
import h5py
import os
import matplotlib.pyplot as plt
from scipy.signal import butter
from inference_preprocess import preprocess_raw_video, detrend
from sklearn.preprocessing import MinMaxScaler
import pandas as pd
from sklearn import metrics
import scipy.stats as sc
from glob import glob

import heartpy as hp

def prepare_3D_CAN(dXsub):
    frame_depth = 10
    num_window = int(dXsub.shape[0]) - frame_depth + 1
    tempX = np.array([dXsub[f:f + frame_depth, :, :, :] # (491, 10, 36, 36 ,6) (169, 10, 36, 36, 6)
                    for f in range(num_window)])
    tempX = np.swapaxes(tempX, 1, 3) # (169, 36, 36, 10, 6)
    tempX = np.swapaxes(tempX, 1, 2) # (169, 36, 36, 10, 6)
    return tempX

def prepare_Hybrid_CAN(dXsub):
    frame_depth = 10
    num_window = int(dXsub.shape[0]) - frame_depth + 1
    tempX = np.array([dXsub[f:f + frame_depth, :, :, :] # (169, 10, 36, 36, 6)
                        for f in range(num_window)])
    tempX = np.swapaxes(tempX, 1, 3) # (169, 36, 36, 10, 6)
    tempX = np.swapaxes(tempX, 1, 2) # (169, 36, 36, 10, 6)
    motion_data = tempX[:, :, :, :, :3]
    apperance_data = np.average(tempX[:, :, :, :, -3:], axis=-2)
    return motion_data, apperance_data

def predict_vitals(workBook, test_name, model_name, video_path):
    mms = MinMaxScaler()
    img_rows = 36
    img_cols = 36
    frame_depth = 10
    batch_size = 100
    try:
        model_checkpoint = os.path.join("D:/Databases/4)Results/actual_models", test_name, "cv_0_epoch24_model.hdf5")
    except:
        model_checkpoint = os.path.join("D:/Databases/4)Results/actual_models", test_name, "cv_0_epoch23_model.hdf5")
    batch_size = batch_size
    sample_data_path = video_path
    print("path:  ",sample_data_path)

    dXsub, fs = preprocess_raw_video(sample_data_path, dim=36)
    print('dXsub shape', dXsub.shape, "fs: ", fs)

    dXsub_len = (dXsub.shape[0] // frame_depth)  * frame_depth
    dXsub = dXsub[:dXsub_len, :, :, :]
    
    if model_name == "TS_CAN":
        model = TS_CAN(frame_depth, 32, 64, (img_rows, img_cols, 3))
    elif model_name == "3D_CAN":
        model = CAN_3D(frame_depth, 32, 64, (img_rows, img_cols, frame_depth, 3))
        dXsub = prepare_3D_CAN(dXsub)
    elif model_name == "CAN":
        model = CAN(32, 64, (img_rows, img_cols, 3))
    elif model_name == "Hybrid_CAN":
        model = Hybrid_CAN(frame_depth, 32, 64, (img_rows, img_cols, frame_depth, 3),
                            (img_rows, img_cols, 3))
        dXsub1, dXsub2 = prepare_Hybrid_CAN(dXsub)
    elif model_name == "PTS_CAN":
        model = PTS_CAN(frame_depth, 32, 64, (img_rows, img_cols, 3))
    else: 
        raise NotImplementedError

    model.load_weights(model_checkpoint)
    if model_name == "3D_CAN":
        yptest = model.predict((dXsub[:, :, :,: , :3], dXsub[:, :, :, : , -3:]), batch_size=batch_size, verbose=1)
    elif model_name == "Hybrid_CAN":
        yptest = model.predict((dXsub1, dXsub2), batch_size=batch_size, verbose=1)
    else:
        yptest = model.predict((dXsub[:, :, :, :3], dXsub[:, :, :, -3:]), batch_size=batch_size, verbose=1)
    if model_name != "PTS_CAN":
        pulse_pred = yptest
    else:
        pulse_pred = yptest[0]
    
    pulse_pred = detrend(np.cumsum(pulse_pred), 100)
    [b_pulse_pred, a_pulse_pred] = butter(1, [0.75 / fs * 2, 2.5 / fs * 2], btype='bandpass')
    pulse_pred = scipy.signal.filtfilt(b_pulse_pred, a_pulse_pred, np.double(pulse_pred))
    pulse_pred = np.array(mms.fit_transform(pulse_pred.reshape(-1,1))).flatten()
    
    ##### ground truth data resampled  #######
    if(str(sample_data_path).find("COHFACE") > 0):
        truth_path = sample_data_path.replace(".avi", "_dataFile.hdf5")   # akutell für COHACE...
    elif(str(sample_data_path).find("UBFC-PHYS") > 0):
        truth_path = sample_data_path.replace("vid_", "").replace(".avi","_dataFile.hdf5")
    else:
        return("Error in finding the ground truth signal...")

    gound_truth_file = h5py.File(truth_path, "r")
    pulse_truth = gound_truth_file["pulse"]   ### range ground truth from 0 to 1
    pulse_truth = detrend(np.cumsum(pulse_truth), 100)
    [b_pulse_tr, a_pulse_tr] = butter(1, [0.75 / fs * 2, 2.5 / fs * 2], btype='bandpass')
    pulse_truth = scipy.signal.filtfilt(b_pulse_tr, a_pulse_tr, np.double(pulse_truth))
    pulse_truth = np.array(mms.fit_transform(pulse_truth.reshape(-1,1))).flatten()
    ### same size #######
    if len(pulse_pred) > len(pulse_truth):
        pulse_pred = pulse_pred[:len(pulse_truth)]
    elif len(pulse_pred) < len(pulse_truth):
        pulse_truth = pulse_truth[:len(pulse_pred)]
    ########### Peaks ###########
    working_data_pred, measures_pred = hp.process(pulse_pred, fs, calc_freq=True)
    working_data_truth, measures_truth = hp.process(pulse_truth, fs, calc_freq=True)
    peaks_pred = working_data_pred['peaklist']
    peaks_truth = working_data_truth['peaklist']

    ######## name files #############
    nameStrAll = str(sample_data_path).split("/")
    nameStr = ""
    if(str(sample_data_path).find("COHFACE") > 0):
        for item in nameStrAll[4:6]:
            nameStr += item + "-"
    elif(str(sample_data_path).find("UBFC-PHYS") > 0):
        nameStr = str(nameStrAll[5]).replace("vid", "").replace(".avi", "")

     ########## Plot ##################
    peaks_pred_new = []
    for peak in peaks_pred:
        if (peak > 400 and peak <700):
            peaks_pred_new.append(peak-400)
    peaks_truth_new = []
    for peak in peaks_truth:
        if (peak > 400 and peak <700):
            peaks_truth_new.append(peak-400)
    plt.figure() #subplot(211)
    plt.plot(pulse_pred[400:700], label='predicted PPG')
    plt.plot(peaks_truth_new, pulse_truth[400:700][peaks_truth_new], "x")
    plt.plot(peaks_pred_new, pulse_pred[400:700][peaks_pred_new], "o")
    plt.title('PPG prediction with ground truth signal')
    plt.ylabel("normalized Signal [a.u.]")
    plt.xlabel("time (samples)")
    plt.plot(pulse_truth[400:700], label='ground truth')
    plt.legend()
    plt.savefig(nameStr + "_both")

    plt.figure()
    plt.subplot(211)
    plt.plot(pulse_truth[400:700], label='Ground truth')
    plt.plot(peaks_truth_new, pulse_truth[400:700][peaks_truth_new], "x")
    plt.ylabel("normalized Signal [a.u.]")
    plt.title('Ground truth')
    plt.subplot(212)
    plt.plot(pulse_pred[400:700], label='Prediction')
    plt.plot(peaks_pred_new, pulse_pred[400:700][peaks_pred_new], "x")
    plt.title("Predicted rPPG")
    plt.ylabel("normalized Signal [a.u.]")
    plt.xlabel("time (samples)")
    plt.legend()
    plt.savefig(nameStr)

    ########### IBI #############
    #ibi_truth = working_data_truth['RR_list_cor']
    #print(ibi_truth)
    #ibi_pred = working_data_pred['RR_list_cor']
    #print(ibi_pred)
    ######### HRV featurs ##############
    #print("HRV Truth:  ",measures_truth)
    #print("HRV Pred:  ", measures_pred)
    ######### Metrics ##############
    # MSE:
    MAE = metrics.mean_absolute_error(pulse_truth, pulse_pred)
    # RMSE:
    RMSE = metrics.mean_squared_error(pulse_truth, pulse_pred, squared=False)
    # Pearson correlation:
    p = sc.pearsonr(pulse_truth, pulse_pred)

    ####### Logging #############
    worksheet = workBook.add_worksheet(nameStr)
    worksheet.write(0,0, video_path)
    worksheet.write(1,0, "MAE")
    worksheet.write(1,1, MAE)
    worksheet.write(2,0, "RMSE")
    worksheet.write(2,1, RMSE)
    worksheet.write(3,0, "p")
    worksheet.write(3,1, p[0])
    worksheet.write(5,1, "Truth")
    worksheet.write(5,2, "Prediction")
    worksheet.write(6,0, "bpm")
    worksheet.write(6,1, measures_truth["bpm"])
    worksheet.write(6,2, measures_pred["bpm"])
    worksheet.write(7,0, "sdnn")
    worksheet.write(7,1, measures_truth["sdnn"])
    worksheet.write(7,2, measures_pred["sdnn"])
    worksheet.write(8,0, "rmssd")
    worksheet.write(8,1, measures_truth["rmssd"])
    worksheet.write(8,2, measures_pred["rmssd"])
    worksheet.write(9,0, "pnn50")
    worksheet.write(9,1, measures_truth["pnn50"])
    worksheet.write(9,2, measures_pred["pnn50"])
    worksheet.write(10,0, "lf/hf")
    worksheet.write(10,1, measures_truth["lf/hf"])
    worksheet.write(10,2, measures_pred["lf/hf"])
    worksheet.write(11,0, "ibi Average")
    worksheet.write(11,1, measures_truth["ibi"])
    worksheet.write(11,2, measures_pred["ibi"])
    
    worksheet.write(13,0, "pulse_truth")
    col = 0
    for val in pulse_truth:
        worksheet.write(14, col, val)
        col += 1
    worksheet.write(15,0, "pulse_predict")
    col = 0
    for val in pulse_pred:
        worksheet.write(15, col, val)
        col += 1
   
if __name__ == "__main__":
    dir_names = glob("D:/Databases/4)Results/actual_models/*")
    test_names = []
    for dir in dir_names:
        split = dir.split("\\")
        test_names.append(split[len(split)-1])
    batch_size = 8
    video_path = ["D:/Databases/1)Training/COHFACE/5/1/data.avi","D:/Databases/1)Training/COHFACE/10/2/data.avi",
    "D:/Databases/1)Training/UBFC-PHYS/s5/vid_s5_T1.avi", "D:/Databases/1)Training/COHFACE/6/0/data.avi",
    "D:/Databases/1)Training/UBFC-PHYS/s13/vid_s13_T3.avi",
    
    "D:/Databases/2)Validation/UBFC-PHYS/s40/vid_s40_T2.avi", "D:/Databases/2)Validation/UBFC-PHYS/s44/vid_s44_T1.avi",
    "D:/Databases/2)Validation/COHFACE/38/0/data.avi", "D:/Databases/2)Validation/UBFC-PHYS/s38/vid_s38_T1.avi",
    "D:/Databases/2)Validation/COHFACE/34/2/data.avi"]
    
    # video_path = ["D:/Databases/1)Training/COHFACE/5/1/data.avi",
    
    # "D:/Databases/2)Validation/UBFC-PHYS/s40/vid_s40_T2.avi"]
    save_dir = "D:/Databases/5)Evaluation/Comparison_Databases"

    test_names = ["TS_CAN_UBFC_new"]#[ "3D_CAN_MIX", "TS_CAN_MIX_2GPU","Hybrid_CAN_MIX_new",  "CAN_MIX_2GPU"]
    print("Models: ", test_names)
   
    for test_name in test_names:
        print("Current Modelname: ", test_name)
        if str(test_name).find("3D_CAN") >=0:
            model_name = "3D_CAN"
        elif str(test_name).find("Hybrid_CAN") >= 0:
            model_name = "Hybrid_CAN"
        elif str(test_name).find("TS_CAN") >= 0:
            model_name = "TS_CAN"
        elif str(test_name).find("PTS_CAN") >= 0:
            model_name = "PTS_CAN"
            continue
        else:
            if str(test_name).find("CAN") >= 0:
                model_name = "CAN"
            else: 
                raise Error("Model not found...")
        # neuer Ordner für Tests
        os.chdir(save_dir)
        try:
            os.makedirs(str(test_name))
        except:
            print("Directory exists...")
        save_path = os.path.join(save_dir, str(test_name))
        os.chdir(save_path)
        workbook = xlsxwriter.Workbook(test_name + ".xlsx")
        for vid in video_path:
            predict_vitals(workbook, test_name, model_name, vid)
        workbook.close()

#python code/predict_vitals_new.py --video_path "D:\Databases\1)Training\COHFACE\1\1\data.avi" --trained_model ./cv_0_epoch24_model.hdf5
#./rPPG-checkpoints/testCohFace1/cv_0_epoch24_model.hdf5
#./rPPG-checkpoints/test1/cv_0_epoch04_model.hdf5'
#python code/predict_vitals_new.py --video_path "D:\Databases\1)Training\UBFC-PHYS\s1\vid_s1_T1.avi" --trained_model ./cv_0_epoch24_model.hdf5