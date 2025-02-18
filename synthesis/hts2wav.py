#!/usr/bin/env python3
# Copyright (c) 2021 oatsu
# Copyright (c) 2021 maka_makamo
# Copyright (c) 2020 Ryuichi Yamamoto
"""
フルラベルから音声ファイルを生成する。
nnsvs.bin.synthesis を改変した。
"""
# ---------------------------------------------------------------------------------
#
# MIT License
#
# Copyright (c) 2020 Ryuichi Yamamoto
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the 'Software'), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED 'AS IS', WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
# ---------------------------------------------------------------------------------

from datetime import datetime
from os.path import join, relpath, split, splitext
from sys import argv

import hydra
import joblib
import numpy as np
import torch
from hydra.experimental import compose, initialize
from nnmnkwii.io import hts
from nnsvs.bin.synthesis import maybe_set_normalization_stats_
from nnsvs.gen import (postprocess_duration, predict_acoustic,
                       predict_duration, predict_timelag)
from nnsvs.logger import getLogger
from nnsvs_gen_override import gen_waveform
from omegaconf import DictConfig, OmegaConf
from scipy.io import wavfile


def maybe_set_checkpoints_(config: DictConfig):
    """
    configファイルを参考に、使用するチェックポイントを設定する。
    """
    model_dir = config.model_dir
    for typ in ('timelag', 'duration', 'acoustic'):
        # checkpoint of each model
        if config[typ].checkpoint is None:
            config[typ].checkpoint = join(model_dir, typ, 'best_loss.pth')
        else:
            config[typ].checkpoint = join(model_dir, typ, config[typ].checkpoint)


def estimate_bit_depth(wav: np.ndarray) -> str:
    """
    wavformのビット深度を判定する。
    16bitか32bit
    16bitの最大値: 32767
    32bitの最大値: 2147483647
    """
    # 音量の最大値を取得
    max_gain = np.max(np.abs(wav))
    # 学習データのビット深度を推定(8388608=2^24)
    if max_gain > 8388608:
        return 'int32'
    if max_gain > 8:
        return 'int16'
    return 'float'


def generate_wav_file(config: DictConfig, wav, out_wav_path):
    """
    ビット深度を指定してファイル出力(32bit float)
    """
    # 出力された音量をもとに、学習に使ったビット深度を推定
    training_data_bit_depth = estimate_bit_depth(wav)
    # print(training_data_bit_depth)

    # 16bitで学習したモデルの時
    if training_data_bit_depth == 'int16':
        wav = wav / 32767
    # 32bitで学習したモデルの時
    elif training_data_bit_depth == 'int32':
        wav = wav / 2147483647
    elif training_data_bit_depth == 'float':
        pass
    # なぜか16bitでも32bitでもないとき
    else:
        raise ValueError('WAVのbit深度がよくわかりませんでした。')

    # 音量ノーマライズする場合
    if config.gain_normalize:
        wav = wav / np.max(np.abs(wav))

    # ファイル出力
    wav = wav.astype(np.float32)
    wavfile.write(out_wav_path, rate=config.sample_rate, data=wav)


def set_each_question_path(config: DictConfig):
    """
    qstを読み取るのめんどくさい
    """
    # hedファイルを全体で指定しているか、各モデルで設定しているかを判定する
    for typ in ('timelag', 'duration', 'acoustic'):
        if config[typ].question_path is None:
            config[typ].question_path = config.question_path
        else:
            config[typ].question_path = config[typ].question_path


def load_qst(question_path, append_hat_for_LL=False) -> tuple:
    """
    question.hed ファイルを読み取って、
    binary_dict, continuous_dict, pitch_idx, pitch_indices を返す。
    """
    binary_dict, continuous_dict = hts.load_question_set(
        question_path, append_hat_for_LL=append_hat_for_LL)
    pitch_indices = np.arange(len(binary_dict), len(binary_dict) + 3)
    pitch_idx = len(binary_dict) + 1
    return (binary_dict, continuous_dict, pitch_indices, pitch_idx)


