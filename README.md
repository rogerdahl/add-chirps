## Add Bluetooth earbud keepalive chirps to audio files

If you have tried to listen to audiobooks with Bluetooth earbuds, you may have found that brief pauses in the narration causes the first couple of syllables in the sentence that follows to be muted.

This is because the earbuds detect the silence and go to sleep in order to increase battery life. Many earbuds go to sleep after only one or two seconds of silence, causing frequent dropouts in audiobooks.

This program works around the issue by adding sound to the silent periods, which prevents the earbuds from going to sleep. The sound is short bursts of white noise which I call, "chirps."

The program does not alter the original audio file, but creates new MP3 files that contain the chirps.

Ideally, the chirps should only occur within periods of silence where the earbuds would normally go to sleep, with volume as low and duration as short as possible while still being detected by the earbuds. The default settings will cause a soft, brief chirp after every 2 seconds of silence that works for my earbuds.

### Usage

```
$ ./add-chirps.py my_audiobook.mp3
```

To speed things up, multiple sections from the single input file are processed in parallel and each section is written to a separate output MP3 file.

To create a single output file, use `--file-duration 0'.`

The new MP3 files are created in the same directory as the input file and have filenames ending with `.chirp.mp3`.

#### Options

```bash
usage: add-chirps.py [-h] [--file-duration sec] [--silence-volume 0.0-1.0]
                     [--silence-duration sec] [--chirp-volume 0.0-1.0]
                     [--chirp-duration sec] [--update-freq sec]
                     [--chunk-duration sec] [--workers N] [--max-length sec]
                     [--quality N] [--bit-rate N] [--debug]
                     audio_in_path

positional arguments:
  audio_in_path

optional arguments:
  -h, --help            show this help message and exit
  --file-duration sec   The generated MP3 is split into files to allow the
                        files to the processed in parallel. 0 = force a single
                        output file (slow) (default: 1800)
  --silence-volume 0.0-1.0
  --silence-duration sec
                        Duration of silence after which the earbuds go to
                        sleep (default: 1.5)
  --chirp-volume 0.0-1.0
                        Volume of chirp (default: 0.01)
  --chirp-duration sec  Duration of chirp (default: 0.01)
  --update-freq sec     Interval between each progress update (default: 1.0)
  --chunk-duration sec  Length of audio to buffer in memory before write to
                        disk (default: 10)
  --workers N           Number of worker processes in multiprocessing pool
                        (default: 16)
  --max-length sec      Max duration of audio to process (handy for testing)
                        (default: None)
  --quality N           MP3 compression quality 2-7 (2 = highest and slowest,
                        7 = lowest and fastest) (default: 2)
  --bit-rate N          MP3 compression bitrate (higher = better quality)
                        (default: 64)
  --debug               Debug level logging (default: False)
```

#### Example output

```bash
Creating MP3 files with chirps...

Input:
  Path:                test_audio.mp3
  Duration:            17h 26m 29s sec
  Channels:            2
  Sample rate:         22,050 Hz
  Total sample count:  1,384,497,450
Output:
  Files:               35
  Samples per file:    39,690,000

test_audio.000.chirp.mp3: 0h 07m 50s   26.11%  [ ################                      ]
test_audio.001.chirp.mp3: 0h 06m 10s   20.56%  [ #############                         ]
test_audio.002.chirp.mp3: 0h 05m 10s   17.22%  [ ###########                           ]
test_audio.003.chirp.mp3: 0h 03m 00s   10.00%  [ ######                                ]
test_audio.004.chirp.mp3: 0h 01m 20s    4.44%  [ ###                                   ]
```

### Install

```bash
$ pip install audioread blessed lameenc
$ git clone <this repository address>
$ cd add-chirps
```
