#!/usr/bin/env python

"""Add Bluetooth earbud keepalive chirps to audio files

Note:
    Had a very strange problem where the terminal would not display typed text after
    exiting the program and using `blessed`. This happened even when `stdout` and
    `stderr` were directed to a file. This issue seems resolved after moving from
    `audiofile` to `audioread` for reading MP3 files. `audiofile` calls out to the
    command line version of ffmpeg to decode MP3. My current theory is that ffmpeg does
    something with `ioctl`s that affects the terminal, which then interferes with the
    ANSI codes that `blessed` sends to the terminal.
"""
import argparse
import collections
import itertools
import logging
import multiprocessing
import multiprocessing.queues
import pathlib
import queue
import random
import struct
import sys
import time

import audioread
import blessed
import lameenc

DEFAULT_SILENCE_THRESHOLD_VOLUME = 0.3
DEFAULT_SILENCE_DURATION_SEC = 1.5

DEFAULT_CHIRP_VOLUME = 0.01
DEFAULT_CHIRP_DURATION_SEC = 0.01

DEFAULT_CHUNK_DURATION_SEC = 10

# 2 = best compression, 7 = fastest
DEFAULT_MP3_COMPRESSION_QUALITY = 2
DEFAULT_MP3_BIT_RATE = 64

DEFAULT_FILE_DURATION_SEC = 30 * 60
DEFAULT_WORKER_COUNT = 16

log = logging.getLogger("")
log.setLevel(logging.DEBUG)


AudioParams = collections.namedtuple(
    "AudioParams", ("channel_count", "sample_rate_hz", "duration_sec")
)


def main():
    term = blessed.Terminal()
    print(term.clear)
    start_ts = time.time()
    try:
        command_line(term)
    except Exception:
        log.exception("Exception")
        return 1
    finally:
        print(f"Total processing time: {format_sec_to_hms(time.time() - start_ts)}")
    return 0


def command_line(term):
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("audio_in_path")

    parser.add_argument(
        "--file-duration",
        dest="file_duration_sec",
        type=float,
        metavar="sec",
        default=DEFAULT_FILE_DURATION_SEC,
        help=(
            "The generated MP3 is split into files to allow the files to the "
            "processed in parallel. 0 = force a single output file (slow)"
        ),
    )

    parser.add_argument(
        "--silence-volume",
        dest="silence_threshold_volume",
        type=float,
        metavar="0.0-1.0",
        default=DEFAULT_SILENCE_THRESHOLD_VOLUME,
    )
    parser.add_argument(
        "--silence-duration",
        dest="silence_duration_sec",
        type=float,
        metavar="sec",
        default=DEFAULT_SILENCE_DURATION_SEC,
        help="Duration of silence after which the earbuds go to sleep",
    )
    parser.add_argument(
        "--chirp-volume",
        dest="chirp_volume",
        type=float,
        metavar="0.0-1.0",
        default=DEFAULT_CHIRP_VOLUME,
        help="Volume of chirp",
    )
    parser.add_argument(
        "--chirp-duration",
        dest="chirp_duration_sec",
        type=float,
        metavar="sec",
        default=DEFAULT_CHIRP_DURATION_SEC,
        help="Duration of chirp",
    )
    parser.add_argument(
        "--update-freq",
        dest="update_frequency_sec",
        type=float,
        metavar="sec",
        default=1.0,
        help="Interval between each progress update",
    )
    parser.add_argument(
        "--chunk-duration",
        dest="chunk_duration_sec",
        type=float,
        metavar="sec",
        default=DEFAULT_CHUNK_DURATION_SEC,
        help="Length of audio to buffer in memory before write to disk",
    )
    parser.add_argument(
        "--workers",
        dest="worker_count",
        type=int,
        metavar="N",
        default=DEFAULT_WORKER_COUNT,
        help="Number of worker processes in multiprocessing pool",
    )
    parser.add_argument(
        "--max-length",
        dest="max_encode_duration_sec",
        type=float,
        metavar="sec",
        default=None,
        help="Max duration of audio to process (handy for testing)",
    )
    parser.add_argument(
        "--quality",
        dest="mp3_compression_quality",
        choices=(2, 3, 4, 5, 6, 7),
        type=int,
        metavar="N",
        default=DEFAULT_MP3_COMPRESSION_QUALITY,
        help="MP3 compression quality 2-7 (2 = highest and slowest, 7 = lowest and fastest)",
    )
    parser.add_argument(
        "--bit-rate",
        dest="mp3_compression_bit_rate",
        choices=(32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320,),
        type=int,
        metavar="N",
        default=DEFAULT_MP3_BIT_RATE,
        help="MP3 compression bitrate (higher = better quality)",
    )

    parser.add_argument("--debug", action="store_true", help="Debug level logging")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO, format="%(message)s"
    )

    try:
        process(args, term)
    except Exception:
        log.exception("Exception")
        return 1

    return 0


