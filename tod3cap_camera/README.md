# TOD3Cap: Camera-based implementations

## Getting Started

a. Create a conda virtual environment and activate it.
```bash
cd tod3cap_camera
conda create -n todc python=3.8 -y 
conda activate todc
```
b. Install PyTorch and torchvision following the official instructions.
```bash
pip install torch==1.9.1+cu111 torchvision==0.10.1+cu111 torchaudio==0.9.1 -f https://download.pytorch.org/whl/torch_stable.html

# Recommended torch>=1.9
```

c. Install gcc>=5 in conda env (optional).
```bash
conda install -c omgarcia gcc-6 # gcc-6.2
```

d. Install mmcv-full.
```bash
pip install mmcv-full==1.4.0
#  pip install mmcv-full==1.4.0 -f https://download.openmmlab.com/mmcv/dist/cu111/torch1.9.0/index.html
```

e. Install mmdet and mmseg.
```bash
pip install mmdet==2.14.0
pip install mmsegmentation==0.14.1
```

f. Install mmdet3d from source code.
```bash
cd mmdetection3d
python setup.py install
```

g. Install Detectron2 and Timm.
```bash
pip install einops fvcore seaborn iopath==0.1.9 timm  typing-extensions==4.5.0 pylint ipython==8.12  numpy==1.19.5 matplotlib==3.5.2 numba==0.48.0 pandas==1.4.4 scikit-image==0.19.3 setuptools==60.5.0
python -m pip install 'git+https://github.com/facebookresearch/detectron2.git'
```

h. Install other dependencies.
```bash
pip install -r requirements.txt
```

i. Install the evaluation utils
```bash
git clone https://gitclone.com/github.com/Maluuba/nlg-eval.git
cd nlg-eval
pip install -r requirements.txt
python setup.py install
nlg-eval --setup
```