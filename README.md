# EAST detector socket: a wrapper for tensorflow east detecting python

### Introduction

This is a socket wrapper of <https://github.com/argman/EAST>.  
Download required files from above repository, and then place it at the root of this project.  
Big thanks to [Insung Park][https://github.com/insung3511/] for giving me advices about openCV.

### Installation

```bash
pip install -r requirements.txt
```

to install required modules. and then,

```bash
python main.py
```

to start a socket server.  
the return packet would be like:

```json
{
  "return": ["results array here!"]
}
```

for more information, please check out the original project.