def process(args, term):
    audio_params = get_audio_params(args.audio_in_path)
    # Number of samples in a file
    file_sample_count = get_file_sample_count(args, audio_params)
    # Total number of samples in source
    total_sample_count = int(audio_params.duration_sec * audio_params.sample_rate_hz)
    # Number of files
    file_count, remainder = divmod(total_sample_count, file_sample_count)
    if remainder:
        file_count += 1

    progress_start_line = log_audio_params(
        args, term, audio_params, file_count, file_sample_count, total_sample_count,
    )

    result_list = []

    pool = multiprocessing.Pool(processes=args.worker_count)
    manager = multiprocessing.Manager()
    progress_queue = manager.Queue()

    for file_idx in range(file_count):
        result_obj = pool.apply_async(
            process_file,
            (args, audio_params, file_idx, file_sample_count, progress_queue),
        )
        result_list.append(result_obj)

    while True:
        not_ready_list = []

        for result_obj in result_list:
            if result_obj.ready():
                # Raise any exceptions that occurred in the workers
                result_obj.get()
            else:
                not_ready_list.append(result_obj)

        if not not_ready_list:
            break

        result_list = not_ready_list

        try:
            progress_tup = progress_queue.get_nowait()
        except queue.Empty:
            pass
        else:
            (
                file_idx,
                audio_out_file_path,
                completed_fraction,
                completed_sec,
            ) = progress_tup

            log_progress_update(
                term,
                file_idx,
                audio_out_file_path,
                completed_fraction,
                completed_sec,
                progress_start_line,
            )


def get_file_sample_count(args, audio_params):
    if args.file_duration_sec:
        return int(args.file_duration_sec * audio_params.sample_rate_hz)
    else:
        return int(audio_params.duration_sec * audio_params.sample_rate_hz)


def process_file(args, audio_params, file_idx, file_sample_count, progress_queue):
    audio_out_file_path = pathlib.Path(args.audio_in_path).with_suffix(
        f".{file_idx:03d}.chirp.mp3"
    )

    mp3_encoder = create_mp3_encoder(args, audio_params)

    single_sample_sec = 1.0 / audio_params.sample_rate_hz
    silence_sec = 0
    completed_sec = 0
    last_update_ts = 0
    chunk_sec = 0

    sample_list = []

    with audio_out_file_path.open("wb") as mp3_file:
        for sample_word in read_samples(
            args, audio_params, file_idx, file_sample_count
        ):
            sample_float = sample_word / 32767
            completed_sec += single_sample_sec
            chunk_sec += single_sample_sec

            if time.time() > last_update_ts + args.update_frequency_sec:
                last_update_ts = time.time()
                queue_progress_update(
                    args, progress_queue, file_idx, audio_out_file_path, completed_sec,
                )

            if abs(sample_float) < args.silence_threshold_volume:
                silence_sec += single_sample_sec
            else:
                silence_sec = 0

            if silence_sec > args.silence_duration_sec + args.chirp_duration_sec:
                silence_sec = 0

            if silence_sec > args.silence_duration_sec:
                sample_float = (random.random() * 2.0) - 1
                sample_float *= args.chirp_volume

            sample_word = int(sample_float * 32767)
            sample_list.append(sample_word)

            if chunk_sec > args.chunk_duration_sec:
                encode_and_write_mp3_chunk(mp3_encoder, mp3_file, sample_list)
                sample_list.clear()
                chunk_sec = 0

        # Flush remaining MP3 bytes out to file.
        encode_and_write_mp3_chunk(mp3_encoder, mp3_file, sample_list)
        mp3_bytes = mp3_encoder.flush()
        mp3_file.write(mp3_bytes)

        # Final 100% update.
        queue_progress_update(
            args, progress_queue, file_idx, audio_out_file_path, completed_sec
        )


def create_verbose_tag(args):
    return (
        f"silence-vol={args.silence_threshold_volume},"
        f"silence-dur={args.silence_duration_sec},"
        f"chirp-vol={args.chirp_volume},"
        f"chirp-dur={args.chirp_duration_sec},"
        f"enc-dur={args.encoding_duration_sec}"
    )


def create_mp3_encoder(args, audio_params):
    mp3_encoder = lameenc.Encoder()
    mp3_encoder.set_bit_rate(args.mp3_compression_bit_rate)
    mp3_encoder.set_in_sample_rate(audio_params.sample_rate_hz)
    mp3_encoder.set_channels(1)
    mp3_encoder.set_quality(args.mp3_compression_quality)
    return mp3_encoder


def encode_and_write_mp3_chunk(mp3_encoder, mp3_file, sample_list):
    log.debug(f"MP3 encoding and writing chunk: {len(sample_list)} samples")
    sample_buf = bytearray(len(sample_list) * 2)
    fmt_str = f"{len(sample_list)}h"
    struct.pack_into(fmt_str, sample_buf, 0, *sample_list)
    mp3_bytes = mp3_encoder.encode(bytes(sample_buf))
    mp3_file.write(mp3_bytes)