def synthesis(config, device, label_path,
              timelag_model, timelag_config, timelag_in_scaler, timelag_out_scaler,
              duration_model, duration_config, duration_in_scaler, duration_out_scaler,
              acoustic_model, acoustic_config, acoustic_in_scaler, acoustic_out_scaler):
    """
    音声ファイルを合成する。
    """
    # load labels and question
    labels = hts.load(label_path).round_()
    # load questions
    set_each_question_path(config)
    log_f0_conditioning = config.log_f0_conditioning

    if config.ground_truth_duration:
        # Use provided alignment
        duration_modified_labels = labels
    else:
        # Time-lag predictions
        timelag_binary_dict, timelag_continuous_dict, timelag_pitch_indices, _ \
            = load_qst(config.timelag.question_path)
        lag = predict_timelag(
            device, labels,
            timelag_model,
            timelag_config,
            timelag_in_scaler,
            timelag_out_scaler,
            timelag_binary_dict,
            timelag_continuous_dict,
            timelag_pitch_indices,
            log_f0_conditioning,
            config.timelag.allowed_range)

        # Duration predictions
        duration_binary_dict, duration_continuous_dict, duration_pitch_indices, _ \
            = load_qst(config.duration.question_path)
        durations = predict_duration(
            device, labels,
            duration_model,
            duration_config,
            duration_in_scaler,
            duration_out_scaler,
            lag,
            duration_binary_dict,
            duration_continuous_dict,
            duration_pitch_indices,
            log_f0_conditioning)
        # Normalize phoneme durations
        duration_modified_labels = postprocess_duration(labels, durations, lag)

    acoustic_binary_dict, acoustic_continuous_dict, acoustic_pitch_indices, acoustic_pitch_idx \
        = load_qst(config.acoustic.question_path)
    # Predict acoustic features
    acoustic_features = predict_acoustic(
        device, duration_modified_labels,
        acoustic_model,
        acoustic_config,
        acoustic_in_scaler,
        acoustic_out_scaler,
        acoustic_binary_dict,
        acoustic_continuous_dict,
        config.acoustic.subphone_features,
        acoustic_pitch_indices,
        log_f0_conditioning)

    # Generate f0, mgc, bap, waveform
    f0, mgc, bap, generated_waveform = gen_waveform(
        duration_modified_labels,
        acoustic_features,
        acoustic_binary_dict,
        acoustic_continuous_dict,
        acoustic_config.stream_sizes,
        acoustic_config.has_dynamic_features,
        config.acoustic.subphone_features,
        log_f0_conditioning,
        acoustic_pitch_idx,
        acoustic_config.num_windows,
        config.acoustic.post_filter,
        config.sample_rate,
        config.frame_period,
        config.acoustic.relative_f0)

    return duration_modified_labels, f0, mgc, bap, generated_waveform


