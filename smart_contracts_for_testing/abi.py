

import os
import os.path
import json


def get_abi_by_filename(fname: str) -> dict:
    """Reads a embedded ABI file and returns it """
    here = os.path.dirname(__file__)
    abi_path = os.path.join(here, fname)
    abi = json.load(open(abi_path, "rt"))
    return abi["abi"]
