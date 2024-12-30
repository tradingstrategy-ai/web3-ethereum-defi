"""Compatibity layer.

- Deal with different web3.py versions
"""
import functools
import itertools
from typing import Dict, Any, Optional, Sequence, Tuple, List, Union, cast, Type

from eth_abi.codec import ABICodec
from eth_typing import HexStr
from eth_utils import encode_hex, function_abi_to_4byte_selector, is_text

from eth_utils.abi import collapse_if_tuple
from eth_utils.toolz import (
    curry,
    partial,
    pipe,
)
from web3._utils.abi import get_aligned_abi_inputs, filter_by_name, get_fallback_func_abi, filter_by_argument_count, get_receive_func_abi, filter_by_encodability
from web3._utils.abi_element_identifiers import ReceiveFn
from web3._utils.contracts import find_matching_fn_abi, extract_argument_types
from web3._utils.function_identifiers import FallbackFn
from web3.exceptions import Web3ValidationError, FallbackNotFound
from web3.types import ABI, ABIFunction, ABIEvent


def _abi_to_signature(abi: Dict[str, Any]) -> str:
    function_signature = "{fn_name}({fn_input_types})".format(
        fn_name=abi["name"],
        fn_input_types=",".join(
            [collapse_if_tuple(abi_input) for abi_input in abi.get("inputs", [])]
        ),
    )
    return function_signature


abi_to_signature = _abi_to_signature


def get_function_info(
    fn_name: str,
    abi_codec: ABICodec,
    contract_abi: Optional[ABI] = None,
    fn_abi: Optional[ABIFunction] = None,
    args: Optional[Sequence[Any]] = None,
    kwargs: Optional[Any] = None,
) -> Tuple[ABIFunction, HexStr, Tuple[Any, ...]]:
    if args is None:
        args = tuple()
    if kwargs is None:
        kwargs = {}

    if fn_abi is None:
        fn_abi = find_matching_fn_abi(contract_abi, abi_codec, fn_name, args, kwargs)

    # typed dict cannot be used w/ a normal Dict
    # https://github.com/python/mypy/issues/4976
    fn_selector = encode_hex(function_abi_to_4byte_selector(fn_abi))  # type: ignore

    fn_arguments = merge_args_and_kwargs(fn_abi, args, kwargs)

    _, aligned_fn_arguments = get_aligned_abi_inputs(fn_abi, fn_arguments)

    return fn_abi, fn_selector, aligned_fn_arguments


def merge_args_and_kwargs(
    function_abi: ABIFunction, args: Sequence[Any], kwargs: Dict[str, Any]
) -> Tuple[Any, ...]:
    """
    Takes a list of positional args (``args``) and a dict of keyword args
    (``kwargs``) defining values to be passed to a call to the contract function
    described by ``function_abi``.  Checks to ensure that the correct number of
    args were given, no duplicate args were given, and no unknown args were
    given.  Returns a list of argument values aligned to the order of inputs
    defined in ``function_abi``.
    """
    # Ensure the function is being applied to the correct number of args
    if len(args) + len(kwargs) != len(function_abi.get("inputs", [])):
        raise TypeError(
            f"Incorrect argument count. Expected '{len(function_abi['inputs'])}'"
            f". Got '{len(args) + len(kwargs)}'"
        )

    # If no keyword args were given, we don't need to align them
    if not kwargs:
        return cast(Tuple[Any, ...], args)

    kwarg_names = set(kwargs.keys())
    sorted_arg_names = tuple(arg_abi["name"] for arg_abi in function_abi["inputs"])
    args_as_kwargs = dict(zip(sorted_arg_names, args))

    # Check for duplicate args
    duplicate_args = kwarg_names.intersection(args_as_kwargs.keys())
    if duplicate_args:
        raise TypeError(
            f"{function_abi.get('name')}() got multiple values for argument(s) "
            f"'{', '.join(duplicate_args)}'"
        )

    # Check for unknown args
    unknown_args = kwarg_names.difference(sorted_arg_names)
    if unknown_args:
        if function_abi.get("name"):
            raise TypeError(
                f"{function_abi.get('name')}() got unexpected keyword argument(s)"
                f" '{', '.join(unknown_args)}'"
            )
        raise TypeError(
            f"Type: '{function_abi.get('type')}' got unexpected keyword argument(s)"
            f" '{', '.join(unknown_args)}'"
        )

    # Sort args according to their position in the ABI and unzip them from their
    # names
    sorted_args = tuple(
        zip(
            *sorted(
                itertools.chain(kwargs.items(), args_as_kwargs.items()),
                key=lambda kv: sorted_arg_names.index(kv[0]),
            )
        )
    )

    if sorted_args:
        return sorted_args[1]
    else:
        return tuple()


