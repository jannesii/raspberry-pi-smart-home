#!/usr/bin/env python3
import os
import logging
import subprocess

logger = logging.getLogger(__name__)


class VideoEncoder:
    """Wraps ffmpeg for H.264 transcoding."""

    def __init__(self, ffmpeg_path: str = "/usr/bin/ffmpeg") -> None:
        """
        Initialize with a logger and optional ffmpeg binary path.
        """
        self.ffmpeg_path = ffmpeg_path

    def encode(self, input_file: str) -> bool:
        """
        Transcode the given MP4 file to H.264 using ffmpeg.
        Returns True on success, False on failure.
        """
        if not os.path.exists(self.ffmpeg_path):
            logger.error("ffmpeg not found at %s", self.ffmpeg_path)
            return False

        output = input_file.replace("_timelapse.mp4", "_timelapse_h264.mp4")
        cmd = [
            self.ffmpeg_path,
            "-i",
            input_file,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "23",
            output,
        ]
        try:
            subprocess.run(
                cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            os.remove(input_file)
            logger.info("created %s", output)
            return True
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode(errors="ignore")
            logger.error("ffmpeg error: %s", err)
            return False
        except Exception:
            logger.exception("unexpected error")
            return False
