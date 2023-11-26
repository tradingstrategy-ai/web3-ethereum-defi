#!/usr/bin/env bash
#
# Taken from https://raw.githubusercontent.com/SCBuergel/SEQS/main/install-scripts/python_appVM.sh
#

set -e
set -u

echo "I am $(whoami)"

echo "Installing Python on appVM"
curl https://pyenv.run | bash

echo "setting .profile..."
echo -e "\
export PYENV_ROOT=\"\$HOME/.pyenv\"\n\
command -v pyenv >/dev/null || export PATH=\"\$PYENV_ROOT/bin:\$PATH\"\n\
eval \"\$(pyenv init -)\"" >> ~/.profile

echo "reloading .profile twice..."
source ~/.profile
source ~/.profile

echo "setting .bashrc..."
echo "eval \"\$(/root/.pyenv/bin/pyenv virtualenv-init -)\"" >> ~/.bashrc

echo "installing latest python..."
pyenv install 3.12

echo "setting symlink..."
ln -f -s /usr/bin/python3 /usr/local/bin/python

echo "setting global python version..."
pyenv global 3.12

echo "installing virtualenv..."
pip install virtualenv

echo "updating pip..."
pip install --upgrade pip

echo "Pip is $(which pip)"
echo "Python is $(which python)"
pip --version
pip install safe-pysha3
pip install -e web3-ethereum-defi

# Set up poetry
curl -sSL https://install.python-poetry.org | python3 -