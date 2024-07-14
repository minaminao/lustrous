import hashlib
import json
import os
import random
import time
from dataclasses import dataclass
from typing import Callable, NoReturn
from uuid import UUID

import requests
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_sandbox.auth import get_shared_secret
from web3 import Web3
from web3.exceptions import TransactionNotFound
from web3.types import TxParams, TxReceipt, Wei

HTTP_PORT = os.getenv("HTTP_PORT", "8545")
PUBLIC_IP = os.getenv("PUBLIC_IP", "127.0.0.1")

ENV = os.getenv("ENV", "production")
FLAG = os.getenv("FLAG", "FLAG{placeholder}")
FUNC_SIG_IS_SOLVED = os.getenv("FUNC_SIG_IS_SOLVED", "isSolved()")

Account.enable_unaudited_hdwallet_features()


def check_uuid(uuid: str) -> str | None:
    try:
        UUID(uuid)
        return uuid
    except (TypeError, ValueError):
        return None


@dataclass
class Action:
    name: str
    handler: Callable[[], int]


def send_transaction(
    web3: Web3, tx: TxParams, ignore_status: bool = False
) -> TxReceipt | None:
    if "gas" not in tx:
        tx["gas"] = 10_000_000

    if "gasPrice" not in tx:
        tx["gasPrice"] = Wei(0)

    txhash = web3.eth.send_transaction(tx)

    while True:
        try:
            rcpt = web3.eth.get_transaction_receipt(txhash)
            break
        except TransactionNotFound:
            time.sleep(0.1)

    if (not ignore_status) and rcpt["status"] != 1:
        raise Exception("failed to send transaction")

    return rcpt


def new_launch_instance_action(
    do_deploy: Callable[[Web3, str, str], str],
) -> Action:
    def action() -> int:
        pow_request(24 if ENV == "production" else 16)

        print("deploying your private blockchain...")

        data = requests.post(
            f"http://127.0.0.1:{HTTP_PORT}/new",
            headers={
                "Authorization": f"Bearer {get_shared_secret()}",
                "Content-Type": "application/json",
            },
        ).json()

        if not data["ok"]:
            print(data["message"])
            return 1

        uuid = data["uuid"]
        mnemonic = data["mnemonic"]
        mnemonic_user = data["mnemonic_user"]

        deployer_account: LocalAccount = Account.from_mnemonic(
            mnemonic, account_path="m/44'/60'/0'/0/0"
        )
        player_account: LocalAccount = Account.from_mnemonic(
            mnemonic_user, account_path="m/44'/60'/0'/0/0"
        )

        web3 = Web3(
            Web3.HTTPProvider(
                f"http://127.0.0.1:{HTTP_PORT}/{uuid}",
                request_kwargs={
                    "headers": {
                        "Authorization": f"Bearer {get_shared_secret()}",
                        "Content-Type": "application/json",
                    },
                },
            )
        )

        setup_addr = do_deploy(web3, deployer_account.address, player_account.address)

        with open(f"/tmp/{uuid}", "w") as f:
            f.write(
                json.dumps(
                    {
                        "uuid": uuid,
                        "mnemonic": mnemonic,
                        "address": setup_addr,
                    }
                )
            )

        print()
        print("your private blockchain has been deployed")
        print("it will automatically terminate in 10 minutes")
        print("here's some useful information")
        print(f"uuid:               {uuid}")
        print(f"rpc endpoint:       http://{PUBLIC_IP}:{HTTP_PORT}/{uuid}")
        print(f"private key:        {player_account.key.hex()}")
        print(f"your address:       {player_account.address}")
        print(f"challenge contract: {setup_addr}")
        return 0

    return Action(name="launch new instance", handler=action)


