"""Cutoff-safe research forecasting and exact, policy-bounded forecast consumption."""
from .contracts import (DemandContractError, normalize_dimensions, select_training_history,
                        validate_training_history)
from .metrics import forecast_metrics
from .netting import net_forecasts
from .forecasting import forecast_history, rolling_backtest, reconcile_bottom_up
from .synthetic import generate_research_history, freeze_research_inputs, verify_research_freeze
from .mintrace import reconcile_mintrace

__all__ = ["DemandContractError", "normalize_dimensions", "select_training_history",
           "validate_training_history", "forecast_metrics", "net_forecasts",
           "forecast_history", "rolling_backtest", "reconcile_bottom_up",
           "generate_research_history", "freeze_research_inputs", "verify_research_freeze", "reconcile_mintrace"]
