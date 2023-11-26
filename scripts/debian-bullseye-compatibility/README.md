Check installations issues on Debian Bullseye

- Debian Bullseye
- Python 3.11
- pyenv
- pysha3 installation issue
- See [safe-pysha3 replacing pysha3](https://github.com/5afe/pysha3)


## Running

Create image:

```shell
docker build --no-cache -t pysha3-test .
```

Run the shell script within the image:


```shell
docker run -v `pwd`:`pwd` -w `pwd` --entrypoint `pwd`/check-install.sh pysha3-test  
```

## Manual inspection of running Debian Bullseye

Map source tree as we so we can do direct install from local source for trials.

```shell
docker run -it -v `pwd`:`pwd` -v $(realpath $PWD/../..):`pwd`/web3-ethereum-defi -w `pwd` --entrypoint /bin/bash pysha3-test
```

Then run the script:

```shell
./check-install.sh
```

Or to active Python environment:

```shell
/root/.pyenv/bin/pyenv global 3.12

```

## pysha3 error

```
Using cached netaddr-0.9.0-py3-none-any.whl (2.2 MB)
Building wheels for collected packages: pysha3
  Building wheel for pysha3 (pyproject.toml) ... error
  error: subprocess-exited-with-error
  
  × Building wheel for pysha3 (pyproject.toml) did not run successfully.
  │ exit code: 1
  ╰─> [18 lines of output]
      running bdist_wheel
      running build
      running build_py
      creating build
      creating build/lib.linux-x86_64-cpython-311
      copying sha3.py -> build/lib.linux-x86_64-cpython-311
      running build_ext
      building '_pysha3' extension
      creating build/temp.linux-x86_64-cpython-311
      creating build/temp.linux-x86_64-cpython-311/Modules
      creating build/temp.linux-x86_64-cpython-311/Modules/_sha3
      gcc -pthread -Wsign-compare -DNDEBUG -g -fwrapv -O3 -Wall -fPIC -DPY_WITH_KECCAK=1 -I/home/user/.pyenv/versions/3.11.4/include/python3.11 -c Modules/_sha3/sha3module.c -o build/temp.linux-x86_64-cpython-311/Modules/_sha3/sha3module.o
      In file included from Modules/_sha3/sha3module.c:20:
      Modules/_sha3/backport.inc:78:10: fatal error: pystrhex.h: No such file or directory
         78 | #include "pystrhex.h"
            |          ^~~~~~~~~~~~
      compilation terminated.
      error: command '/usr/bin/gcc' failed with exit code 1
      [end of output]
  
  note: This error originates from a subprocess, and is likely not a problem with pip.
  ERROR: Failed building wheel for pysha3
Failed to build pysha3
ERROR: Could not build wheels for pysha3, which is required to install pyproject.toml-based projects

```