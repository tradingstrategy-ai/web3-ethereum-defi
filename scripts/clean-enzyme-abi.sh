#!/bin/bash
#
# Clean Enzyme from AST data, because otherwise our package upload to PyPi would be too large.
#
# Called from Makefile.
#
# Had to move this to shell scripts, as Makefile one liner stopped working, maybe a shell incompatibility issue.
#
for abi_file in eth_defi/abi/enzyme/*.json ; do cat <<< $(jq 'del(.ast)' $abi_file) > $abi_file ; done