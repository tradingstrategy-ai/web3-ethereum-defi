"""GMX event data.

What's little madness between friends.
"""

from dataclasses import dataclass
from typing import List


@dataclass(slots=True, frozen=True)
class AddressKeyValue:
    key: str
    value: str  # Representing Solidity address as str

@dataclass(slots=True, frozen=True)
class AddressArrayKeyValue:
    key: str
    value: List[str]  # List of addresses as str

@dataclass(slots=True, frozen=True)
class UintKeyValue:
    key: str
    value: int  # Representing uint256 as int

@dataclass(slots=True, frozen=True)
class UintArrayKeyValue:
    key: str
    value: List[int]  # List of uint256 as int

@dataclass(slots=True, frozen=True)
class IntKeyValue:
    key: str
    value: int  # Representing int256 as int

@dataclass(slots=True, frozen=True)
class IntArrayKeyValue:
    key: str
    value: List[int]  # List of int256 as int

@dataclass(slots=True, frozen=True)
class BoolKeyValue:
    key: str
    value: bool

@dataclass(slots=True, frozen=True)
class BoolArrayKeyValue:
    key: str
    value: List[bool]

@dataclass(slots=True, frozen=True)
class Bytes32KeyValue:
    key: str
    value: bytes  # Representing bytes32

@dataclass(slots=True, frozen=True)
class Bytes32ArrayKeyValue:
    key: str
    value: List[bytes]  # List of bytes32

@dataclass(slots=True, frozen=True)
class BytesKeyValue:
    key: str
    value: bytes

@dataclass(slots=True, frozen=True)
class BytesArrayKeyValue:
    key: str
    value: List[bytes]

@dataclass(slots=True, frozen=True)
class StringKeyValue:
    key: str
    value: str

@dataclass(slots=True, frozen=True)
class StringArrayKeyValue:
    key: str
    value: List[str]

@dataclass(slots=True, frozen=True)
class AddressItems:
    items: List[AddressKeyValue]
    arrayItems: List[AddressArrayKeyValue]

@dataclass(slots=True, frozen=True)
class UintItems:
    items: List[UintKeyValue]
    arrayItems: List[UintArrayKeyValue]

@dataclass(slots=True, frozen=True)
class IntItems:
    items: List[IntKeyValue]
    arrayItems: List[IntArrayKeyValue]

@dataclass(slots=True, frozen=True)
class BoolItems:
    items: List[BoolKeyValue]
    arrayItems: List[BoolArrayKeyValue]

@dataclass(slots=True, frozen=True)
class Bytes32Items:
    items: List[Bytes32KeyValue]
    arrayItems: List[Bytes32ArrayKeyValue]

@dataclass(slots=True, frozen=True)
class BytesItems:
    items: List[BytesKeyValue]
    arrayItems: List[BytesArrayKeyValue]

@dataclass(slots=True, frozen=True)
class StringItems:
    items: List[StringKeyValue]
    arrayItems: List[StringArrayKeyValue]

@dataclass(slots=True, frozen=True)
class EventLogData:
    addressItems: AddressItems
    uintItems: UintItems
    intItems: IntItems
    boolItems: BoolItems
    bytes32Items: Bytes32Items
    bytesItems: BytesItems
    stringItems: StringItems



def parse_data(

) -> EventLogData:
    pass