def get_audio_params(audio_in_path):
    with audioread.audio_open(audio_in_path) as f:
        return AudioParams(f.channels, f.samplerate, f.duration)


def read_samples(args, audio_params, file_idx, file_sample_count):
    """Read samples from audio file and mix stereo to mono.

    - MP3 has 1152 samples per audio channel per block.
    - For MP3, read_blocks() returns 4608 bytes
    - = 2304 16-bit words
    - = two channels with 1152 samples each
    """
    if audio_params.channel_count not in (1, 2):
        raise WakeupChirpsError(
            f"Input audio file must have one or two channels (mono or stereo). "
            f"This file has {audio_params.channel_count} channels"
        )
    fmt_str = "h" * audio_params.channel_count
    offset_sample_count = file_idx * file_sample_count
    with audioread.audio_open(args.audio_in_path) as f:
        sample_idx = 0
        for block in f.read_blocks():
            for sample_list in struct.iter_unpack(fmt_str, block):
                if sample_idx >= offset_sample_count:
                    if sample_idx >= offset_sample_count + file_sample_count:
                        break
                    mono_sample = sum(sample_list) / len(sample_list)
                    yield mono_sample
                sample_idx += 1


def queue_progress_update(
    args, progress_queue, file_idx, audio_out_file_path, completed_sec
):
    completed_fraction = completed_sec / args.file_duration_sec
    progress_queue.put(
        (file_idx, audio_out_file_path, completed_fraction, completed_sec)
    )


def log_audio_params(
    args, term, audio_params, file_count, file_sample_count, total_sample_count
):
    total_hms_str = format_sec_to_hms(audio_params.duration_sec)
    file_hms_str = format_sec_to_hms(args.file_duration_sec)
    with term.location(0, 0):
        print(f"Creating MP3 files with chirps...\n")
        print(f"Input:")
        print(f"  Path:                {args.audio_in_path}")
        print(f"  Duration:            {total_hms_str}")
        print(f"  Channels:            {audio_params.channel_count}")
        print(f"  Sample rate:         {audio_params.sample_rate_hz:,} Hz")
        print(f"  Samples:             {total_sample_count:,}")
        print(f"")
        print(f"Output:")
        print(f"  Files:               {file_count:,}")
        print(f"  Duration:            {file_hms_str}")
        print(f"  Channels:            1")
        print(f"  Sample rate:         {audio_params.sample_rate_hz:,} Hz")
        print(f"  Samples per file:    {file_sample_count:,}")
        return term.get_location()[0] + 1


def log_progress_update(
    term,
    file_idx,
    audio_out_file_path,
    completed_fraction,
    completed_sec,
    progress_start_line,
):
    status_str = (
        f"{audio_out_file_path}: {format_sec_to_hms(completed_sec)} "
        f"{completed_fraction * 100.0: 7.2f}%"
    )
    start_str = " [ "
    end_str = " ]"
    total_bar_len = term.width - len(status_str) - len(start_str) - len(end_str) - 1
    completed_bar_len = round(total_bar_len * completed_fraction)
    remaining_bar_len = total_bar_len - completed_bar_len
    line_idx = progress_start_line + (file_idx % (term.height - progress_start_line))
    with term.location(0, line_idx):
        print(
            f"{status_str} {start_str}"
            f'{"#" * completed_bar_len}{" " * remaining_bar_len}{end_str}'
        )


def format_sec_to_hms(sec):
    rem_int, s_int = divmod(int(sec), 60)
    h_int, m_int, = divmod(rem_int, 60)
    return "{}h {:02d}m {:02d}s".format(h_int, m_int, s_int)


def create_test_permutations(args):
    # The lists are arranged from best to worst, which causes the permutations to also
    # be generated in best to worst order.
    silence_volume_list = [0.3]
    silence_duration_list = [2.0, 1.5, 1.0, 0.5]
    chirp_volume_list = [0.001, 0.005, 0.01, 0.05, 0.1]
    chirp_duration_list = [0.001, 0.005, 0.01, 0.05, 0.1]

    perm_list = list(
        itertools.product(
            *[
                args.audio_in_path,
                silence_volume_list,
                silence_duration_list,
                chirp_volume_list,
                chirp_duration_list,
                [2 * 60],
                [10],
                [1],
            ]
        )
    )

    # pprint.pprint(perm_list)

    pool = multiprocessing.Pool(processes=16)

    for perm_tup in perm_list:

        tag_str = create_verbose_tag(args)

        audio_out_file_path = pathlib.Path(args.audio_in_path).with_suffix(
            f".{tag_str}.chirp.mp3"
        )

        try:
            pool.apply_async(process_file, perm_tup)
        except Exception as e:
            # raise e
            log.exception(e)
            return 1

    pool.close()
    pool.join()


class WakeupChirpsError(Exception):
    pass


if __name__ == "__main__":
    sys.exit(main())
