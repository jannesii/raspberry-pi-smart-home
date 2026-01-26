import os
import cv2
from datetime import datetime


def create_video_from_images(image_folder, output_folder, fps=25):
    # Gather image file names (you can adjust the extensions if needed)
    image_files = [
        os.path.join(image_folder, f)
        for f in os.listdir(image_folder)
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    ]

    if not image_files:
        print("No images found in the folder.")
        return

    # Sort files by name (assumes names contain sortable timestamps)
    image_files.sort()

    # Read the first image to determine frame size.
    first_frame = cv2.imread(image_files[0])
    if first_frame is None:
        print(f"Error reading the first image: {image_files[0]}")
        return

    height, width, _ = first_frame.shape

    # Prepare output file name.
    video_filename = os.path.join(
        output_folder, f"timelapse_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
    )

    # Define video codec and create VideoWriter object. The H264 codec in an MP4 container is good for iPhone.
    fourcc = cv2.VideoWriter_fourcc(*"avc3")
    video_writer = cv2.VideoWriter(video_filename, fourcc, fps, (width, height))

    for fname in image_files:
        frame = cv2.imread(fname)
        if frame is None:
            print(f"Warning: Could not read {fname}, skipping.")
            continue
        video_writer.write(frame)

    video_writer.release()
    print(f"Video created successfully: {video_filename}")


if __name__ == "__main__":
    # Folder containing the images and output folder for the video
    images_folder = "testPhotos"
    output_folder = "testTimelapses"

    # Create output folder if it doesn't exist
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    create_video_from_images(images_folder, output_folder)
