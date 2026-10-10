"""Submitted Equation 3.1/A4 with an explicit prediction adapter.

No fitting or selection happens here. FixtureSettings keeps the teaching adapter;
ScientificSettings supplies frozen calendar coefficients at the scheduled hour.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class FixtureSettings:
    kind: str
    intercept: float
    reference_weight: float
    centre: float
    scale: float
    weight: float
    cutoff: float

    def prediction_at(self, timestamp, reference):
        return self.intercept + (self.reference_weight * reference if self.kind == "P" else 0)

    def validate(self):
        if self.kind not in {"S", "P"}:
            raise ValueError("Fixture prediction kind must be S or P")
        numbers = (self.intercept, self.reference_weight, self.centre,
                   self.scale, self.weight, self.cutoff)
        if not all(math.isfinite(x) for x in numbers):
            raise ValueError("Settings must be finite")
        if self.scale <= 0 or self.cutoff <= 0 or self.weight not in {0.02, 0.05, 0.10, 0.20}:
            raise ValueError("Positive scale/cutoff and a submitted lambda are required")
        if self.kind == "S" and self.reference_weight != 0:
            raise ValueError("S cannot use reference input")


class EWMA:
    """One fresh instance per run and variable; missing input is not zero."""

    def __init__(self, settings):
        settings.validate()
        self.settings = settings
        self.z = 0.0
        self.count = 0
        self.gap = 0
        self.previous_valid_id = None

    def step(self, state_id, y=None, reference=None, unavailable_reason=None, *, timestamp=None):
        p = self.settings
        previous_id = self.previous_valid_id
        previous_z = self.z
        reason = unavailable_reason
        if reason is None and (y is None or not math.isfinite(y)):
            reason = "target_unavailable"
        if reason is None and p.kind == "P" and (reference is None or not math.isfinite(reference)):
            reason = "reference_unavailable"
        prediction = residual = standardised = None
        if reason is not None:
            self.gap += 1
            action = "hold" if self.gap <= 6 else "remain_reset"
            if self.gap >= 7:
                self.z, self.count, self.previous_valid_id = 0.0, 0, None
                if self.gap == 7:
                    action = "reset"
            execution, flag = "unevaluated", None
        else:
            prediction = p.prediction_at(timestamp, reference)
            residual = y - prediction
            standardised = (residual - p.centre) / p.scale
            new_z = p.weight * standardised + (1 - p.weight) * self.z
            if not all(math.isfinite(x) for x in (prediction, residual, standardised, new_z)):
                raise ArithmeticError("Nonfinite EWMA calculation; run must fail")
            self.z = new_z
            self.count += 1
            self.gap = 0
            self.previous_valid_id = state_id
            action = "update"
            execution = "evaluated" if self.count >= 228 else "unevaluated"
            flag = abs(self.z) > p.cutoff if execution == "evaluated" else None
            reason = "strict_crossing" if flag else ("within_cutoff" if flag is False else "warm_up")
        return {"state_id": state_id, "previous_valid_state_id": previous_id,
                "previous_z": previous_z, "z": self.z, "valid_updates": self.count,
                "unavailable_hours": self.gap, "ready": execution == "evaluated",
                "action": action, "model_prediction": prediction, "residual": residual,
                "standardised_residual": standardised, "execution": execution,
                "prediction": flag, "reason": reason}