def find_matching_fn_abi(
    abi: ABI,
    abi_codec: ABICodec,
    fn_identifier: Optional[Union[str, Type[FallbackFn], Type[ReceiveFn]]] = None,
    args: Optional[Sequence[Any]] = None,
    kwargs: Optional[Any] = None,
) -> ABIFunction:
    args = args or tuple()
    kwargs = kwargs or dict()
    num_arguments = len(args) + len(kwargs)

    if fn_identifier is FallbackFn:
        return get_fallback_func_abi(abi)

    if fn_identifier is ReceiveFn:
        return get_receive_func_abi(abi)

    if not is_text(fn_identifier):
        raise TypeError("Unsupported function identifier")

    name_filter = functools.partial(filter_by_name, fn_identifier)
    arg_count_filter = functools.partial(filter_by_argument_count, num_arguments)
    encoding_filter = functools.partial(filter_by_encodability, abi_codec, args, kwargs)

    function_candidates = pipe(abi, name_filter, arg_count_filter, encoding_filter)

    if len(function_candidates) == 1:
        return function_candidates[0]
    else:
        matching_identifiers = name_filter(abi)
        matching_function_signatures = [
            abi_to_signature(func) for func in matching_identifiers
        ]

        arg_count_matches = len(arg_count_filter(matching_identifiers))
        encoding_matches = len(encoding_filter(matching_identifiers))

        if arg_count_matches == 0:
            diagnosis = (
                "\nFunction invocation failed due to improper number of arguments."
            )
        elif encoding_matches == 0:
            diagnosis = (
                "\nFunction invocation failed due to no matching argument types."
            )
        elif encoding_matches > 1:
            diagnosis = (
                "\nAmbiguous argument encoding. "
                "Provided arguments can be encoded to multiple functions "
                "matching this call."
            )

        collapsed_args = extract_argument_types(args)
        collapsed_kwargs = dict(
            {(k, extract_argument_types([v])) for k, v in kwargs.items()}
        )
        message = (
            f"\nCould not identify the intended function with name `{fn_identifier}`, "
            f"positional arguments with type(s) `{collapsed_args}` and "
            f"keyword arguments with type(s) `{collapsed_kwargs}`."
            f"\nFound {len(matching_identifiers)} function(s) with "
            f"the name `{fn_identifier}`: {matching_function_signatures}{diagnosis}"
        )

        raise Web3ValidationError(message)



def filter_by_type(_type: str, contract_abi: ABI) -> List[Union[ABIFunction, ABIEvent]]:
    return [abi for abi in contract_abi if abi["type"] == _type]


def filter_by_name(name: str, contract_abi: ABI) -> List[Union[ABIFunction, ABIEvent]]:
    return [
        abi
        for abi in contract_abi
        if (
            abi["type"] not in ("fallback", "constructor", "receive")
            and abi["name"] == name
        )
    ]



def get_receive_func_abi(contract_abi: ABI) -> ABIFunction:
    receive_abis = filter_by_type("receive", contract_abi)
    if receive_abis:
        return cast(ABIFunction, receive_abis[0])
    else:
        raise FallbackNotFound("No receive function was found in the contract ABI.")