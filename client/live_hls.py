import subprocess
import shlex

cmd = """
rpicam-vid
  --width 2304 --height 1296 --framerate 30
  --codec h264 --inline
  --nopreview --timeout 0
  --bitrate 4000000
  --libav-format hls
  --output /srv/hls/printer1/index.m3u8
"""

subprocess.run(shlex.split(cmd), check=True)