def new_battle_action() -> Action:
    def action() -> int:
        pow_request(20 if ENV == "production" else 16)

        try:
            uuid = check_uuid(input("uuid please: "))
            if not uuid:
                print("invalid uuid!")
                return 1
        except Exception as e:
            print(f"Error with UUID: {e}")
            return 1

        data = requests.post(
            f"http://127.0.0.1:{HTTP_PORT}/battle",
            headers={
                "Authorization": f"Bearer {get_shared_secret()}",
                "Content-Type": "application/json",
            },
            data=json.dumps(
                {
                    "uuid": uuid,
                }
            ),
        ).json()

        if not data["ok"]:
            print("Error:", data["message"])
            return 1

        print("tx status:", data["status"])
        print("tx hash:", data["tx_hash"])
        return 0

    return Action(name="send battle tx (with uniformly random arg)", handler=action)


def new_kill_instance_action() -> Action:
    def action() -> int:
        try:
            uuid = check_uuid(input("uuid please: "))
            if not uuid:
                print("invalid uuid!")
                return 1
        except Exception as e:
            print(f"Error with UUID: {e}")
            return 1

        data = requests.post(
            f"http://127.0.0.1:{HTTP_PORT}/kill",
            headers={
                "Authorization": f"Bearer {get_shared_secret()}",
                "Content-Type": "application/json",
            },
            data=json.dumps(
                {
                    "uuid": uuid,
                }
            ),
        ).json()

        print(data["message"])
        return 0

    return Action(name="kill instance", handler=action)


def is_solved_checker(web3: Web3, addr: str) -> bool:
    result = web3.eth.call(
        {
            "to": addr,
            "data": web3.keccak(text=FUNC_SIG_IS_SOLVED)[:4],
        }
    )
    return int(result.hex(), 16) == 1


def new_get_flag_action(
    checker: Callable[[Web3, str], bool] = is_solved_checker,
) -> Action:
    def action() -> int:
        try:
            uuid = check_uuid(input("uuid please: "))
            if not uuid:
                print("invalid uuid!")
                return 1
        except Exception as e:
            print(f"Error with UUID: {e}")
            return 1

        try:
            with open(f"/tmp/{uuid}", "r") as f:
                data = json.loads(f.read())
        except Exception:
            print("bad uuid")
            return 1

        web3 = Web3(Web3.HTTPProvider(f"http://127.0.0.1:{HTTP_PORT}/{data['uuid']}"))

        try:
            if not checker(web3, data["address"]):
                print("are you sure you solved it?")
                return 1
        except Exception as e:
            print(f"Error with checker: {e}")
            return 1

        print()
        print("Congratulations! You have solved it! Here's the flag: ")
        print(FLAG)
        return 0

    return Action(
        name=f"get flag (if {FUNC_SIG_IS_SOLVED} is true)",
        handler=action,
    )


def pow_request(bits: int = 24) -> None:
    SOLVE_POW_PY_URL = "https://minaminao.github.io/tools/solve-pow.py"
    preimage_prefix = hex(random.randint(0, 1 << 64))[2:]

    print()
    print("== PoW ==")
    print(
        f'  sha256("{preimage_prefix}" + YOUR_INPUT) must start with {bits} zeros in binary representation'
    )
    print("  please run the following command to solve it:")
    print(f"    python3 <(curl -sSL {SOLVE_POW_PY_URL}) {preimage_prefix} {bits}")
    print()
    your_input = input("  YOUR_INPUT = ")
    print()
    if len(your_input) > 0x100:
        print("  your_input must be less than 256 bytes")
        exit(1)

    digest = hashlib.sha256((preimage_prefix + your_input).encode()).digest()
    digest_int = int.from_bytes(digest, "big")
    print(f'  sha256("{preimage_prefix + your_input}") = {digest.hex()}')
    if digest_int < (1 << (256 - bits)):
        print("  correct")
    else:
        print("  incorrect")
        exit(1)
    print("== END POW ==")


def run_launcher(actions: list[Action]) -> NoReturn:
    for i, action in enumerate(actions):
        print(f"{i+1} - {action.name}")

    action_id = int(input("action? ")) - 1
    if action_id < 0 or action_id >= len(actions):
        print("you can't")
        exit(1)

    exit(actions[action_id].handler())