def hts2wav(config: DictConfig, label_path: str = None, out_wav_path: str = None) -> None:
    """
    configファイルから各種設定を取得し、labファイルをもとにWAVファイルを生成する。

    もとの my_app との相違点:
        - ビット深度指定をできるようにした。
        - utt_list を使わず単一ファイルのみにした。
        - 単一ファイルのときの音量ノーマライズを無効にした。
    """
    logger = getLogger(config.verbose)
    logger.info(OmegaConf.to_yaml(config))

    # GPUのCUDAが使えるかどうかを判定
    device = 'cuda' if torch.cuda.is_available() else 'cpu'

    # 使用モデルの学習済みファイルのパスを設定する。
    maybe_set_checkpoints_(config)
    maybe_set_normalization_stats_(config)

    # モデルに関するファイルを読み取る。
    model_root = config.model_dir
    # timelag
    timelag_config = OmegaConf.load(join(model_root, "timelag", "model.yaml"))
    timelag_model = hydra.utils.instantiate(timelag_config.netG).to(device)
    checkpoint = torch.load(config.timelag.checkpoint,
                            map_location=lambda storage,
                            loc: storage)
    timelag_model.load_state_dict(checkpoint['state_dict'])
    timelag_in_scaler = joblib.load(config.timelag.in_scaler_path)
    timelag_out_scaler = joblib.load(config.timelag.out_scaler_path)
    timelag_model.eval()

    # duration
    duration_config = OmegaConf.load(join(model_root, "duration", "model.yaml"))
    duration_model = hydra.utils.instantiate(duration_config.netG).to(device)
    checkpoint = torch.load(config.duration.checkpoint,
                            map_location=lambda storage,
                            loc: storage)
    duration_model.load_state_dict(checkpoint['state_dict'])
    duration_in_scaler = joblib.load(config.duration.in_scaler_path)
    duration_out_scaler = joblib.load(config.duration.out_scaler_path)
    duration_model.eval()

    # acoustic model
    acoustic_config = OmegaConf.load(join(model_root, "acoustic", "model.yaml"))
    acoustic_model = hydra.utils.instantiate(acoustic_config.netG).to(device)
    checkpoint = torch.load(config.acoustic.checkpoint,
                            map_location=lambda storage,
                            loc: storage)
    acoustic_model.load_state_dict(checkpoint['state_dict'])
    acoustic_in_scaler = joblib.load(config.acoustic.in_scaler_path)
    acoustic_out_scaler = joblib.load(config.acoustic.out_scaler_path)
    acoustic_model.eval()

    # 設定を表示
    # print(OmegaConf.to_yaml(config))
    # synthesize wav file from lab file.
    # 入力するラベルファイルを指定。
    if label_path is None:
        assert config.label_path is not None
        label_path = config.label_path
    else:
        pass
    logger.info('Process the label file: %s', label_path)

    # 出力するwavファイルの設定。
    if out_wav_path is None:
        out_wav_path = config.out_wav_path

    # パラメータ推定
    logger.info('Synthesize the wav file: %s', out_wav_path)
    duration_modified_labels, f0, sp, bap, wav = synthesis(
        config, device, label_path,
        timelag_model, timelag_config, timelag_in_scaler, timelag_out_scaler,
        duration_model, duration_config, duration_in_scaler, duration_out_scaler,
        acoustic_model, acoustic_config, acoustic_in_scaler, acoustic_out_scaler)

    # 中間ファイル出力
    with open(out_wav_path.replace('.wav', '_timing.lab'), 'wt') as f_lab:
        lines = str(duration_modified_labels).splitlines()
        s = ''
        for line in lines:
            t_start, t_end, context = line.split()
            context = context[context.find('-') + 1: context.find('+')]
            s += f'{t_start} {t_end} {context}\n'
        f_lab.write(s)
    with open(out_wav_path.replace('.wav', '.f0'), 'wb') as f_f0:
        f0.astype(np.float64).tofile(f_f0)
    with open(out_wav_path.replace('.wav', '.mgc'), 'wb') as f_mgc:
        sp.astype(np.float64).tofile(f_mgc)
    with open(out_wav_path.replace('.wav', '.bap'), 'wb') as f_bap:
        bap.astype(np.float64).tofile(f_bap)
    # サンプルレートとビット深度を指定してWAVファイル出力
    generate_wav_file(config, wav, out_wav_path)

    logger.info('Synthesized the wav file: %s', out_wav_path)


def main():
    """
    手動起動したとき
    """
    # コマンドライン引数に必要な情報があるかチェック
    if len(argv) >= 4:
        voicebank_config_yaml_path = argv[1].strip('"')
        label_path = argv[2].strip('"')
        out_wav_path = argv[3].strip('"')
    # コマンドライン引数が不足していれば標準入力で受ける
    else:
        voicebank_config_yaml_path = \
            input("Please input voicebank's config file path\n>>> ").strip('"')
        label_path = \
            input('Please input label file path\n>>> ').strip('"')
        out_wav_path = f'{splitext(label_path)[0]}.wav'

    # configファイルのパスを分割する
    config_path, config_name = split(voicebank_config_yaml_path)

    # configファイルを読み取る
    initialize(config_path=relpath(config_path))
    config = compose(config_name=config_name, overrides=[f'+config_path={config_path}'])

    # WAVファイル生成
    str_now = datetime.now().strftime('%Y%m%d%h%M%S')
    out_wav_path = out_wav_path.replace('.wav', f'__{str_now}.wav')
    hts2wav(config, label_path, out_wav_path)


if __name__ == '__main__':
    main()
