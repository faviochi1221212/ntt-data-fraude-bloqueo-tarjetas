"""Backend transaccional simulado (mock) para el MVP.

No hay integración real con el core bancario: los estados se generan al azar con pesos fijos.
`force_result` permite fijar el resultado para tests deterministas y pruebas dirigidas.
"""

import random
from enum import Enum
from typing import Optional


class BlockStatus(str, Enum):
    BLOCK_REQUESTED = "BLOCK_REQUESTED"
    BLOCKED = "BLOCKED"
    BLOCK_PENDING = "BLOCK_PENDING"
    BLOCK_FAILED = "BLOCK_FAILED"
    ALREADY_BLOCKED = "ALREADY_BLOCKED"


class TransactionStatus(str, Enum):
    SETTLED = "settled"
    PENDING = "pending"


# BLOCK_REQUESTED tiene peso 0: con los pesos definidos no sale al azar, solo con force_result.
BLOCK_STATUS_WEIGHTS = {
    BlockStatus.BLOCKED: 70,
    BlockStatus.BLOCK_PENDING: 10,
    BlockStatus.BLOCK_FAILED: 10,
    BlockStatus.ALREADY_BLOCKED: 10,
    BlockStatus.BLOCK_REQUESTED: 0,
}
TRANSACTION_STATUS_WEIGHTS = {TransactionStatus.SETTLED: 80, TransactionStatus.PENDING: 20}


def _choose(weights: dict, rng: Optional[random.Random]):
    options = [k for k, w in weights.items() if w > 0]
    return (rng or random).choices(options, weights=[weights[k] for k in options], k=1)[0]


def simulate_block_request(
    tarjeta_id: str, force_result: Optional[str] = None, rng: Optional[random.Random] = None
) -> BlockStatus:
    """Simula la solicitud de bloqueo de `tarjeta_id` al backend."""
    if force_result is not None:
        return BlockStatus(force_result)
    return _choose(BLOCK_STATUS_WEIGHTS, rng)


def simulate_transaction_status(
    transaccion_id: str, force_result: Optional[str] = None, rng: Optional[random.Random] = None
) -> TransactionStatus:
    """Simula la consulta del estado de liquidación de `transaccion_id`."""
    if force_result is not None:
        return TransactionStatus(force_result)
    return _choose(TRANSACTION_STATUS_WEIGHTS, rng)
