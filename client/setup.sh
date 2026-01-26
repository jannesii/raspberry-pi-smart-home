sudo apt update -y
sudo apt install -y libcap-dev libatlas-base-dev ffmpeg libopenjp2-7
sudo apt install -y libcamera-dev
sudo apt install -y libkms++-dev libfmt-dev libdrm-dev
sudo apt install -y python3-pip python3-dev build-essential libgpiod2
sudo apt install -y sshfs rsync rpicam-apps ffmpeg

python -m venv --system-site-packages .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

#git clone https://github.com/adafruit/Adafruit_Python_DHT.git temp‑dht
#cd temp‑dht
#python setup.py install --force-pi
#cd ..
#sudo rm -rf temp‑dht
