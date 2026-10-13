"""Finite trusted capture budgets; legacy limits stay the default."""
import math
from ..domain.contract import fields

DEFAULT = {"file_bytes":8*1024*1024,"aggregate_bytes":64*1024*1024,"entries":10000,"seconds":10}
CEILINGS = {"file_bytes":1024*1024*1024,"aggregate_bytes":4*1024*1024*1024,"entries":1000000,"seconds":300}

def validate_budget(value):
    fields(value,DEFAULT)
    for name,ceiling in CEILINGS.items():
        types=(int,float) if name=="seconds" else (int,)
        if type(value[name]) not in types or not math.isfinite(value[name]) or not 0<value[name]<=ceiling:
            raise RuntimeError("invalid_capture_budget")
    if value["file_bytes"]>value["aggregate_bytes"]:
        raise RuntimeError("invalid_capture_budget")
    return value
