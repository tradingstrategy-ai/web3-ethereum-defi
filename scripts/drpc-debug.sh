#!/bin/bash

set -e
set -u


curl -X POST -H "Content-Type: application/json" \
  --data '{
    "jsonrpc": "2.0",
    "method": "eth_call",
    "params": [
      {
        "to": "0xbEef047a543E45807105E51A8BBEFCc5950fcfBa",
        "data": "0x01e1d114"
      },
      "0x15296e6"
    ],
    "id": 1
  }' \
  $JSON_RPC_ETHEREUM